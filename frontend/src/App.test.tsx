import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { health, makeEvent, makeResult, makeTask } from './test/fixtures';
import type { Task, TaskEvent } from './types';

class MockEventSource extends EventTarget {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: MockEventSource[] = [];
  readyState = MockEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn(() => { this.readyState = MockEventSource.CLOSED; });

  constructor(public readonly url: string) {
    super();
    MockEventSource.instances.push(this);
  }

  open() { this.readyState = MockEventSource.OPEN; this.onopen?.(); }
  fail() { this.readyState = MockEventSource.CONNECTING; this.onerror?.(); }
  progress(event: TaskEvent) {
    this.dispatchEvent(new MessageEvent('progress', { data: JSON.stringify(event), lastEventId: String(event.id) }));
  }
}

const fetchMock = vi.fn<typeof fetch>();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
let tasks: Task[];
let healthFails: boolean;
let browserAvailable: boolean;
let planFails: boolean;
let resultsFail: boolean;

async function route(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const path = new URL(String(input), 'http://localhost').pathname;
  if (path === '/api/health') {
    if (healthFails) throw new TypeError('Network is offline');
    return json({ ...health, browser_available: browserAvailable });
  }
  if (path === '/api/tasks') return json(tasks);
  if (path === '/api/tasks/plan') {
    if (planFails) throw new TypeError('Network is offline');
    const input = JSON.parse(String(init?.body)) as Partial<Task>;
    const task = makeTask(input);
    tasks = [task, ...tasks];
    return json(task);
  }
  const match = path.match(/^\/api\/tasks\/([^/]+)(?:\/(execute|cancel|results))?$/);
  if (match) {
    const task = tasks.find((item) => item.id === match[1]);
    if (!task) return json({ error: { code: 'NOT_FOUND', message: 'Task not found.' } }, 404);
    if (match[2] === 'execute') {
      const running: Task = { ...task, status: 'RUNNING', started_at: '2026-10-19T10:00:01Z', current_action: 'Opening catalog.' };
      tasks = tasks.map((item) => item.id === task.id ? running : item);
      return json(running);
    }
    if (match[2] === 'cancel') {
      const cancelled: Task = { ...task, status: 'CANCELLED', finished_at: '2026-10-19T10:00:01Z', current_action: 'Task cancelled.' };
      tasks = tasks.map((item) => item.id === task.id ? cancelled : item);
      return json(cancelled);
    }
    if (match[2] === 'results') return resultsFail
      ? json({ error: { code: 'REPORT_UNAVAILABLE', message: 'Results temporarily unavailable.' } }, 503)
      : json(task.result ?? makeResult());
    return json(task);
  }
  throw new Error(`Unexpected request: ${init?.method || 'GET'} ${path}`);
}

beforeEach(() => {
  tasks = [];
  healthFails = false;
  browserAvailable = true;
  planFails = false;
  resultsFail = false;
  MockEventSource.instances = [];
  fetchMock.mockReset().mockImplementation(route);
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('EventSource', MockEventSource);
});

afterEach(() => { vi.useRealTimers(); });

async function click(element: HTMLElement) {
  // Let mocked HTTP's real Response.json() and the resulting React effects settle.
  await act(async () => { fireEvent.click(element); });
}

async function createDemoPlan() {
  fireEvent.click(screen.getByRole('button', { name: /Demo catalog/ }));
  await click(screen.getByRole('button', { name: /Generate a plan/ }));
  const approve = await screen.findByRole('button', { name: /Approve & execute/ });
  expect(approve).toBeVisible();
  expect(approve).toBeEnabled();
}

function updateTask(event: TaskEvent, overrides: Partial<Task> = {}) {
  tasks = tasks.map((task) => task.id === event.task_id ? { ...task, status: event.status, progress: event.progress, current_action: event.message, logs: [...task.logs, event], ...overrides } : task);
}

describe('workflow dashboard', () => {
  it('plans without executing, requires approval, tracks completed steps, and displays downloadable results', async () => {
    render(<App />);
    expect(await screen.findByText('API connected')).toBeInTheDocument();
    await createDemoPlan();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/execute'))).toBe(false);
    expect(screen.getByText('Your approval is the next step.')).toBeInTheDocument();
    expect(screen.getByText('Page contains products.')).toBeInTheDocument();

    const plannedSource = MockEventSource.instances.at(-1)!;
    await click(screen.getByRole('button', { name: /Approve & execute/ }));
    expect(fetchMock).toHaveBeenCalledWith('/api/tasks/task-001/execute', expect.objectContaining({ body: '{"approved":true}' }));
    await screen.findByRole('button', { name: 'Cancel run' });
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(2));
    const source = MockEventSource.instances.at(-1)!;
    expect(plannedSource.close).toHaveBeenCalled();

    const step = makeEvent({ id: 2, type: 'step', status: 'RUNNING', progress: 1, message: 'Catalog loaded successfully.' });
    updateTask(step);
    act(() => { source.open(); source.progress(step); source.progress(step); });
    expect(screen.getByRole('progressbar', { name: 'Completed steps' })).toHaveAttribute('aria-valuenow', '1');
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '1 of 3 steps completed');
    expect(within(screen.getByRole('log')).getAllByText(step.message)).toHaveLength(1);

    const warning = makeEvent({ id: 3, type: 'warning', status: 'RUNNING', progress: 1, message: 'One product has an optional field missing.' });
    updateTask(warning);
    act(() => { source.progress(warning); });
    expect(screen.getByText('Plan & execution warnings')).toBeInTheDocument();

    const done = makeEvent({ id: 4, type: 'state', status: 'SUCCEEDED', progress: 3, message: 'Reports saved.' });
    updateTask(done, { result: makeResult(), finished_at: done.timestamp });
    await act(async () => { source.progress(done); });
    expect(await screen.findByRole('cell', { name: 'Everyday notebook' })).toBeVisible();
    expect(screen.getByRole('link', { name: /CSV/ })).toHaveAttribute('href', '/api/tasks/task-001/reports/csv');
    expect(screen.getByRole('link', { name: /JSON/ })).toHaveAttribute('href', '/api/tasks/task-001/reports/json');
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '3');
    expect(source.close).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith('/api/tasks/task-001/results', expect.anything());
    const history = within(screen.getByRole('region', { name: /Run history/ }));
    expect(history.getByText('ID: task-001')).toBeVisible();
    expect(history.getByText('1 record extracted')).toBeVisible();
    expect(history.getByRole('button', { name: 'Open task task-001' })).toHaveTextContent('View results');
    fireEvent.click(screen.getByRole('tab', { name: /Execution log/ }));
    expect(within(screen.getByRole('log')).getByText('Reports saved.')).toBeInTheDocument();
    await click(history.getByRole('button', { name: 'Open task task-001' }));
    expect(await screen.findByRole('cell', { name: 'Everyday notebook' })).toBeVisible();
  });

  it('supports the public example, task types, optional fields, and local validation', async () => {
    render(<App />);
    await click(screen.getByRole('button', { name: /Generate a plan/ }));
    expect(screen.getByRole('alert')).toHaveTextContent('10–2,000 characters');
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/plan'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: /Public books/ }));
    expect(screen.getByLabelText('Target URL')).toHaveValue('https://books.toscrape.com/');
    fireEvent.change(screen.getByLabelText('Task type'), { target: { value: 'report' } });
    for (const field of ['title', 'price']) fireEvent.click(screen.getByRole('checkbox', { name: field }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'description' }));
    await click(screen.getByRole('button', { name: /Generate a plan/ }));
    await screen.findByRole('button', { name: /Approve & execute/ });
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/plan'))!;
    expect(JSON.parse(String(init?.body))).toMatchObject({ target_url: 'https://books.toscrape.com/', task_type: 'report', fields: ['description'] });
  });

  it('switches to inspection fields and restores extraction/report choices without leaking stale fields', async () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /Demo catalog/ }));
    fireEvent.change(screen.getByLabelText('Task type'), { target: { value: 'inspection' } });
    fireEvent.change(screen.getByRole('textbox', { name: /Your instruction/ }), { target: { value: 'Inspect the page title and description.' } });
    expect(screen.getByRole('checkbox', { name: 'title' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'description' })).toBeChecked();
    expect(screen.queryByRole('checkbox', { name: 'price' })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'category' })).not.toBeInTheDocument();

    await click(screen.getByRole('button', { name: /Generate a plan/ }));
    await screen.findByRole('button', { name: /Approve & execute/ });
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/plan'))!;
    expect(JSON.parse(String(init?.body))).toMatchObject({ task_type: 'inspection', fields: ['title', 'description'] });

    for (const taskType of ['extraction', 'report']) {
      fireEvent.change(screen.getByLabelText('Task type'), { target: { value: taskType } });
      expect(screen.getByRole('checkbox', { name: 'title' })).toBeChecked();
      expect(screen.getByRole('checkbox', { name: 'price' })).toBeChecked();
      expect(screen.getByRole('checkbox', { name: 'category' })).not.toBeChecked();
      expect(screen.getByRole('checkbox', { name: 'description' })).not.toBeChecked();
    }
    fireEvent.click(screen.getByRole('button', { name: /Public books/ }));
    expect(screen.getByLabelText('Task type')).toHaveValue('extraction');
    expect(screen.getByRole('checkbox', { name: 'category' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'title' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'price' })).toBeChecked();
  });

  it('cancels a reviewed plan without executing or requesting results', async () => {
    render(<App />);
    await createDemoPlan();
    await click(screen.getByRole('button', { name: 'Cancel plan' }));
    await waitFor(() => expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0'));
    expect(screen.queryByRole('button', { name: /Approve & execute/ })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/execute'))).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/results'))).toBe(false);
    expect(MockEventSource.instances.every((source) => source.close.mock.calls.length > 0)).toBe(true);
  });

  it('recovers via HTTP on SSE error while retaining native EventSource reconnect', async () => {
    tasks = [makeTask({ status: 'RUNNING' })];
    render(<App />);
    await click(await screen.findByRole('button', { name: 'Open task task-001' }));
    await screen.findByRole('button', { name: 'Cancel run' });
    const source = MockEventSource.instances[0];
    tasks = tasks.map((task) => ({ ...task, progress: 2, current_action: 'Validating records.' }));
    await act(async () => { source.fail(); });
    await waitFor(() => expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '2'));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(source.close).not.toHaveBeenCalled();
    act(() => { source.open(); });
    expect(screen.getByText('Live stream')).toBeInTheDocument();

    // The terminal event was lost; the server closed the stream after completion.
    tasks = tasks.map((task) => ({ ...task, status: 'SUCCEEDED', progress: 3, result: makeResult() }));
    await act(async () => { source.fail(); });
    expect(await screen.findByRole('cell', { name: 'Everyday notebook' })).toBeVisible();
    expect(source.close).toHaveBeenCalled();
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it('cleans up streams and ignores stale HTTP and SSE updates when history selection changes', async () => {
    const first = makeTask({ status: 'RUNNING' });
    const second = makeTask({ id: 'task-002', instruction: 'Inspect the second catalog page', logs: [makeEvent({ task_id: 'task-002', message: 'Second task saved log.' })] });
    tasks = [first, second];
    const { unmount } = render(<App />);
    await click(await screen.findByRole('button', { name: 'Open task task-001' }));
    await screen.findByRole('button', { name: 'Cancel run' });
    const firstSource = MockEventSource.instances[0];
    let resolveStale: (response: Response) => void = () => { throw new Error('Stale request not started'); };
    fetchMock.mockImplementation((input, init) => String(input) === '/api/tasks/task-001'
      ? new Promise<Response>((resolve) => { resolveStale = resolve; })
      : route(input, init));
    act(() => { firstSource.fail(); });
    await click(screen.getByRole('button', { name: 'Open task task-002' }));
    await screen.findByRole('button', { name: /Approve & execute/ });
    expect(firstSource.close).toHaveBeenCalled();
    await act(async () => {
      firstSource.progress(makeEvent({ id: 99, status: 'FAILED', type: 'error', message: 'Stale task failure.' }));
      resolveStale(json({ ...first, status: 'FAILED', error: 'Stale HTTP failure.' }));
    });
    expect(screen.getByText('Second task saved log.')).toBeInTheDocument();
    expect(screen.queryByText('Stale task failure.')).not.toBeInTheDocument();
    expect(screen.queryByText('Stale HTTP failure.')).not.toBeInTheDocument();
    unmount();
    expect(MockEventSource.instances.every((source) => source.close.mock.calls.length > 0)).toBe(true);
  });

  it('loads existing task logs and retries a failed results request', async () => {
    tasks = [makeTask({ status: 'SUCCEEDED', progress: 3, logs: [makeEvent({ status: 'SUCCEEDED', message: 'Saved history event.' })] })];
    resultsFail = true;
    render(<App />);
    await click(await screen.findByRole('button', { name: 'Open task task-001' }));
    const retry = await screen.findByRole('button', { name: 'Retry results' });
    expect(retry).toBeVisible();
    expect(screen.getByRole('tab', { name: 'Results' })).toHaveAttribute('aria-selected', 'true');
    expect(within(screen.getByRole('tabpanel', { name: 'Results' })).getByRole('alert')).toHaveTextContent('Results temporarily unavailable.');
    resultsFail = false;
    await click(retry);
    expect(await screen.findByRole('cell', { name: 'Everyday notebook' })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: /Execution log/ }));
    expect(screen.getByText('Saved history event.')).toBeInTheDocument();
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it('surfaces unexpected planning network errors without inventing a task', async () => {
    planFails = true;
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /Demo catalog/ }));
    await click(screen.getByRole('button', { name: /Generate a plan/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the API');
    expect(screen.queryByRole('button', { name: /Approve & execute/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Generate a plan/ })).toBeEnabled();
  });

  it('polls real health, reflects browser availability, and recovers from offline status', async () => {
    vi.useFakeTimers();
    browserAvailable = false;
    render(<App />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText('API connected')).toBeInTheDocument();
    expect(screen.getByText('Browser unavailable')).toBeInTheDocument();
    healthFails = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(screen.getByText('API offline')).toBeInTheDocument();
    expect(screen.getByText('Browser status unknown')).toBeInTheDocument();
    healthFails = false;
    browserAvailable = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(screen.getByText('API connected')).toBeInTheDocument();
    expect(screen.getByText('Browser ready')).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url) === '/api/health')).toHaveLength(3);
  });
});

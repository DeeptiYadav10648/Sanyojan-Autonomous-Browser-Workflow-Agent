import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient } from './api';
import { health, makeResult, makeTask } from './test/fixtures';

const fetchMock = vi.fn<typeof fetch>();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

describe('API client', () => {
  it('uses the same-origin API by default and normalizes configured bases for reports and SSE', async () => {
    const api = createApiClient();
    fetchMock.mockResolvedValue(json(health));
    await expect(api.health()).resolves.toEqual(health);
    expect(fetchMock).toHaveBeenCalledWith('/api/health', expect.objectContaining({ headers: { Accept: 'application/json' } }));

    const remote = createApiClient(' https://api.example.test/prefix/// ');
    expect(remote.url('/api/tasks/a/reports/csv')).toBe('https://api.example.test/prefix/api/tasks/a/reports/csv');
    expect(remote.eventsUrl('task/a b')).toBe('https://api.example.test/prefix/api/tasks/task%2Fa%20b/events');
  });

  it('posts a plan with selected fields and executes only with explicit approval', async () => {
    const api = createApiClient();
    fetchMock.mockImplementation(async () => json(makeTask()));
    const input = { instruction: 'Inspect the product catalog', target_url: 'https://example.test', task_type: 'inspection' as const, fields: ['title', 'description'] };
    const controller = new AbortController();
    await api.plan(input, controller.signal);
    expect(fetchMock).toHaveBeenLastCalledWith('/api/tasks/plan', expect.objectContaining({ method: 'POST', body: JSON.stringify(input), signal: controller.signal, headers: { Accept: 'application/json', 'Content-Type': 'application/json' } }));
    await api.execute('task-001');
    expect(fetchMock).toHaveBeenLastCalledWith('/api/tasks/task-001/execute', expect.objectContaining({ method: 'POST', body: '{"approved":true}' }));
    await api.cancel('task-001');
    const [, cancelInit] = fetchMock.mock.calls.at(-1)!;
    expect(cancelInit?.method).toBe('POST');
    expect(cancelInit?.body).toBeUndefined();
  });

  it('fetches saved history, a task including its logs, and results', async () => {
    const api = createApiClient('/backend');
    fetchMock.mockResolvedValueOnce(json([makeTask()])).mockResolvedValueOnce(json(makeTask())).mockResolvedValueOnce(json(makeResult()));
    await expect(api.tasks()).resolves.toHaveLength(1);
    await expect(api.task('task-001')).resolves.toMatchObject({ logs: expect.any(Array) });
    await expect(api.results('task-001')).resolves.toMatchObject({ extracted_count: 1 });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/backend/api/tasks', '/backend/api/tasks/task-001', '/backend/api/tasks/task-001/results']);
  });

  it('preserves backend error messages, codes, status, and details', async () => {
    fetchMock.mockResolvedValue(json({ error: { code: 'INVALID_INSTRUCTION', message: 'Instruction is too short.', details: { minimum: 10 } } }, 422));
    await expect(createApiClient().plan({ instruction: 'short', target_url: 'https://example.test', task_type: 'extraction' })).rejects.toMatchObject({ name: 'ApiError', message: 'Instruction is too short.', code: 'INVALID_INSTRUCTION', status: 422, details: { minimum: 10 } });
  });

  it.each([200, 503])('reports non-JSON responses at HTTP %s', async (status) => {
    fetchMock.mockResolvedValue(new Response('<html>upstream unavailable</html>', { status }));
    await expect(createApiClient().tasks()).rejects.toMatchObject({ code: 'INVALID_RESPONSE', status });
  });

  it('handles network failures and preserves aborts for race-safe cleanup', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(createApiClient().tasks()).rejects.toMatchObject({ name: 'ApiError', code: 'NETWORK_ERROR', status: 0 });
    const abort = new DOMException('Request aborted', 'AbortError');
    fetchMock.mockRejectedValueOnce(abort);
    await expect(createApiClient().tasks()).rejects.toBe(abort);
  });

  it('preserves aborts while reading the HTTP response body', async () => {
    const abort = new DOMException('Body read aborted', 'AbortError');
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream({
      start(controller) { controller.error(abort); },
    })));
    await expect(createApiClient().tasks()).rejects.toBe(abort);
  });

  it('recognizes cross-realm aborts without treating other thrown values as cancellation', async () => {
    const abort = { name: 'AbortError', message: 'Cross-realm cancellation' };
    fetchMock.mockRejectedValueOnce(abort);
    await expect(createApiClient().tasks()).rejects.toBe(abort);

    for (const failure of [null, undefined, 'AbortError', { name: 'TypeError' }]) {
      fetchMock.mockRejectedValueOnce(failure);
      await expect(createApiClient().tasks()).rejects.toBeInstanceOf(ApiError);
    }
  });
});

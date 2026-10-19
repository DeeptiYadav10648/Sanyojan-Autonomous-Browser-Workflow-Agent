import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TaskWorkspace } from './TaskWorkspace';
import { makeTask, makeResult } from '../test/fixtures';
import type { Task, TaskResult } from '../types';

function renderTask(task: Task, result: TaskResult) {
  return render(<TaskWorkspace selectedId={task.id} task={task} loading={false} taskError={null}
    result={result} resultError={null} resultsLoading={false} connection="closed" operation={null}
    browserUnavailable={false} onExecute={vi.fn()} onCancel={vi.fn()} onRefresh={vi.fn()} />);
}

describe('failed task results', () => {
  it('shows failed steps without inventing report download links or crashing', () => {
    const result = makeResult({ records: [], extracted_count: 0, valid_count: 0, reports: {}, failed_steps: [3] });
    const task = makeTask({ status: 'FAILED', error: 'Unsupported static markup', result });
    render(<TaskWorkspace selectedId={task.id} task={task} loading={false} taskError={null}
      result={result} resultError={null} resultsLoading={false} connection="closed" operation={null}
      browserUnavailable={false} onExecute={vi.fn()} onCancel={vi.fn()} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole('tab', { name: /Results/ }));
    expect(screen.getByText('Failed steps: 3')).toBeVisible();
    expect(screen.queryByRole('link', { name: /^CSV/ })).not.toBeInTheDocument();
    expect(screen.getByText('Unsupported static markup')).toBeVisible();
  });
});

describe('source provenance', () => {
  it('labels the demo target and record source as a bundled fixture without broken external links', () => {
    const result = makeResult();
    const task = makeTask({ status: 'SUCCEEDED', result });
    renderTask(task, result);

    for (const label of screen.getAllByText('Bundled demo fixture')) expect(label).toBeVisible();
    expect(screen.getAllByText('Bundled demo fixture')).toHaveLength(2);
    expect(screen.getByText(task.target_url, { selector: 'span' })).toBeVisible();
    expect(screen.queryByRole('link', { name: task.target_url })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'View page' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'CSV' })).toHaveAttribute('href', result.reports.csv);
    expect(screen.getAllByRole('link').every((link) => !link.getAttribute('href')?.includes('demo.sanyojan.test'))).toBe(true);
  });

  it('keeps safe public HTTP(S) source links and renders unsafe sources as text', () => {
    const https = 'https://books.toscrape.com/';
    const http = 'http://public.example.test/catalog';
    const record = makeResult().records[0];
    const result = makeResult({
      records: [
        { ...record, source_url: https },
        { ...record, source_url: http },
        { ...record, source_url: 'javascript:alert(1)' },
      ],
      extracted_count: 3,
      valid_count: 3,
    });
    renderTask(makeTask({ status: 'SUCCEEDED', target_url: https, result }), result);

    expect(screen.getByRole('link', { name: https })).toHaveAttribute('href', https);
    expect(screen.getAllByRole('link', { name: 'View page' }).map((link) => link.getAttribute('href'))).toEqual([https, http]);
    expect(screen.getByText('Source unavailable')).toBeVisible();
    expect(screen.queryByText('Bundled demo fixture')).not.toBeInTheDocument();
    expect(screen.getAllByRole('link').every((link) => !link.getAttribute('href')?.startsWith('javascript:'))).toBe(true);
  });
});

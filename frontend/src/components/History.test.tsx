import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { makeResult, makeTask } from '../test/fixtures';
import { History } from './History';

describe('history metadata and actions', () => {
  it('shows full IDs and real record counts, distinguishing zero records from pending results', () => {
    const completed = makeTask({
      id: '83a00138-3f64-4713-90b5-f82170e8e007',
      status: 'SUCCEEDED',
      result: makeResult({ records: [], extracted_count: 0, valid_count: 0 }),
    });
    const planned = makeTask({ id: 'pending-task' });
    const cancelled = makeTask({ id: 'cancelled-task', status: 'CANCELLED' });
    const onSelect = vi.fn();
    render(<History tasks={[completed, planned, cancelled]} loading={false} error={null}
      selectedId={null} onSelect={onSelect} onRefresh={vi.fn().mockResolvedValue(undefined)} />);

    expect(screen.getByText(`ID: ${completed.id}`)).toBeVisible();
    expect(screen.getByText('ID: pending-task')).toBeVisible();
    expect(screen.getByText('0 records extracted')).toBeVisible();
    expect(screen.getByText('No results yet')).toBeVisible();
    expect(screen.getByText('No saved results')).toBeVisible();
    const viewResults = screen.getByRole('button', { name: `Open task ${completed.id}` });
    expect(viewResults).toHaveTextContent('View results');
    expect(screen.getByRole('button', { name: 'Open task pending-task' })).toHaveTextContent('View task');
    fireEvent.click(viewResults);
    expect(onSelect).toHaveBeenCalledWith(completed.id);
  });
});

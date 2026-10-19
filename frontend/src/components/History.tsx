import { isTerminal, type Task } from '../types';
import { isBundledDemoSource } from './SourceReference';
import { StatusBadge } from './TaskWorkspace';

interface Props {
  tasks: Task[];
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRefresh: () => Promise<void>;
}

export function History({ tasks, loading, error, selectedId, onSelect, onRefresh }: Props) {
  return (
    <section className="panel history-panel" id="history" aria-labelledby="history-title">
      <div className="history-heading">
        <div>
          <span className="eyebrow">YOUR WORK, ALL IN ONE PLACE</span>
          <h2 id="history-title">Run history <span className="history-count">{tasks.length}</span></h2>
        </div>
        <button className="button secondary small" type="button" onClick={() => { void onRefresh(); }} disabled={loading}>
          <span className={loading ? 'spin-glyph' : ''} aria-hidden="true">↻</span>
          {loading ? 'Refreshing…' : 'Refresh history'}
        </button>
      </div>
      {error && (
        <div role="alert" className="notice error inset">
          {error}
          <button className="text-button" type="button" onClick={() => { void onRefresh(); }}>Retry history</button>
        </div>
      )}
      {!tasks.length ? (
        <div className="history-empty">
          <span className="history-empty-mark" aria-hidden="true">↺</span>
          <div>
            <h3>{loading ? 'Loading your workflows…' : error ? 'Your history is unavailable.' : 'A fresh start. A world of possibilities.'}</h3>
            <p>{loading ? 'Fetching saved tasks from the API.' : error ? 'Retry loading saved workflows using Refresh history above.' : 'Your planned and completed tasks will appear here. Create your first plan above.'}</p>
          </div>
        </div>
      ) : (
        <div className="table-scroll">
          <table className="history-table">
            <caption className="sr-only">Saved workflow tasks. Select a task to view its plan, logs, and results.</caption>
            <thead>
              <tr>
                <th scope="col">Workflow</th>
                <th scope="col">Type</th>
                <th scope="col">Status</th>
                <th scope="col">Created</th>
                <th scope="col"><span className="sr-only">Open task</span></th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr className={selectedId === task.id ? 'selected-row' : ''} key={task.id}>
                  <td>
                    <button className="history-task" type="button" onClick={() => onSelect(task.id)} aria-pressed={selectedId === task.id}>
                      <span className="history-task-mark" aria-hidden="true">≋</span>
                      <span>
                        <strong title={task.instruction}>{task.instruction}</strong>
                        <span>{isBundledDemoSource(task.target_url) ? 'Bundled demo fixture' : task.target_url}</span>
                      </span>
                    </button>
                    <div className="history-task-metadata">
                      <span className="history-task-id">ID: {task.id}</span>
                      <span className="history-record-count">
                        {task.result
                          ? `${task.result.extracted_count} ${task.result.extracted_count === 1 ? 'record' : 'records'} extracted`
                          : task.status === 'SUCCEEDED' ? 'Record count unavailable'
                            : isTerminal(task.status) ? 'No saved results' : 'No results yet'}
                      </span>
                    </div>
                  </td>
                  <td><span className="type-label">{task.task_type}</span></td>
                  <td><StatusBadge status={task.status} /></td>
                  <td className="history-date">
                    <time dateTime={task.created_at}>
                      {new Date(task.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                      <span>{new Date(task.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                    </time>
                  </td>
                  <td>
                    <button className="button secondary history-view" type="button" onClick={() => onSelect(task.id)} aria-label={`Open task ${task.id}`}>
                      {task.status === 'SUCCEEDED' ? 'View results' : 'View task'} <span aria-hidden="true">↗</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

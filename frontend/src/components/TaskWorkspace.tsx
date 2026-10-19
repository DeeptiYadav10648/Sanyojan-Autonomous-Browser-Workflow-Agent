import { useEffect, useState } from 'react';
import { api } from '../api';
import { isTerminal, statusLabels, type Task, type TaskEvent, type TaskResult } from '../types';
import { SourceReference } from './SourceReference';

export function StatusBadge({ status }: { status: Task['status'] }) {
  return <span className={`status-badge status-${status.toLowerCase()}`}><span className="status-dot" />{statusLabels[status]}</span>;
}

function displayTime(timestamp: string) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function WarningList({ warnings, title = 'Worth a look' }: { warnings: string[]; title?: string }) {
  if (!warnings.length) return null;
  return <div className="notice warning"><strong>{title}</strong><ul>{[...new Set(warnings)].map((warning) => <li key={warning}>{warning}</li>)}</ul></div>;
}

function ExecutionLog({ logs }: { logs: TaskEvent[] }) {
  return (
    <div role="log" aria-label="Execution log" className="execution-log" aria-live="polite" aria-relevant="additions">
      {!logs.length && <div className="small-empty"><span className="empty-log-mark" aria-hidden="true">≋</span><p>No events yet.</p><span>Execution updates will appear here.</span></div>}
      {logs.map((event) => (
        <div className={`log-event log-${event.type}`} key={event.id}>
          <time dateTime={event.timestamp}>{displayTime(event.timestamp)}</time>
          <span className="log-marker" aria-hidden="true" />
          <div><span className="log-type">{event.type}</span><p>{event.message}</p></div>
        </div>
      ))}
    </div>
  );
}

function ResultPreview({ result }: { result: TaskResult }) {
  const fields = [...new Set(result.records.flatMap((record) => Object.keys(record.data)))];
  return (
    <div className="result-content">
      <div className="result-heading"><div><h3>{result.page_title || 'Extracted results'}</h3><p>Your browser run, in structured data.</p></div><div className="download-actions">{result.reports.csv && <a className="button secondary small" href={api.url(result.reports.csv)} download>CSV <span aria-hidden="true">↓</span></a>}{result.reports.json && <a className="button secondary small" href={api.url(result.reports.json)} download>JSON <span aria-hidden="true">↓</span></a>}</div></div>
      <div className="result-stats"><div><strong>{result.extracted_count}</strong><span>records extracted</span></div><div><strong>{result.valid_count}</strong><span>records validated</span></div><div><strong>{result.duration_seconds.toFixed(1)}s</strong><span>run duration</span></div></div>
      <WarningList warnings={result.warnings} title="Result warnings" />
      {result.failed_steps.length > 0 && <div className="notice warning">Failed steps: {result.failed_steps.join(', ')}</div>}
      {result.records.length ? <>
        <div className="table-scroll">
          <table className="results-table">
            <caption className="sr-only">Extracted records preview</caption>
            <thead>
              <tr>
                <th scope="col">#</th>
                {fields.map((field) => <th scope="col" key={field}>{field}</th>)}
                <th scope="col">Validation</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {result.records.slice(0, 20).map((record, index) => (
                <tr key={`${record.source_url}-${index}`}>
                  <td className="row-number">{String(index + 1).padStart(2, '0')}</td>
                  {fields.map((field) => (
                    <td key={field}>
                      {record.data[field] === null || record.data[field] === undefined
                        ? <span className="muted">—</span> : String(record.data[field])}
                    </td>
                  ))}
                  <td>
                    <span className={`validation-badge ${record.valid ? 'valid' : 'invalid'}`}>{record.valid ? 'Valid' : 'Review'}</span>
                    {record.warnings.length > 0 && (
                      <ul className="record-warnings">
                        {record.warnings.map((warning, warningIndex) => <li key={warningIndex}>{warning}</li>)}
                      </ul>
                    )}
                  </td>
                  <td><SourceReference url={record.source_url} extractedAt={record.extracted_at} compact /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="table-note">Showing {Math.min(result.records.length, 20)} of {result.extracted_count} records. Download a report for the full dataset.</p>
      </> : <div className="small-empty"><p>No records were returned.</p><span>Review the execution log and warnings for details.</span></div>}
    </div>
  );
}

interface Props {
  selectedId: string | null;
  task: Task | null;
  loading: boolean;
  taskError: string | null;
  result: TaskResult | null;
  resultError: string | null;
  resultsLoading: boolean;
  connection: 'idle' | 'connecting' | 'live' | 'recovering' | 'closed';
  operation: 'plan' | 'execute' | 'cancel' | null;
  browserUnavailable: boolean;
  onExecute: () => Promise<void>;
  onCancel: () => Promise<void>;
  onRefresh: () => void;
}

export function TaskWorkspace({ selectedId, task, loading, taskError, result, resultError, resultsLoading, connection, operation, browserUnavailable, onExecute, onCancel, onRefresh }: Props) {
  const [tab, setTab] = useState<'activity' | 'results'>('activity');
  useEffect(() => { setTab('activity'); }, [selectedId]);
  useEffect(() => { if (task?.status === 'SUCCEEDED') setTab('results'); }, [task?.id, task?.status]);

  const total = task?.plan?.steps.length ?? 0;
  const completed = task?.progress ?? 0;
  const percent = total > 0 ? Math.max(0, Math.min(100, (completed / total) * 100)) : 0;
  const eventWarnings = task?.logs.filter((event) => event.type === 'warning').map((event) => event.message) ?? [];

  return (
    <section className="panel workspace-panel" aria-labelledby="workspace-title" aria-busy={loading}>
      <div className="panel-heading workspace-panel-heading"><span className="section-number">02</span><div><h2 id="workspace-title">Your execution workspace</h2><p>A clear plan. Every step visible.</p></div>{task && <button className="icon-button" type="button" onClick={onRefresh} aria-label="Refresh selected task" title="Refresh selected task">↻</button>}</div>
      {taskError && <div className="notice error inset" role="alert">{taskError}<button type="button" className="text-button" onClick={onRefresh}>Try again</button></div>}
      {loading && <div className="workspace-loading" role="status"><span className="spinner" /> Loading task…</div>}
      {!selectedId && !loading && <div className="workspace-empty">
        <div className="workflow-illustration" aria-hidden="true"><div className="illustration-orbit" /><div className="illustration-node node-a">01</div><div className="illustration-core"><span className="brand-symbol" /></div><div className="illustration-node node-b">02</div><div className="illustration-node node-c">✓</div><span className="orbit-spark spark-a" /><span className="orbit-spark spark-b" /></div>
        <span className="eyebrow">INTENT → PLAN → ACTION</span><h3>Great workflows start<br />with a little direction.</h3><p>Create a task to see your agent’s plan here.<br />You review it. You decide when it runs.</p><div className="empty-capabilities"><span><span aria-hidden="true">✓</span> Reviewable plans</span><span><span aria-hidden="true">✓</span> Live execution</span><span><span aria-hidden="true">✓</span> Structured results</span></div>
      </div>}
      {task && !loading && <div className="task-content">
        <div className="task-title-row"><span className="eyebrow">{task.task_type} WORKFLOW</span><StatusBadge status={task.status} /></div>
        <h3 className="task-instruction">{task.instruction}</h3>
        <div className="task-meta">
          <SourceReference url={task.target_url} />
          <span className="task-id" title={task.id}>#{task.id.slice(0, 8)}</span>
        </div>
        {task.error && <div className="notice error" role="alert"><strong>Task failed</strong><p>{task.error}</p></div>}
        {task.plan && <>
          <div className="plan-summary"><span className="plan-symbol" aria-hidden="true">≡</span><div><h4>Execution plan <span className="mode-badge">{task.plan.mode}</span></h4><p>{task.plan.summary}</p><div className="plan-fields">{task.plan.fields.map((field) => <span key={field}>{field}</span>)}</div></div></div>
          <ol className="plan-steps">{task.plan.steps.map((step, index) => {
            const done = index < completed;
            const active = task.status === 'RUNNING' && index === completed;
            return <li className={`${done ? 'step-done' : ''} ${active ? 'step-active' : ''}`} key={step.number}><span className="step-number" aria-label={done ? `Step ${step.number} completed` : `Step ${step.number}`}>{done ? '✓' : String(step.number).padStart(2, '0')}</span><div className="step-body"><div className="step-heading"><h4>{step.action}</h4><span className="step-risk">{step.risk} risk · {step.timeout_ms / 1000}s timeout</span></div><p>{step.expected_result}</p><details><summary>Target & validation</summary><dl><dt>Target</dt><dd>{step.target}</dd><dt>Validation</dt><dd>{step.validation_rule}</dd></dl></details></div></li>;
          })}</ol>
          <WarningList warnings={[...task.plan.warnings, ...eventWarnings]} title="Plan & execution warnings" />
        </>}
        {task.status === 'PLANNED' && <div className="approval-box"><div><span className="approval-mark" aria-hidden="true">✓</span><div><strong>Your approval is the next step.</strong><p>Review the plan above before starting the browser.</p></div></div><div className="approval-actions"><button className="button secondary" type="button" disabled={Boolean(operation)} onClick={() => { void onCancel(); }}>{operation === 'cancel' ? 'Cancelling…' : 'Cancel plan'}</button><button className="button primary" type="button" disabled={Boolean(operation) || browserUnavailable} onClick={() => { void onExecute(); }}>{operation === 'execute' ? <><span className="spinner" /> Starting…</> : <>Approve & execute <span aria-hidden="true">→</span></>}</button></div>{browserUnavailable && <p className="browser-unavailable">The API reports that the browser is unavailable. Execution will be enabled when it is ready.</p>}</div>}
        {task.status !== 'PLANNED' && <div className="progress-section"><div className="progress-heading"><strong>{isTerminal(task.status) ? statusLabels[task.status] : 'Execution progress'}</strong><span>{completed} / {total} steps completed</span></div><div className="progress-track" role="progressbar" aria-label="Completed steps" aria-valuemin={0} aria-valuemax={Math.max(total, completed, 1)} aria-valuenow={completed} aria-valuetext={`${completed} of ${total} steps completed`}><span style={{ width: `${percent}%` }} /></div><div className="progress-footer"><span>{task.current_action || (task.status === 'CANCELLED' ? 'This task was cancelled.' : 'Waiting for the next update.')}</span>{!isTerminal(task.status) && <button className="text-button danger-text" type="button" disabled={Boolean(operation)} onClick={() => { void onCancel(); }}>{operation === 'cancel' ? 'Cancelling…' : 'Cancel run'}</button>}</div></div>}

        <div className="workspace-tabs" role="tablist" aria-label="Task details"><button id="activity-tab" role="tab" aria-selected={tab === 'activity'} aria-controls="activity-panel" tabIndex={tab === 'activity' ? 0 : -1} onClick={() => setTab('activity')} onKeyDown={(event) => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { setTab('results'); document.getElementById('results-tab')?.focus(); } }}>Execution log <span className="tab-count">{task.logs.length}</span></button><button id="results-tab" role="tab" aria-selected={tab === 'results'} aria-controls="results-panel" tabIndex={tab === 'results' ? 0 : -1} onClick={() => setTab('results')} onKeyDown={(event) => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { setTab('activity'); document.getElementById('activity-tab')?.focus(); } }}>Results {result && <span className="tab-count">{result.extracted_count}</span>}</button><span className={`connection-label connection-${connection}`}><span className="status-dot" />{connection === 'live' ? 'Live stream' : connection === 'recovering' ? 'Reconnecting' : connection === 'connecting' ? 'Connecting' : 'Saved activity'}</span></div>
        <div id="activity-panel" role="tabpanel" aria-labelledby="activity-tab" hidden={tab !== 'activity'}><ExecutionLog logs={task.logs} /></div>
        <div id="results-panel" role="tabpanel" aria-labelledby="results-tab" hidden={tab !== 'results'}>{resultsLoading && <p className="result-loading" role="status"><span className="spinner" /> Loading results…</p>}{resultError && <div className="notice error" role="alert">{resultError}<button className="text-button" type="button" onClick={onRefresh}>Retry results</button></div>}{result ? <ResultPreview result={result} /> : !resultsLoading && !resultError && <div className="small-empty"><span className="empty-log-mark" aria-hidden="true">▤</span><p>{task.status === 'SUCCEEDED' ? 'No result data is available yet.' : 'Results will land here.'}</p><span>{isTerminal(task.status) ? 'This task has no saved results.' : 'Approve and run your plan to collect structured data.'}</span></div>}</div>
      </div>}
    </section>
  );
}

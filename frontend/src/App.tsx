import { Composer } from './components/Composer';
import { History } from './components/History';
import { TaskWorkspace } from './components/TaskWorkspace';
import { useDashboard } from './hooks/useDashboard';

export default function App() {
  const dashboard = useDashboard();
  const { health, healthError, checkedAt, tasks } = dashboard;
  const activeCount = tasks.filter((task) => ['CREATED', 'PLANNING', 'RUNNING'].includes(task.status)).length;
  const completedCount = tasks.filter((task) => task.status === 'SUCCEEDED').length;
  const countsUnknown = !tasks.length && (dashboard.historyLoading || Boolean(dashboard.historyError));
  const healthLabel = health ? 'API connected' : healthError ? 'API offline' : 'Connecting to API';

  function newWorkflow() {
    dashboard.selectTask(null);
    document.getElementById('instruction')?.focus();
    document.getElementById('workspace')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to workspace</a>
      <aside className="sidebar">
        <a className="brand" href="#main-content" aria-label="Sanyojan home"><span className="brand-icon"><span className="brand-symbol" /></span><span>Sanyojan<span className="brand-caption">BROWSER WORKFLOW AGENT</span></span></a>
        <div className="workspace-switcher"><span className="workspace-avatar">S</span><div><strong>My workspace</strong><span>Local environment</span></div><span className="workspace-switcher-dot" /></div>
        <span className="nav-label">WORKSPACE</span>
        <nav aria-label="Main navigation"><a className="nav-link active" href="#workspace"><span className="nav-grid" aria-hidden="true"><i /><i /><i /><i /></span>Control center<span className="nav-active-dot" /></a><a className="nav-link" href="#history"><span className="nav-history" aria-hidden="true">↺</span>Run history<span className="nav-count">{tasks.length}</span></a></nav>
        <div className="sidebar-note"><span className="sidebar-note-star" aria-hidden="true">✧</span><h3>Less repetition.<br />More possibility.</h3><p>Turn a simple instruction into a transparent, repeatable browser workflow.</p><span className="sidebar-note-line" /></div>
        <div className="sidebar-bottom"><div className={`agent-indicator ${health?.browser_available ? 'ready' : ''}`}><span className="status-dot" /><div><strong>{health?.browser_available ? 'Browser ready' : health ? 'Browser unavailable' : 'Browser status unknown'}</strong><span>{health ? `${health.planner_mode} planner` : 'Waiting for API health'}</span></div></div><div className="sidebar-footer"><span>Built for intentional work.</span><span>{health ? `v${health.version}` : 'Sanyojan'}</span></div></div>
      </aside>

      <div className="main-shell">
        <header className="topbar"><div className="breadcrumb"><span className="breadcrumb-workspace">Workspace</span><span aria-hidden="true">/</span><strong>Control center</strong></div><div className="topbar-right"><span className={`health-pill ${health ? 'online' : healthError ? 'offline' : ''}`} title={healthError || (checkedAt ? `Last checked ${checkedAt.toLocaleTimeString()}` : 'Checking API health')} role="status"><span className="status-dot" />{healthLabel}</span><span className="user-avatar" title="Local workspace">S</span></div></header>

        <main id="main-content">
          <section className="page-intro"><div><div className="intro-eyebrow"><span className="eyebrow-line" />WORKFLOW CONTROL CENTER</div><h1>Your workflow,<br className="mobile-break" /> <span>orchestrated.</span></h1><p>Give your browser a goal. Stay in control of every step.</p></div><button className="button dark" type="button" onClick={newWorkflow}><span aria-hidden="true">＋</span> New workflow</button></section>

          <div className="overview-stats" aria-label="Workflow statistics"><div className="stat-card"><div className="stat-mark stat-purple" aria-hidden="true">≋</div><div><span>Total workflows</span><strong>{countsUnknown ? '—' : tasks.length}</strong></div><span className="stat-description">All your intents</span></div><div className="stat-card"><div className="stat-mark stat-green" aria-hidden="true">✓</div><div><span>Completed</span><strong>{countsUnknown ? '—' : completedCount}</strong></div><span className="stat-description">Outcomes delivered</span></div><div className="stat-card"><div className="stat-mark stat-amber" aria-hidden="true">↗</div><div><span>In progress</span><strong>{countsUnknown ? '—' : activeCount}</strong></div><span className="stat-description">Live workflows</span></div></div>

          {healthError && <div className="notice warning health-notice"><strong>Connection needs attention</strong><p>{healthError} Health checks retry automatically every 15 seconds.</p></div>}
          {dashboard.operationError && <div className="notice error operation-error" role="alert"><strong>We couldn’t complete that request.</strong><p>{dashboard.operationError}</p></div>}

          <div className="workspace-grid" id="workspace"><Composer busy={Boolean(dashboard.operation)} onPlan={dashboard.planTask} /><TaskWorkspace selectedId={dashboard.selectedId} task={dashboard.task} loading={dashboard.loading} taskError={dashboard.taskError} result={dashboard.result} resultError={dashboard.resultError} resultsLoading={dashboard.resultsLoading} connection={dashboard.connection} operation={dashboard.operation} browserUnavailable={health?.browser_available === false} onExecute={dashboard.executeTask} onCancel={dashboard.cancelTask} onRefresh={dashboard.refreshTask} /></div>

          <History tasks={tasks} loading={dashboard.historyLoading} error={dashboard.historyError} selectedId={dashboard.selectedId} onSelect={(id) => { dashboard.selectTask(id); document.getElementById('workspace')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }); }} onRefresh={dashboard.refreshHistory} />
          <footer className="main-footer"><span><span className="footer-brand-dot" /> Sanyojan <span className="footer-divider">/</span> Intent into action.</span><span>Planned with purpose. Executed with your approval.</span></footer>
        </main>
      </div>
    </div>
  );
}

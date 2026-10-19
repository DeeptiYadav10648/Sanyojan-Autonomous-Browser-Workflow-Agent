import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage, isAbortError } from '../api';
import { isTerminal, type Health, type PlanInput, type Task, type TaskEvent, type TaskResult } from '../types';

const HEALTH_INTERVAL = 15_000;
const RECOVERY_INTERVAL = 4_000;

function mergeLogs(first: TaskEvent[], second: TaskEvent[]) {
  return [...new Map([...first, ...second].map((event) => [event.id, event])).values()]
    .sort((a, b) => a.id - b.id);
}

function isTaskEvent(value: unknown): value is TaskEvent {
  if (!value || typeof value !== 'object') return false;
  const event = value as Partial<TaskEvent>;
  return typeof event.id === 'number' && typeof event.task_id === 'string'
    && typeof event.timestamp === 'string' && typeof event.message === 'string'
    && typeof event.progress === 'number' && Number.isFinite(event.progress)
    && ['state', 'step', 'warning', 'error', 'log'].includes(event.type ?? '')
    && ['CREATED', 'PLANNING', 'PLANNED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'].includes(event.status ?? '');
}

function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let timeout: ReturnType<typeof setTimeout>;
    let controller: AbortController;

    async function poll() {
      controller = new AbortController();
      timeout = setTimeout(() => controller.abort(), 10_000);
      try {
        const response = await api.health(controller.signal);
        if (!alive) return;
        setHealth(response);
        setHealthError(null);
      } catch (error) {
        if (!alive) return;
        setHealth(null);
        setHealthError(isAbortError(error) ? 'Health check timed out.' : errorMessage(error));
      } finally {
        clearTimeout(timeout);
        if (alive) {
          setCheckedAt(new Date());
          timer = setTimeout(() => { void poll(); }, HEALTH_INTERVAL);
        }
      }
    }

    void poll();
    return () => {
      alive = false;
      controller?.abort();
      clearTimeout(timer);
      clearTimeout(timeout);
    };
  }, []);

  return { health, healthError, checkedAt };
}

function useHistory() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  const upsertTask = useCallback((task: Task) => {
    setTasks((previous) => [task, ...previous.filter((item) => item.id !== task.id)]
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)));
  }, []);

  const refreshHistory = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setHistoryLoading(true);
    try {
      const history = await api.tasks(controller.signal);
      if (controller.signal.aborted) return;
      setTasks(history.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)));
      setHistoryError(null);
    } catch (error) {
      if (!controller.signal.aborted) setHistoryError(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
    return () => { requestRef.current?.abort(); };
  }, [refreshHistory]);

  return { tasks, historyLoading, historyError, refreshHistory, upsertTask };
}

type Connection = 'idle' | 'connecting' | 'live' | 'recovering' | 'closed';

function useSelectedTask(
  id: string | null,
  revision: number,
  onSnapshot: (task: Task) => void,
  onTerminal: () => Promise<void>,
) {
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(false);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [result, setResult] = useState<TaskResult | null>(null);
  const [resultError, setResultError] = useState<string | null>(null);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [connection, setConnection] = useState<Connection>('idle');

  useEffect(() => {
    setTask(null);
    setResult(null);
    setTaskError(null);
    setResultError(null);
    setResultsLoading(false);
    setConnection('idle');
    setLoading(Boolean(id));
    if (!id) return;

    let alive = true;
    let source: EventSource | null = null;
    let recoveryTimer: ReturnType<typeof setTimeout> | undefined;
    let requestSerial = 0;
    let eventVersion = 0;
    let terminal = false;
    let terminalProcessed = false;
    let resultsRequested = false;
    const controller = new AbortController();
    const taskId = id;

    function closeStream() {
      source?.close();
      source = null;
      clearTimeout(recoveryTimer);
      recoveryTimer = undefined;
    }

    async function loadResults() {
      if (resultsRequested || !alive) return;
      resultsRequested = true;
      setResultsLoading(true);
      try {
        const response = await api.results(taskId, controller.signal);
        if (alive) {
          setResult(response);
          setResultError(null);
        }
      } catch (error) {
        if (alive && !isAbortError(error)) setResultError(errorMessage(error));
      } finally {
        if (alive) setResultsLoading(false);
      }
    }

    function scheduleRecovery() {
      if (!alive || recoveryTimer) return;
      recoveryTimer = setTimeout(() => {
        recoveryTimer = undefined;
        void synchronize(true);
      }, RECOVERY_INTERVAL);
    }

    async function synchronize(recovering = false) {
      const serial = ++requestSerial;
      const version = eventVersion;
      try {
        const snapshot = await api.task(taskId, controller.signal);
        if (!alive || serial !== requestSerial || version !== eventVersion) return;
        // A terminal SSE message must not be overwritten by an older HTTP snapshot.
        if (terminal && !isTerminal(snapshot.status)) {
          scheduleRecovery();
          return;
        }
        setTask((previous) => ({
          ...snapshot,
          logs: mergeLogs(previous?.id === taskId ? previous.logs : [], snapshot.logs),
        }));
        setTaskError(null);
        onSnapshot(snapshot);
        if (isTerminal(snapshot.status)) {
          terminal = true;
          closeStream();
          setConnection('closed');
          if (snapshot.result) setResult(snapshot.result);
          if (snapshot.status === 'SUCCEEDED' || snapshot.result) void loadResults();
          if (!terminalProcessed) {
            terminalProcessed = true;
            void onTerminal();
          }
        } else {
          connect();
          if (recovering && source?.readyState !== EventSource.OPEN) scheduleRecovery();
        }
      } catch (error) {
        if (!alive || serial !== requestSerial || isAbortError(error)) return;
        setTaskError(errorMessage(error));
        if (recovering || terminal) scheduleRecovery();
      } finally {
        if (alive && serial === requestSerial) setLoading(false);
      }
    }

    function connect() {
      if (!alive || source || terminal) return;
      setConnection('connecting');
      try {
        source = new EventSource(api.eventsUrl(taskId));
        source.onopen = () => {
          if (!alive || terminal) return;
          clearTimeout(recoveryTimer);
          recoveryTimer = undefined;
          setConnection('live');
        };
        source.addEventListener('progress', (message) => {
          if (!alive || terminal) return;
          let event: TaskEvent;
          try {
            const parsed: unknown = JSON.parse((message as MessageEvent<string>).data);
            if (!isTaskEvent(parsed)) throw new Error('Invalid progress event');
            event = parsed;
          } catch {
            setTaskError('A live update could not be read. Refreshing the task…');
            void synchronize(true);
            return;
          }
          if (event.task_id !== taskId) return;
          eventVersion += 1;
          setTaskError(null);
          setTask((previous) => {
            if (!previous || previous.id !== taskId) return previous;
            // Replayed Last-Event-ID messages are harmless, including status updates.
            if (previous.logs.some((item) => item.id === event.id)) return previous;
            const latestId = previous.logs.at(-1)?.id ?? -1;
            return {
              ...previous,
              ...(event.id > latestId ? {
                status: event.status,
                progress: event.progress,
                updated_at: event.timestamp,
                current_action: event.message,
              } : {}),
              logs: mergeLogs(previous.logs, [event]),
            };
          });
          if (isTerminal(event.status)) {
            terminal = true;
            closeStream();
            setConnection('closed');
            terminalProcessed = true;
            void onTerminal();
            void synchronize(true);
          }
        });
        source.onerror = () => {
          if (!alive || terminal) return;
          setConnection('recovering');
          // Keep the same EventSource: native reconnect preserves Last-Event-ID.
          void synchronize(true);
        };
      } catch {
        setConnection('recovering');
        setTaskError('Live updates are unavailable. Checking task status periodically.');
        scheduleRecovery();
      }
    }

    void synchronize();
    return () => {
      alive = false;
      controller.abort();
      closeStream();
    };
  }, [id, revision, onSnapshot, onTerminal]);

  // Hide the old selection immediately, before its effect cleanup has run.
  const currentTask = task?.id === id ? task : null;
  return { task: currentTask, loading, taskError, result: currentTask ? result : null, resultError, resultsLoading, connection };
}

export function useDashboard() {
  const health = useHealth();
  const history = useHistory();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [operation, setOperation] = useState<'plan' | 'execute' | 'cancel' | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const actionRef = useRef<AbortController | null>(null);
  const selected = useSelectedTask(selectedId, revision, history.upsertTask, history.refreshHistory);

  useEffect(() => () => { actionRef.current?.abort(); }, []);

  const selectTask = useCallback((id: string | null) => {
    actionRef.current?.abort();
    setOperation(null);
    setOperationError(null);
    setSelectedId(id);
    // Reopening the selected history entry should reload its logs/results too.
    setRevision((value) => value + 1);
  }, []);

  async function runOperation(kind: 'plan' | 'execute' | 'cancel', input?: PlanInput) {
    actionRef.current?.abort();
    const controller = new AbortController();
    actionRef.current = controller;
    setOperation(kind);
    setOperationError(null);
    try {
      let response: Task;
      if (kind === 'plan' && input) response = await api.plan(input, controller.signal);
      else if (kind === 'execute' && selectedId) response = await api.execute(selectedId, controller.signal);
      else if (kind === 'cancel' && selectedId) response = await api.cancel(selectedId, controller.signal);
      else throw new Error('Select a task first.');
      if (controller.signal.aborted) return;
      history.upsertTask(response);
      setSelectedId(response.id);
      setRevision((value) => value + 1);
      void history.refreshHistory();
    } catch (error) {
      if (!controller.signal.aborted) {
        setOperationError(errorMessage(error));
        // A response can be lost after the server accepted a mutation.
        void history.refreshHistory();
        if (selectedId) setRevision((value) => value + 1);
      }
    } finally {
      if (!controller.signal.aborted) setOperation(null);
    }
  }

  return {
    ...health,
    ...history,
    ...selected,
    selectedId,
    operation,
    operationError,
    selectTask,
    refreshTask: () => setRevision((value) => value + 1),
    planTask: (input: PlanInput) => runOperation('plan', input),
    executeTask: () => runOperation('execute'),
    cancelTask: () => runOperation('cancel'),
  };
}

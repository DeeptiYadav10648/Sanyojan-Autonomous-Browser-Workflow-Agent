export type TaskType = 'extraction' | 'inspection' | 'report';
export type TaskStatus =
  | 'CREATED'
  | 'PLANNING'
  | 'PLANNED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED';

export interface Health {
  status: 'ok';
  planner_mode: 'deterministic';
  browser_available: boolean;
  version: string;
}

export interface PlanStep {
  number: number;
  action: string;
  target: string;
  expected_result: string;
  validation_rule: string;
  risk: 'low';
  timeout_ms: number;
}

export interface Plan {
  mode: 'deterministic';
  summary: string;
  fields: string[];
  warnings: string[];
  steps: PlanStep[];
}

export interface TaskEvent {
  id: number;
  task_id: string;
  timestamp: string;
  type: 'state' | 'step' | 'warning' | 'error' | 'log';
  message: string;
  status: TaskStatus;
  progress: number;
}

export interface ResultRecord {
  data: Record<string, string | number | null>;
  source_url: string;
  extracted_at: string;
  valid: boolean;
  warnings: string[];
}

export interface TaskResult {
  records: ResultRecord[];
  extracted_count: number;
  valid_count: number;
  warnings: string[];
  failed_steps: number[];
  duration_seconds: number;
  reports: { csv?: string; json?: string };
  page_title: string;
}

export interface Task {
  id: string;
  instruction: string;
  target_url: string;
  task_type: TaskType;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  plan: Plan | null;
  progress: number;
  current_action: string;
  logs: TaskEvent[];
  result: TaskResult | null;
  error: string | null;
}

export interface PlanInput {
  instruction: string;
  target_url: string;
  task_type: TaskType;
  fields?: string[];
}

export const isTerminal = (status: TaskStatus) =>
  status === 'SUCCEEDED' || status === 'FAILED' || status === 'CANCELLED';

export const statusLabels: Record<TaskStatus, string> = {
  CREATED: 'Created',
  PLANNING: 'Planning',
  PLANNED: 'Awaiting approval',
  RUNNING: 'Running',
  SUCCEEDED: 'Completed',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
};

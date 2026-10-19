import type { Health, Task, TaskEvent, TaskResult } from '../types';

export const health: Health = {
  status: 'ok', planner_mode: 'deterministic', browser_available: true, version: '0.1.0',
};

export function makeEvent(overrides: Partial<TaskEvent> = {}): TaskEvent {
  return {
    id: 1,
    task_id: 'task-001',
    timestamp: '2026-10-19T10:00:00Z',
    type: 'state',
    message: 'Plan ready for approval.',
    status: 'PLANNED',
    progress: 0,
    ...overrides,
  };
}

export function makeResult(overrides: Partial<TaskResult> = {}): TaskResult {
  return {
    records: [{ data: { title: 'Everyday notebook', price: 12.5, category: 'Stationery' }, source_url: 'https://demo.sanyojan.test/products', extracted_at: '2026-10-19T10:01:00Z', valid: true, warnings: [] }],
    extracted_count: 1,
    valid_count: 1,
    warnings: [],
    failed_steps: [],
    duration_seconds: 2.5,
    reports: { csv: '/api/tasks/task-001/reports/csv', json: '/api/tasks/task-001/reports/json' },
    page_title: 'Demo catalog results',
    ...overrides,
  };
}

export function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-001',
    instruction: 'Extract the title, price, and category of every product from this demo catalog.',
    target_url: 'https://demo.sanyojan.test/products',
    task_type: 'extraction',
    status: 'PLANNED',
    created_at: '2026-10-19T10:00:00Z',
    updated_at: '2026-10-19T10:00:00Z',
    started_at: null,
    finished_at: null,
    plan: {
      mode: 'deterministic',
      summary: 'Open the catalog, extract the requested fields, and validate the records.',
      fields: ['title', 'price', 'category'],
      warnings: [],
      steps: [
        { number: 1, action: 'Open catalog', target: 'https://demo.sanyojan.test/products', expected_result: 'The catalog is accessible.', validation_rule: 'Page contains products.', risk: 'low', timeout_ms: 15000 },
        { number: 2, action: 'Extract product fields', target: 'product cards', expected_result: 'Product records have the requested fields.', validation_rule: 'Every record has a title.', risk: 'low', timeout_ms: 10000 },
        { number: 3, action: 'Validate and export', target: 'records', expected_result: 'CSV and JSON reports are available.', validation_rule: 'Reports match extracted records.', risk: 'low', timeout_ms: 5000 },
      ],
    },
    progress: 0,
    current_action: 'Ready for approval.',
    logs: [makeEvent()],
    result: null,
    error: null,
    ...overrides,
  };
}

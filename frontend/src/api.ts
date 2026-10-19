import type { Health, PlanInput, Task, TaskResult } from './types';

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const isAbortError = (error: unknown) =>
  // DOMException and errors from another realm need not extend this realm's Error.
  typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError';

export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'An unexpected error occurred. Please try again.';

export function createApiClient(baseUrl = '') {
  const base = baseUrl.trim().replace(/\/+$/, '');
  const url = (path: string) => `${base}/${path.replace(/^\/+/, '')}`;

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await fetch(url(path), {
        ...init,
        headers: { Accept: 'application/json', ...init.headers },
      });
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new ApiError('Cannot reach the API. Check your connection and try again.', 'NETWORK_ERROR', 0);
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new ApiError(
        response.ok ? 'The API returned an unreadable response.' : `The API request failed (${response.status}).`,
        'INVALID_RESPONSE',
        response.status,
      );
    }

    if (!response.ok) {
      const payload = body as { error?: { message?: string; code?: string; details?: unknown } } | null;
      throw new ApiError(
        payload?.error?.message || `The API request failed (${response.status}).`,
        payload?.error?.code || 'REQUEST_FAILED',
        response.status,
        payload?.error?.details,
      );
    }
    return body as T;
  }

  const post = <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, {
      method: 'POST',
      signal,
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    });
  const taskPath = (id: string) => `/api/tasks/${encodeURIComponent(id)}`;

  return {
    url,
    health: (signal?: AbortSignal) => request<Health>('/api/health', { signal }),
    tasks: (signal?: AbortSignal) => request<Task[]>('/api/tasks', { signal }),
    task: (id: string, signal?: AbortSignal) => request<Task>(taskPath(id), { signal }),
    plan: (input: PlanInput, signal?: AbortSignal) => post<Task>('/api/tasks/plan', input, signal),
    execute: (id: string, signal?: AbortSignal) => post<Task>(`${taskPath(id)}/execute`, { approved: true }, signal),
    cancel: (id: string, signal?: AbortSignal) => post<Task>(`${taskPath(id)}/cancel`, undefined, signal),
    results: (id: string, signal?: AbortSignal) => request<TaskResult>(`${taskPath(id)}/results`, { signal }),
    eventsUrl: (id: string) => url(`${taskPath(id)}/events`),
  };
}

export const api = createApiClient(import.meta.env.VITE_API_BASE_URL || '');

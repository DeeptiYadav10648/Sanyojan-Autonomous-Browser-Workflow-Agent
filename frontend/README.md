# Sanyojan dashboard

A React 19 + Vite + TypeScript control center for the Sanyojan browser workflow API. Application code always uses the real API; HTTP and EventSource mocks exist only in tests. No icon, font, or component-library network dependencies are required.

## Workspace integration

This directory is the `@sanyojan/frontend` npm workspace. The repository owner must include `frontend` in the root package's `workspaces` and generate/maintain the **root `package-lock.json`**. Dependencies are pinned exactly in this package. Node **22.16.0** is pinned in `.nvmrc`.

Dependency installation is owned by the repository's main agent. From the repository root, after the root workspace exists:

```sh
npm install
```

Use `npm ci` on subsequent clean checkouts with the committed root lockfile. Do not install global packages or generate a competing frontend lockfile.

## Run

Start the backend at `http://127.0.0.1:8000`, then from the repository root:

```sh
npm run dev --workspace @sanyojan/frontend
```

Open **http://127.0.0.1:5173**. Vite binds to `127.0.0.1`, uses port `5173`, refuses a different port, and proxies `/api` to `http://127.0.0.1:8000`, including SSE.

The default API base is empty (same origin). To target a different API, copy `.env.example` to `.env.local` within `frontend/` and set `VITE_API_BASE_URL` to the desired origin or path prefix. Restart Vite after changes. A cross-origin backend must permit the frontend origin with CORS. This is a build-time setting; it also prefixes SSE and CSV/JSON report links.

## Checks (offline after installation)

From the repository root:

```sh
npm test --workspace @sanyojan/frontend
npm run build --workspace @sanyojan/frontend
```

Or, from this directory:

```sh
npm test
npm run build
npm run test:watch
```

`npm test` is the single offline frontend test command. Tests cover API requests/error handling, explicit approval, examples/fields/task types, cancellation, actual completed-step progress, event replay deduplication, results/download URLs, saved logs, native SSE reconnect recovery, selection races, cleanup, and live health polling. Tests mock only the browser's HTTP and EventSource boundaries. `build` runs strict TypeScript checking before producing `dist/`.

## Production integration

Build with `npm run build --workspace @sanyojan/frontend`. Serve `frontend/dist/` from the repository's production web server/container, with `/api` routed to the backend. The development proxy is not part of the production bundle. Disable response buffering for the SSE route and allow long-lived connections. For separate frontend/API origins, set `VITE_API_BASE_URL` before building and configure CORS on the API. The frontend has one page and needs no client-side route rewrites.

The root Dockerfile, root runtime configuration, lockfile, and deployment orchestration belong to the repository owner; this frontend does not edit them.

## Behavior

- Health is fetched on mount and every 15 seconds; an unavailable API clears stale health data. Health requests time out after 10 seconds. Browser readiness and API version come directly from health.
- Planning sends the instruction (10–2,000 trimmed characters), target URL, selected task type, and optional supported output fields. Empty fields are omitted so backend defaults apply. Demo and public-books examples only fill the form.
- Plans expose summary, warnings, output fields, step target, validation, risk, and timeout. **Approve & execute** posts `{ "approved": true }`; planning never executes automatically. A known-unavailable browser disables execution until health recovers.
- History fetches full task snapshots including saved logs. Selection aborts stale HTTP work and closes the previous EventSource and recovery timers. Creating a plan selects it; selecting a different history entry cancels the previous pending UI operation.
- Named SSE `progress` events are merged by event ID. The same native EventSource stays alive on transient errors, preserving browser-managed `Last-Event-ID` reconnect behavior. `onerror` also fetches a task snapshot; a 4-second recovery check covers prolonged disconnects. Terminal snapshots/events close the stream, refresh history, and fetch results for successful runs or saved partial results.
- Progress uses completed step counts, never elapsed-time estimates. Result previews show up to 20 records with validation, warnings, source links, counts, duration, and complete CSV/JSON downloads.
- Failed requests show actionable messages and retry controls. No tasks, health, logs, progress, or results are fabricated by the application.

## Boundaries

The frontend depends on the supplied API contract. It does not provide a backend, browser runtime, authentication, persistent local cache, or production reverse proxy. Initial empty/offline states are intentional. A live backend/browser smoke test and visual browser review require the repository integration environment.

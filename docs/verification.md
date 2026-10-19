# Verification record

## Environment and commands actually run

Verified on Windows 11 with Node 24.19.0, npm 11.17.0, Python 3.11.9, and uv 0.12.22. The repository was empty on the existing `main` branch; its existing GitHub origin was preserved.

| Command / check | Observed result |
| --- | --- |
| `uv lock --check` / `uv sync --frozen` | Lock is current; 26 installed Python packages checked. |
| `npm ci` | Clean lockfile installation succeeded. |
| `$env:PLAYWRIGHT_BROWSERS_PATH="0"; .\.venv\Scripts\python.exe -m playwright install chromium` | Chromium and supporting binaries installed inside the project `.venv`. |
| `npm test` | TypeScript/Vite production build passed; **272 Python tests passed, 1 skipped**; **22 frontend tests passed**. |
| `npm audit` | **0 reported vulnerabilities**, including development dependencies, after pinning Vite 6.4.4 and Vitest 4.1.11. This is not a Python/system/browser vulnerability audit. |
| `.\.venv\Scripts\python.exe scripts/demo.py` | Real connected dashboard demo succeeded; desktop/mobile screenshots generated. |
| Direct backend and Vite-proxied `/api/health` | Both returned `status: ok`, `planner_mode: deterministic`, `browser_available: true`. |
| Public Books to Scrape plan → approve → execute | **SUCCEEDED: 20 extracted, 20 valid**, actual title `All products \| Books to Scrape - Sandbox`. This network smoke check is not required by the offline tests. |
| `git diff --check` | Passed. Local configuration, databases, reports, screenshots, virtualenv, and dependency/build directories are ignored. |

The one skipped test creates a filesystem symlink: this Windows environment does not grant the required symlink privilege. Canonical IDs, formats, directory containment, and traversal rejection have independent passing tests. Starlette's TestClient produces one dependency-level AnyIO deprecation warning; no application test failure is hidden by it.

## What the real browser tests prove

`tests/test_e2e.py` starts an isolated HTTP backend with temporary SQLite/report storage and uses actual managed Chromium. It covers:

- Planning leaves the task awaiting approval; approval starts the real browser pipeline.
- The bundled fixture is read through Playwright and produces **three extracted/validated products**, not precomputed result JSON.
- CSV/JSON endpoints return real serialized files and matching provenance.
- Durable SSE events can be replayed after an event cursor.
- The built React dashboard reports real health, plans and approves a task, subscribes to actual SSE, displays extracted rows, and downloads a CSV.
- Refresh/history navigation restores the persisted task and results.
- A 390px viewport has no document-level horizontal overflow; tables scroll within their containers.
- Page inspection returns the real title/visible text, and cancellation of a running browser task is terminal-idempotent and denies report downloads.

Unit/API tests separately exercise mocked model responses, invalid schemas, unsafe URLs/DNS/redirects, IP pinning/TLS SNI, state races, bounded execution, cancellation cleanup, extraction profiles, missing/duplicate/malformed records, CSV formula protection, report path safety, HTTP errors, and frontend reconnect/selection races.

## Local preview and artifacts

The development preview was started on `http://127.0.0.1:5173`, with FastAPI on `http://127.0.0.1:8000`. Both can be restarted using the README commands. `scripts/demo.py` writes these uncommitted, reproducible artifacts:

- `test-results/demo-desktop.png`
- `test-results/demo-mobile.png`

Task history and actual downloadable reports remain in local `data/`, not source control.

## Explicit gaps

- **Docker executable is absent here. Docker image build, Compose validation/startup, and Linux execution were not run.** The Dockerfile provisions locked dependencies, Chromium/system packages, non-root runtime, healthcheck, and the built UI; its first real container verification remains a manual acceptance step.
- GNU Make is absent here; `make test` is a convenience alias. Its underlying `npm test` was run successfully.
- No live AI planning integration is claimed. The documented OmniRush one-shot coding CLI was inspected, not exposed to HTTP input. The app uses the labeled deterministic planner; see `model-integration.md`.
- Public multi-user deployment is not production-ready: there is no authentication, ownership, retention/deletion API, verified Chromium sandbox, external egress firewall, distributed queue, or hard resource quota. See `security.md`.
- No push, remote repository creation, or visibility change was performed. Inspect/authenticate/push manually using the README publication commands.

# Sanyojan — From intent to action

A local-first dashboard for planning, approving, and running bounded, read-only browser workflows. Inspect one static page or extract supported product listings, follow durable progress events, and download validated CSV and JSON reports.

**This MVP uses a deterministic, limited natural-language grammar, not a live AI model.** No API keys, OmniRush login, or other secrets are needed. An injectable model-understanding interface exists; see [model integration](docs/model-integration.md).

Repository: [DeeptiYadav10648/Sanyojan-Autonomous-Browser-Workflow-Agent](https://github.com/DeeptiYadav10648/Sanyojan-Autonomous-Browser-Workflow-Agent).

## Try the workflow

1. Start the backend and frontend below, then open **http://127.0.0.1:5173**.
2. Select **Demo catalog** and **Generate a plan**. Review the five actions, warnings, and fields.
3. Approve execution. Watch progress, inspect records and validation warnings, and download CSV or JSON.
4. Reopen the task from history, or cancel a nonterminal task.

The demo target, **`https://demo.sanyojan.test/products`**, is an exact, virtual fixture URL. Playwright intercepts its navigation and supplies `backend/sanyojan/fixtures/products.html`; no fixture service listens on a port, no DNS lookup is needed, and there is no private-address exception. Do not open that URL in an ordinary browser. The bundled catalog has three products.

For a public example use `https://books.toscrape.com/` with **title** and **price** selected. This is the only public hostname allowed by default. Its listing profile does not supply category or description; requesting those fields produces explicit missing-value warnings.

### Supported scope

- `extraction` and `report`: one page of visible `data-record` fields, Books to Scrape listings, or supported schema.org Product markup. Both produce CSV and JSON.
- `inspection`: actual page title and visible body text, limited to `title` / `description` fields.
- Fields: `title`, `price`, `category`, `description`. Every requested field is required for a record to be valid; missing values stay null. Price normalization is not currency conversion.
- The fixed pipeline is `navigate → inspect_title → extract → validate → report`. All execution requires explicit approval of a `PLANNED` task.
- No scripts, browser-originated page network requests, clicking, filling, logins, payments, uploads, screenshots, pagination, crawling, sorting, filtering, arbitrary selectors, or user-specified record counts. External styles/images are blocked, so visibility is evaluated in the restricted rendering, not an exact reproduction of the live site.

Instructions such as “Extract product titles and prices and generate CSV and JSON reports” are supported. Ambiguous or unsupported instructions are rejected. Fields are inferred from the instruction when omitted; extraction defaults to title/price if no field words match, and inspection defaults to title/description.

## Prerequisites

Run all commands from the **repository root**.

| Tool | Version / source of truth |
| --- | --- |
| Node.js | **24.19.0**, `.nvmrc` (root npm workspace runtime) |
| Python | **3.11.9**, `.python-version` |
| uv | **0.12.22**, Python dependency manager; root `uv.lock` |
| npm | Bundled with Node; root `package-lock.json` covers the frontend workspace |
| Chromium | Playwright **1.56.0**'s managed Chromium, installed below |
| Docker Engine + Compose v2 | Only for container deployment |
| GNU Make | Optional convenience wrapper; `npm test` is the canonical check |

Dependency and browser installation require internet access. Application dependencies go into root `.venv` and `node_modules`, not global installs. The commands below use Playwright's hermetic mode (`PLAYWRIGHT_BROWSERS_PATH=0`), which installs Chromium inside `.venv`; the backend, demo script, and tests default to that same mode. Linux Chromium also needs system libraries; the Dockerfile installs these with Playwright's `--with-deps` option.

## Setup and run: Windows PowerShell

With the pinned runtimes and uv available:

```powershell
uv sync --frozen
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
.\.venv\Scripts\python.exe -m playwright install chromium
npm ci
Copy-Item .env.example .env
```

Copy the example only on first setup; retain any existing `.env` settings. No virtual-environment activation is required.

Backend, terminal 1:

```powershell
.\.venv\Scripts\python.exe -m uvicorn sanyojan.app:app --app-dir backend --host 127.0.0.1 --port 8000
```

Frontend, terminal 2:

```powershell
npm run dev
```

Vite listens on `127.0.0.1:5173` and proxies `/api` to `127.0.0.1:8000`. Check readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

`status: ok` means the API is responding; **`browser_available: true`** is also required for execution. The browser check runs at backend startup, so restart after installing Chromium. Stop each development server with **Ctrl+C**.

Optional: drive the real demo UI automatically and capture desktop/mobile screenshots while both servers are running:

```powershell
.\.venv\Scripts\python.exe scripts/demo.py
```

This plans and approves an actual demo task, then saves `test-results/demo-desktop.png` and `test-results/demo-mobile.png` locally (not committed). For the built dashboard, pass `--url http://127.0.0.1:8000`.

## Setup and run: Linux

With Node 24.19.0, Python 3.11.9, uv 0.12.22, and Chromium system libraries available:

```sh
uv sync --frozen
export PLAYWRIGHT_BROWSERS_PATH=0
.venv/bin/python -m playwright install chromium
npm ci
cp -n .env.example .env
```

Run these in separate terminals:

```sh
.venv/bin/python -m uvicorn sanyojan.app:app --app-dir backend --host 127.0.0.1 --port 8000
npm run dev
```

Or use `make setup`, then `make backend` and `make dev` in separate terminals. If Linux browser libraries are missing, use the container deployment below, which provisions them during image construction.

## Configuration and storage

- Root **`.env` is backend configuration** (`Settings` reads it relative to the current working directory); it is not Vite configuration. Environment variables override that file. See [.env.example](.env.example) for every `SANYOJAN_*` setting and its bounds.
- `SANYOJAN_ALLOWED_DOMAINS`, `SANYOJAN_CORS_ORIGINS`, and `SANYOJAN_TRUSTED_HOSTS` must be **JSON arrays**, e.g. `["books.toscrape.com"]`, not comma-separated strings. Host allowlists use exact names.
- Frontend configuration uses the existing **`frontend/.env.example`**. Optionally copy it to `frontend/.env`; leave `VITE_API_BASE_URL` empty for the development proxy and same-origin deployment. Vite embeds that value at build/start time; it is public, never a place for secrets. This Docker build intentionally uses the same-origin default.
- Defaults: `data/tasks.sqlite3` for SQLite history/events, `data/reports/` for reports, `frontend/dist/` for the built UI. Paths are relative to the root. Keep these writable storage paths private.
- Defaults bound execution to 60 seconds, 15 seconds per browser action, 2,000,000 HTML bytes, 100 records, three redirects, two concurrent tasks, and 1,000 stored tasks. There is no queue or automatic history deletion. Full storage rejects new plans.

The service uses one process/worker. Do not add Uvicorn workers or replicas sharing this database: worker ownership, concurrency limits, and cancellation are in memory. History persists; interrupted active tasks become `FAILED` on restart, and `PLANNED` tasks remain available for approval.

## Test and build

After installing dependencies and Chromium:

```sh
npm test
```

This is the single offline test command on Windows and Linux (`make test` delegates to it). It builds the frontend with TypeScript/Vite, runs `.venv`'s `pytest -q` (including real HTTP/Chromium dashboard E2E tests), then frontend Vitest. It does not install packages or browsers. The public Books to Scrape example requires internet; offline tests and the virtual demo do not.

```sh
npm run build
```

The build writes `frontend/dist`. Restart the backend after building to serve the dashboard and API together at **http://127.0.0.1:8000**, without Vite. The runtime entry point is `backend/sanyojan/app.py:create_app`, with module-level `app` for Uvicorn.

Validation environment for this implementation: **Windows 11, Node 24.19.0, Python 3.11.9, uv 0.12.22**. Docker is not installed in that environment; **Docker build, Compose startup, and Linux container execution have not been verified**. The runtime image contains production Python dependencies, not the host test toolchain; run `npm test` on a prepared host before deployment.

## Deploy and stop with Docker Compose

Docker builds require network access for base images, locked dependencies, Chromium, and system packages. Start from the root with a first-time copy of `.env.example` to `.env` (`Copy-Item` on PowerShell or `cp -n` on Linux), then:

```sh
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs -f sanyojan
```

Open **http://127.0.0.1:8000**. The multi-stage build compiles the frontend with Node 24.19.0 and serves it from FastAPI in Python 3.11.9. uv is pinned to 0.12.22, Python installation uses `uv sync --frozen --no-dev`, and Chromium plus its system packages are installed in the image. The image healthcheck requires API health and the startup Chromium-availability flag.

Compose passes `.env` to the backend, fixes data/frontend paths and headless mode for the image, and binds the host port to loopback only. It uses a non-root app user, a writable named data volume, an init process, 1 GiB shared memory, dropped capabilities, and `no-new-privileges`. Browser binaries are readable at `/opt/playwright` via `PLAYWRIGHT_BROWSERS_PATH`.

Stop containers while retaining history and reports:

```sh
docker compose down
```

`make deploy`, `make logs`, and `make stop` wrap these commands. `docker compose down --volumes` additionally **deletes stored task data and reports**. For backups, stop the service and archive the complete data volume (or local `data/` directory), including SQLite files and reports together.

This is an unauthenticated local demo. A remote deployment needs an authenticated TLS gateway and network isolation/egress policy; loopback binding and Playwright routing are not a browser-sandbox guarantee. See [security](docs/security.md).

## Troubleshooting

| Symptom | Check / remedy |
| --- | --- |
| `Project Python missing` or `pytest` missing | Run `uv sync --frozen` from the root; the test runner uses root `.venv`. A production `--no-dev` install lacks pytest. |
| `BROWSER_UNAVAILABLE`, or health has `browser_available: false` | Set `PLAYWRIGHT_BROWSERS_PATH=0` and install Chromium with the documented `.venv` interpreter, then restart the backend. A browser in the user's global cache is not the default. On Linux, missing system libraries are provisioned by the Dockerfile. |
| Cannot reach API / port already in use | Keep both dev terminals running; verify ports 8000/5173 and `/api/health`. Vite uses a strict port. Stop the process occupying the port before restarting. |
| Environment settings fail to parse | Use JSON arrays for list settings and start from the root. Keep `VITE_API_BASE_URL` in `frontend/.env`, not root `.env`. |
| Public URL is blocked | Only the exact allowlisted host and HTTP(S) default ports are accepted. No credentials, queries, fragments, private/reserved DNS answers, or cross-host redirects. Use the exact virtual demo URL for an offline run. |
| Missing fields / no records | Use supported static markup. For books select title/price; for inspection select only title/description. JavaScript-rendered catalogs and hidden-only metadata are unsupported. |
| Live progress disconnects | Native SSE reconnect uses `Last-Event-ID`; the UI also fetches snapshots. Ensure any proxy disables SSE buffering; refresh the selected task if necessary. |
| `CAPACITY_REACHED` / `RATE_LIMITED` / `HISTORY_FULL` | Wait for an active task, wait one minute for mutation rate limits, or stop and archive the full data directory/volume before starting a fresh workspace. |
| Container data permission error | Use the default named volume. A substituted bind mount must be writable by UID/GID 10001. |

## Project structure and deeper contracts

```text
backend/sanyojan/   FastAPI, orchestration, eight agent roles, safety, SQLite
  fixtures/        Bundled virtual demo HTML
frontend/          React 19 + TypeScript + Vite dashboard (root npm workspace)
tests/             Python checks
scripts/check.mjs  Cross-platform offline test/build entry point
pyproject.toml     Python dependencies and pytest configuration
uv.lock            Locked Python dependency graph
package-lock.json  Locked root/frontend npm dependency graph
docs/              Architecture, security, and model-integration contracts
Dockerfile         Frontend build stage and non-root backend runtime
docker-compose.yml Loopback deployment and persistent data volume
```

- [Architecture, eight roles, API/status table, SSE, and state diagram](docs/architecture.md)
- [Security boundaries and remaining limitations](docs/security.md)
- [Deterministic planner and provider-neutral model integration](docs/model-integration.md)
- [Actual verification results and remaining deployment checks](docs/verification.md)

### Publication

The implementation is committed locally on `main`; it has not been pushed. Use the existing `origin` repository linked above; do not replace its URL. Before publishing:

```powershell
git status --short
git remote -v
git log --oneline -5
gh auth status
npm test
git push -u origin main
```

Run the push only when ready to publish. If `gh auth status` fails, run `gh auth login`. If the remote already has independent history, fetch and inspect it before integrating; never force-push to overwrite it. In GitHub, verify the repository is public, add a description and demo screenshots/video, and use its URL for the buildathon submission. Do not add `.env`, local data/reports, logs, `node_modules`, or `.venv`.

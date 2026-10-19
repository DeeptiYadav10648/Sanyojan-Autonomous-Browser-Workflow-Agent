# Security boundaries

Sanyojan's MVP is an **unauthenticated, local, single-user demo** for reading one approved static page. Treat instructions and all extracted page content as untrusted. No secrets, provider keys, authenticated browsing sessions, or personal-data workflows are required.

## Approval and executable authority

- Planning does not launch a browser workflow. Execution requires a persisted `PLANNED` task and `POST /api/tasks/{id}/execute` with `{"approved":true}`. The backend revalidates safety and state before creating a worker.
- Strict Pydantic models reject extra properties. `Plan` accepts only five ordered actions: `navigate`, `inspect_title`, `extract`, `validate`, `report`. The dispatcher is fixed Python code; plan targets, page text, and model output are never shell commands or executable selectors.
- The default parser rejects unsupported actions/constraints, including clicks, form filling, logins, purchases, uploads, scripts, pagination, sorting, and filtering. This grammar is a usability/policy filter, not the sole security boundary; the execution code lacks these capabilities.
- An injected understanding adapter is trusted server-side code but its returned `TaskSpec` is still schema-checked and URL/type-bound. No HTTP input chooses adapters or runs the OmniRush CLI. See [model integration](model-integration.md).

## Public fetch / SSRF boundary

`safety.SafetyPolicy` is the only application transport for public page HTML. Chromium does not fetch that HTML from the public network.

1. Accept only HTTP(S), default ports, and an **exact** configured allowlisted hostname. The default public allowlist is `["books.toscrape.com"]`; no wildcard subdomains. Reject embedded credentials, query strings, fragments, malformed characters, localhost/metadata hostnames, and disallowed IP literals.
2. Resolve DNS with a bounded timeout at planning, again before execution, and freshly for **every fetch/redirect hop**. Reject the destination if **any** returned address is nonpublic, reserved, multicast, or a blocked IPv6 transition/mapped address.
3. Connect to the vetted numeric IP itself, not a second hostname lookup. Preserve the approved HTTP Host and, for HTTPS, the hostname for TLS certificate verification/SNI. This limits DNS-rebinding opportunities between validation and connection.
4. Send only a static **GET**, with no cookies, authorization, system proxy use, scripts, or automatic redirect handling. Redirects must remain on the original exact hostname, use an allowed URL, and stay within the configured hop limit. No second approved host is implicitly authorized by a redirect.
5. Accept only HTTP 200 HTML/XHTML with identity encoding. Reject compressed responses rather than decompressing unbounded data. Enforce byte limits, socket timeouts, and a fetch deadline of at most 30 seconds within the whole-task timeout. Request no subresources.

The **exact** `https://demo.sanyojan.test/products` target instead supplies a bundled fixture through Playwright routing. It has no DNS lookup, listening fixture service, or private-IP exemption. Other paths, trailing slashes, queries, or variants do not receive the fixture shortcut.

The fetcher still performs an outbound request to the approved public server, which can observe its IP and request timing. Application checks supplement, rather than replace, an external egress firewall. Adding allowlisted domains does not add extraction capabilities or make arbitrary sites trusted.

## Browser restrictions and resource limits

- Each execution creates fresh browser/context resources. JavaScript is disabled, service workers are blocked, and downloads are disabled. A route fulfills exactly one approved main-frame GET with supplied HTML; every other page request is aborted.
- Supplied HTML receives a restrictive CSP: scripts, connections, frames, forms, and external resources are denied; only inline styles are allowed. No page-supplied/generated JavaScript is evaluated. Locators read supported visible fields and page title/body text.
- Browser/context/driver teardown is awaited on completion, failure, timeout, and cancellation. Defaults limit tasks to 60 seconds, actions to 15 seconds, HTML to 2,000,000 bytes, records to 100, and simultaneous tasks to two. These are application limits, not a hard process memory/CPU quota.
- There are no clicks, fills, credential handling, persistent browser profiles, screenshots, payments, or privileged browser automation in this MVP. External styles are unavailable, so restricted-render visibility can differ from a live site's rendering.

**Do not claim a hard browser sandbox.** Current Chromium launch calls use Playwright defaults and do not explicitly enable/verify Chromium sandboxing; Playwright may disable it. Running as a non-root user, dropping capabilities, setting `no-new-privileges`, and blocking page requests do not prove renderer containment or stop a browser/process exploit. Compose does not impose an outbound firewall or hard resource quotas. Use host/container network isolation and appropriate process isolation beyond the local demo.

## API and dashboard

- The default backend and Vite dev server bind loopback; Compose publishes only `127.0.0.1:8000`. Trusted hosts and CORS origins are explicitly configured. POSTs carrying an unapproved browser Origin are rejected; requests without Origin are still allowed.
- POST bodies are limited to 16 KiB, five seconds to read, and JSON when nonempty. Mutations are capped at 30 per client IP per minute with bounded in-memory client tracking. Capacity/history bounds return 429. Limits are per process, not a distributed abuse defense.
- Normal app responses use content-type protection, no-referrer, frame denial, cache restrictions, and CSP. The React dashboard renders page text as text. Validation errors avoid echoing raw inputs; unexpected errors expose sanitized messages, and service logs use task IDs/counts/error types rather than raw browser traces.
- There is **no authentication, authorization, tenant separation, or per-task ownership**. Anyone who can reach the API can read history/results/reports and mutate tasks. UUID task IDs are identifiers, not access tokens. Origin/CORS checks do not authenticate non-browser clients.

Remote hosting therefore requires an authenticated TLS gateway, explicit trusted-host/origin configuration, and network isolation/egress policy. Keep SSE streaming unbuffered through that gateway. Do not expose this service directly to an untrusted network.

## Stored data and reports

- SQLite stores instructions, target URLs, task state, events, and result snapshots in plaintext. CSV/JSON reports also remain in plaintext. The default `data/` directory and Compose volume persist across restarts and `docker compose down`; there is no automatic expiry or deletion API.
- No secrets should be entered in task instructions or URLs. Backend `.env` is local configuration and excluded from the Docker build context. `VITE_*` values are public build-time frontend configuration. Neither needs credentials for the demo.
- Reporting accepts canonical UUID filenames and only `csv`/`json`, checks directory containment and symlinks, and replaces files atomically. These checks assume a trusted local filesystem; they are not a hardened defense against another local process concurrently rewriting the directory. Each report is atomic separately, not as a two-file transaction.
- CSV cells/headers beginning with spreadsheet formula prefixes (including leading whitespace) are apostrophe-escaped. JSON preserves the underlying untrusted text. Both formats include provenance and validation warnings; escaping is not a reason to trust data or formulas in downstream tools.
- Missing/invalid values and duplicate records remain visible and flagged. `SUCCEEDED` describes completed processing/report creation, not universal record validity or factual correctness. Download endpoints only serve reports for successful tasks.

Stop the service before backing up or archiving database files and reports together. A fresh workspace or `docker compose down --volumes` removes access to that stored history; the latter deletes the volume. Keep backups private and review extracted content before reuse.

## What remains outside the MVP

Public multi-user hosting, authentication/authorization, a verified browser sandbox, hard CPU/memory quotas, external egress enforcement, distributed job coordination, retention/deletion management, and a supported tool-disabled live-model integration are not implemented. Source boundaries and defaults are described in [architecture](architecture.md); setup/deployment commands and the actual verification environment are in the [README](../README.md).

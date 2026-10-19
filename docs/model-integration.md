# Model integration: current behavior and extension boundary

## What runs today

The shipped application uses **`DeterministicAdapter`**, not a live model. No provider, API key, OmniRush account, or CLI process is required at runtime. `/api/health` reports `planner_mode: "deterministic"`; plans also carry `mode: "deterministic"` and warn that no live AI model is connected.

The public instruction field accepts a limited grammar for reading/inspecting one page and extracting/reporting supported fields. It checks for supported terms, rejects unsupported action/constraint words, and ensures any URL written in the instruction matches the explicit target. It is not unrestricted natural-language understanding. If fields are omitted, inspection uses title/description; extraction/report infer supported field words and otherwise use title/price. Explicit inspection fields must be limited to title/description.

`WorkflowPlanningAgent` always generates the same schema-defined action sequence:

```text
navigate → inspect_title → extract → validate → report
```

All task types create CSV and JSON when execution succeeds. There is no arbitrary model-authored action dispatcher, selector language, code execution, or automatic execution without approval.

## OmniRush investigation

The inspected installation is **OmniRush 2.5.6**. Evidence comes from its installed `package.json`, `README.md`, `omnirush --help`, and `src/engine-prompt.js` (the CLI package, not this application's source).

| Observation | Consequence for Sanyojan |
| --- | --- |
| Help and installed README document `omnirush -p "<prompt>"`: one-shot reply on stdout, errors on stderr, exit 1 when a turn fails. | A one-shot coding-agent interface exists. That alone is not a model-only API. |
| The one-shot path still uses the coding engine with powerful local coding tools. | It cannot be treated as a restricted JSON-understanding service for untrusted HTTP instructions. |
| `src/engine-prompt.js` lists classic `--no-tools`, `--no-builtin-tools`, `--tools`, and related flags as ignored/unsupported by the current engine. Its parser ignores them rather than establishing tool restrictions. | Those flags do **not** disable the coding tools. Do not use them as a security boundary. |
| No supported tool-disabled, model-only programmatic endpoint was established from the inspected interface/documentation. | The application retains deterministic understanding and an injectable provider-neutral contract. No undocumented endpoint is assumed. |

The CLI investigation checked help/docs/source; it does not establish that a restricted live model call was successfully made. The supported stdout behavior must not be confused with safe runtime integration. This repository has no OmniRush subprocess adapter, shell wrapper, inferred provider URL, or API-key environment setting.

**Never invoke `omnirush -p` (or another unrestricted coding CLI) on HTTP-supplied instructions or extracted page text.** A shell-free subprocess, timeout, or “return JSON only” prompt would still leave the coding engine's capabilities available. Schema validation after execution cannot undo tool side effects.

## Existing provider-neutral contract

`backend/sanyojan/planning.py` defines:

```python
class UnderstandingAdapter(Protocol):
    async def understand(self, request: PlanInput) -> TaskSpec:
        ...
```

`TaskUnderstandingAgent(adapter=None)` uses the deterministic implementation by default. `create_app(settings=None, browser=None, adapter=None)` passes a server-injected adapter to the orchestrator. Injection is application composition by trusted server code, not an HTTP parameter or environment-selected executable. No live provider adapter is bundled.

The understanding boundary enforces:

1. `asyncio.wait_for(..., timeout=20)` around the adapter call, independent of the later browser-workflow deadline.
2. Fresh `TaskSpec.model_validate` even when the adapter returns a `TaskSpec` object. Returned data must satisfy the strict schema; unknown properties are forbidden.
3. `objective` of 1–500 characters, `target_url` up to 2,048 characters, supported task type, one to four unique supported fields, and at most ten string constraints. Supported fields are title, price, category, and description.
4. Exact equality of the returned URL and task type to the user's explicit request. An adapter cannot redirect the task or change its type.
5. `PLANNER_RESPONSE_INVALID` / HTTP 502 for schema-validation failure or adapter timeout; `PLANNER_POLICY` / HTTP 400 for changed URL/type. Other unexpected adapter failures follow the sanitized application-error path. Invalid output is not silently replaced with a successful plan.

The current common schema does not compare adapter-selected fields to explicit request fields or enforce inspection-only fields for every injected adapter; the default deterministic adapter enforces its own inspection rule. A future provider implementation must honor explicit fields and inspection semantics rather than rely on those additional checks already existing.

After understanding, **`WorkflowPlanningAgent` constructs the `Plan` itself**, not the adapter. `Plan` validates five numbered, ordered, low-risk actions. Safety validates URL/DNS and plan navigation, and explicit approval precedes execution with another safety check. Free-text objectives and constraints are descriptive data, not executable authority.

The current health/plan mode labels remain deterministic even when an adapter is injected. A genuine live-model feature must update those labels transparently; injecting an adapter alone does not implement live-provider UX or operational configuration.

## Requirements for a future live provider

Before adding a live provider, establish a documented, supported **model-only** interface that has no coding tools or local filesystem/shell authority. Keep that integration in a server-side `UnderstandingAdapter`, returning only `TaskSpec` data. Preserve strict validation, deadlines, explicit URL/type/field intent, fixed planning, safety checks, and approval.

The implementation would also need bounded transport/output handling, cancellation, explicit unavailable/error behavior, honest health/mode reporting, secret handling, and disclosure of what instruction data leaves the machine. Keep raw page content out of understanding prompts; it is untrusted data, not instructions. Credentials must stay out of frontend `VITE_*` values, reports, logs, and committed files.

Meaningful acceptance checks should cover malformed/extra-key output, changed URL/type/fields, deadline expiry, provider failure, prompt-injection attempts, no execution before approval, and unchanged safety enforcement after approval. None of this requires adding a speculative endpoint or invoking the installed coding CLI. Until such an integration is implemented and verified, the deterministic local workflow is the supported path.

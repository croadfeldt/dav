# Building and Extending the DAV Review Console

Audience: an LLM (or developer) tasked with confidently modifying or extending the review console. This document is the **companion** to `../DAV-AI-PROMPT.md`. The parent doc covers why DAV (the framework) is shaped the way it is; this one covers why the **review console** — the operator-facing surface — is shaped the way it is.

The review console is a substantial application in its own right, with its own architectural decisions (Postgres event log, OAuth-proxy auth, GitHub PR integration, model proxy) that don't belong in the parent's engine-focused narrative. Where the parent doc is authoritative on framework concerns (predictable correctness, schema, deployment topology), this doc points up to it rather than restating.

This is a design narrative *and* a requirements doc. Some sections describe built behavior; others describe capabilities we've committed to building. Each section flags which is which.

---

## 1. What the review console is and isn't

The review console is the **operator-facing surface for the DAV system**. It's where humans interact with the framework: browse the spec content under analysis, see what DAV concluded, triage gaps, draft fixes with model assistance, and ship those fixes back to the spec repo as PRs. It also serves as the **integration platform** through which external tools — dashboards, ChatOps, CI gates, custom analyzers — observe and participate in the analysis workflow.

The framing matters. The console started narrower (a spec-content reviewer with drift tracking) and has grown into the operator's home for the whole DAV experience. That growth is deliberate, not accidental, and it changes what belongs here vs. what doesn't.

Things the console **is**:

- An analysis viewer: run summary, per-UC verdict + sample drill-down, gap aggregation across UCs, trend over time.
- A spec-content reviewer: file-by-file approve / needs-work tracking with drift detection. (The original feature; still load-bearing.)
- A model-assisted resolution drafter: take a gap, pull relevant context, ask a frontier model to propose a fix, let a human iterate, ship as a PR.
- A pipeline trigger: kick off DAV runs against the engine's Tekton pipeline (Self-Test tab today; will broaden).
- A configuration surface: switch the spec/corpus repo+branch the running system reads from.
- An integration platform: expose a versioned REST API, emit webhooks on workflow events, accept findings and votes from external tools through scoped tokens.

Things the console **is not**:

- **A CI orchestrator.** Tekton runs the pipeline. GitHub Actions runs the spec repo's checks. The console *triggers* and *observes*; it does not conduct.
- **A plugin runtime.** Third-party code does not execute inside the console. Integrations are separate processes that talk to the API.
- **A replacement for GitHub/GitLab review.** The native PR review process — branch protection, CODEOWNERS, required reviewers, repo CI — is the second gate after in-console review. Reinventing that here is explicitly out of scope.
- **The source of truth on analyses.** The engine produces them; the console persists a copy and renders them. Lose the console's database and you reseed from artifacts; the engine's outputs are still authoritative.
- **A deterministic system.** It runs on top of a non-deterministic framework and orchestrates calls to non-deterministic models. Predictable correctness applies here too: we make variance bounded and visible, not zero.

## 2. Position in the DAV project

DAV's deployment topology (parent doc §4.7) splits engine and inference. The console sits inside the engine layer, alongside the engine package and the MCP server. It is not part of the inference layer.

The data flow at a high level:

```
spec repo ──► MCP server ──► engine (stage 2 agent) ──► analyses
                                                         │
                                                         ▼
                                               console (Postgres) ◄── humans (browser)
                                                         │            ◄── tools (API)
                                                         ▼
                                               proposals ──► spec repo (PRs)
                                                         │
                                                         ▼
                                               next DAV run validates
```

Every arrow is a coordination boundary worth being explicit about:

- **Engine → console** (analysis ingestion). The engine emits analysis YAML; the console persists a structured copy so it can be queried, joined across runs, and drilled into by reviewers. The *engine* is the source of truth; the console's storage is a derived index.
- **Console → spec repo** (PR creation). Proposals leave the system as PRs against the spec repo. The console writes; GitHub/GitLab takes over.
- **Spec repo → engine** (next run). The merged change becomes part of the spec corpus the engine analyzes on the next run. The verdict trend in the console then validates whether the fix worked. This is the closing loop: the console doesn't *judge* fixes; the framework does, and the console renders the result.

This positioning means the console is **a participant in the workflow, not the workflow itself**. Several design decisions follow from that and are worth keeping in mind when adding capabilities (see §10 on locked decisions).

## 3. Four capability areas

The console's surface area falls into four loosely-coupled capability areas. They share infrastructure (auth, database, navigation, design system) but have distinct data models and lifecycles. Mixing them into a single feature surface makes the code unreadable; keeping them clean makes both extension and removal tractable.

### 3.1 Analysis viewing (planned)

Renders DAV's output in views appropriate to different audiences. Four views:

- **Run summary** — top-level dashboard for one run: total UCs, verdict matrix (supported / partially_supported / not_supported), wall time, dissent count. The "is the architecture green this week" question.
- **Per-UC** — for one UC in one run: the header (handle, dimensions, verdict pattern across samples), per-sample table (seed, tool_call_count, tokens, wall_time, verdict, confidence), consensus block (component / capability / gap factors with vote counts), and per-sample drill-down with markdown-rendered rationale.
- **Gap aggregation** — pivot from "what did this UC say" to "what gaps appear across UCs." Group by theme, count flagging samples ("5 of 27 samples flagged atomicity-related concerns"), click through to the originating UC + sample. This is the architect's view.
- **Trend** — verdict pattern delta vs. baseline, per UC. The view that closes the loop after a fix has merged: same UC moving from `2-1 partial` to `3-0 supported` shows the spec change worked. Same UC moving the other way shows a regression.

Trend depends on having ≥2 runs ingested; gap aggregation depends on having gaps with `gap_consensus` annotations populated by verification-mode runs (parent doc §3 on schema). Both are reads against the same underlying store.

### 3.2 Model-assisted resolution (planned)

Take a gap, draft a proposed resolution with frontier-model help, let humans review and iterate, ship as a PR.

The flow:

1. Reviewer opens a gap (entry points: gap aggregation view, per-UC drill-down).
2. Console packages context server-side: the gap object, the UC, the relevant spec files, per-sample rationale.
3. Console calls a frontier model through a server-side proxy. Model returns a proposed change set (diff against existing files, new files, or both), a PR title, a PR body draft, and reasoning.
4. UI renders proposal as side-by-side diff + reasoning. Reviewer can accept, edit inline, or send a follow-up message ("only address §9.1, the change is too broad"). Iteration is a chat thread persisted with the proposal.
5. On approval, console opens a PR via GitHub API (or GitLab equivalent) on a branch named for the proposal. Body includes a traceability footer linking back to run / UC / gap.
6. Console polls PR status (`open` / `changes-requested` / `merged` / `closed`) and displays it.
7. Next DAV run on `main` re-evaluates the UC. Trend view shows whether verdict pattern improved.

Proposals can also resolve with a **rebuttal** outcome: the model concludes the gap is invalid (insufficient evidence, or the spec actually does cover it in a section the analyzer didn't find), the human approves the rebuttal, and the proposal is recorded with no PR. Rebuttals matter — without them, every gap pressures toward producing a PR, which produces noise PRs and trains reviewers to rubber-stamp.

The model proxy lives server-side because API keys cannot ship to the browser. Provider is pluggable via env (`MODEL_PROVIDER`, `MODEL_NAME`, `MODEL_API_KEY_REF`). Anthropic API is the v1 default; on-prem OpenAI-compatible endpoints work the same way. Every model call is logged with reviewer identity, timestamp, prompt size, and response — both for audit and for future cost analysis.

### 3.3 CI/CD coordination (planned)

The console interacts with two existing CI systems and **explicitly does not replace either**.

- **DAV's Tekton pipeline.** The Self-Test tab today triggers `dav` PipelineRuns and lists recent ones. This will broaden into "any DAV run" — corpus runs, focused mini-runs against just the UCs affected by an open proposal, scheduled runs. The console writes a `PipelineRun` and reads the resulting analyses; Tekton handles execution.
- **The spec repo's CI.** When the console opens a PR, the spec repo's CI runs whatever checks it normally runs (linting, link checks, possibly a focused DAV check on the affected UCs). The console may **register a status check** on the PR (`dav-review/proposal-validated` → passes when in-console human review is complete) so the PR can require it, but the PR's *gate* — what blocks merge — is the spec repo's branch protection, not the console.

The `dav-review/proposal-validated` check is the only piece of "review process" the console adds. Everything else (required reviewers, CODEOWNERS, signed commits, conventional commits) is the spec repo's policy.

### 3.4 Integration surface (planned)

External tools observe and participate in the workflow through:

- **A versioned REST API** (`/api/v1/...`) with a published OpenAPI spec at `/openapi.json`. FastAPI emits this for free; the work is treating the spec as a contract and committing to backwards compatibility within v1.
- **Service-account API tokens**, separate from human OAuth identity. Each token has scopes; scopes gate endpoints. v1 scope set: `runs:read`, `gaps:read`, `findings:write`, `proposals:read`, `proposals:write`, `pr:create`, `webhooks:manage`, `admin`.
- **Webhooks** for push integrations: subscribe a URL, receive HMAC-signed POSTs on workflow events. v1 events: `run.completed`, `run.regression_detected`, `proposal.created`, `proposal.pr_opened`, `proposal.pr_merged`, `gap.severity_changed`. Delivery semantics: at-least-once with exponential retry, signed payloads, delivery log persisted for debugging.
- **Idempotency-Key** support on POSTs that mutate. CI systems retry; the console must not duplicate.

§7 covers this in more depth.

## 4. The data model

This section is normative. The console's database shape is load-bearing for everything above; readers extending the console should match it.

### 4.1 Current — the spec-review and use-case-management subsystems

All tables are in `review-console/api/app/schema.sql` (idempotent — safe to re-apply).

**Corpus file review (legacy, preserved for backward compat):**
- **`files`** — corpus file content + SHA. One row per spec file. Updated on corpus reload.
- **`review_events`** — append-only event log. Every review action (review, update, clear) is a row. Captures the file's SHA at the time of review, the reviewer identity, the status, and free-text notes.
- Views: `review_current` (latest non-cleared review per file × reviewer), `review_drift` (review SHA ≠ current file SHA), `file_current_status` (latest review per file).

**Managed use cases:**
- **`managed_use_cases`** — one row per managed UC. UUID (from YAML), title, full YAML text, `lifecycle_state` (draft/ready/in_review/approved/deprecated), tags, created/updated by/at. UUID is extracted from YAML content at write time.
- **`lifecycle_events`** — append-only audit trail for lifecycle state transitions. Every transition is a row: `uc_uuid`, `from_state`, `to_state`, `actor`, `notes`, `created_at`. The current lifecycle state is the `to_state` of the most recent event per UC.

**Named UC sets:**
- **`use_case_sets`** — one row per named set. `id`, `name` (unique case-insensitively), `description`, `created_by`, timestamps.
- **`use_case_set_members`** — many-to-many between sets and UCs. `(set_id, uc_uuid)` PK. Tracks `uc_source` (managed/corpus), `uc_handle`, `uc_path`, `added_by`.

The append-only-events + derived-views pattern is **deliberate** and worth preserving. Reviews and lifecycle transitions are historical records; the current state is a projection. New capabilities should follow the same pattern.

### 4.2 Current — the results subsystem

Analysis outputs are not ingested into Postgres. The API reads them directly from the shared workspace PVC at `/workspace/results/`. This sidesteps the ingestion pipeline and lets the Results tab work with zero additional schema. The trade-off: no cross-run aggregation queries (gap aggregation, trend). That's planned once the corpus is stable enough for multi-run comparison to be meaningful.

The workspace reader is in `api/app/results.py`. It scans `<workspace>/results/` for run directories (format: `YYYY-MM-DDTHH-MM-SSZ-<7char-hash>`), reads `run-summary.yaml` for counts and metadata, and reads `analyses/<uc_uuid>.yaml` for per-UC detail. All three analysis modes (verification, reproduce, explore) are rendered; explore mode surfaces per-sample detail and the variance report.

### 4.3 Planned — the analysis ingestion subsystem

To support cross-run queries (gap aggregation, trend), the console will eventually ingest analyses into Postgres. Required new tables:

- **`analysis_runs`** — one row per DAV run. `run_id`, `mode`, `engine_version`, `consumer_version`, `started_at`, `completed_at`, `triggered_by`, `pipelinerun_name`.
- **`uc_analyses`** — one row per (run, UC). Extracted fields for query: `verdict`, `verdict_pattern`, `has_dissent`. Full analysis JSONB for rendering.
- **`uc_samples`** — one row per (run, UC, sample). Per-sample drill-down data.
- **`uc_gaps`** — one row per (run, UC, gap). For gap aggregation across UCs and runs.

Indexing: gap aggregation hits `(theme)` and `(theme, severity_label)`; trend hits `(uc_handle, run_started_at DESC)`; drill-down hits `(run_id, uc_handle)`.

### 4.5 Planned — the proposal subsystem

For model-assisted resolution:

- **`proposals`** — one row per resolution attempt. Links to `(run_id, uc_handle, gap_hash)`. Status enum: `drafting | under-review | approved | pr-open | pr-merged | rejected | applied | rebuttal`. Holds the canonical proposed change set (current revision), the PR URL once created, and the resolution outcome.
- **`proposal_events`** — append-only event log per proposal. Same pattern as `review_events`. Captures every model turn, every human edit, every status transition. This is the audit trail that tells you who said what when.
- **`proposal_messages`** — the model conversation history. Required for iteration ("the change is too broad" works because the model has prior context).

Hashing the gap (instead of using a gap ID) matters because gaps don't carry stable IDs across runs (parent doc §6.7). Hash on normalized description so a re-run of the same UC against unchanged spec finds the same proposal.

### 4.6 Planned — the integration subsystem

For external tool integration:

- **`api_tokens`** — token hash (never the token), name, scopes, owner, created_at, last_used_at, revoked_at. Tokens issued through an admin-only endpoint, returned exactly once.
- **`webhook_subscriptions`** — URL, secret, event filter, owner, active flag.
- **`webhook_deliveries`** — every delivery attempt: subscription, event, payload, response status, latency, attempt count, completed_at. Retained for ~30 days then aged out.

## 5. Architectural rationale — load-bearing decisions

These are decisions that emerged from the console's evolution and should not be undone without reading the rationale.

### 5.1 Postgres-backed, append-only event log

The console uses Postgres for state and the event-log pattern for mutable concerns (reviews, soon proposals). This is **deliberate**.

Why Postgres: reliable, transactional, joinable, queryable. The data is structured (analyses have a schema), relational (samples belong to UCs belong to runs), and queryable in interesting ways (gap aggregation across runs is a real query). Document stores or KV stores would force application-level joins; we'd reinvent SQL badly. The trade-off is one more thing to operate; the OpenShift deployment runs a Postgres pod alongside the API, which is acceptable for the current single-tenant model.

Why append-only events: audit. Every review, every proposal turn, every status transition is a row. The current state is a view. This costs storage (cheap) and buys reproducibility ("how did this proposal end up in `pr-merged` state? show me the events") plus simple time-travel ("what did the team think on date X?"). Don't shortcut to mutable rows. The discipline pays for itself the first time someone asks an audit question.

**Don't undo:** moving to mutable rows because "events feel verbose" is a regression. The verbosity is the audit trail.

### 5.2 OAuth proxy for humans, API tokens for machines

Two auth paths into the same FastAPI dependency:

- Human requests come through an OAuth proxy sidecar (origin-oauth-proxy in OpenShift) which sets `X-Forwarded-User`. The console trusts that header.
- Machine requests carry `Authorization: Bearer <token>`. The console hashes, looks up in `api_tokens`, validates scopes against the endpoint's required scope.

The auth dependency picks the first present and records `actor_type=human|service` and `actor_id` for audit. Endpoints declare their required scope via FastAPI dependencies. This is **deliberate** — it keeps the auth logic in one place, makes scope-required-by-endpoint declarative and testable, and means the audit log treats humans and machines uniformly.

`ALLOW_ANON_WRITES=true` exists for local dev only and bypasses both paths to a synthetic `anonymous` reviewer. It must be off in production.

**Don't undo:** rolling humans and machines into a single auth path with shared credentials. Service accounts need different lifecycle (issuance, rotation, revocation) than human SSO; conflating them produces both insecure tokens and frustrated humans.

### 5.3 API versioning from day one

The console's API moves to `/api/v1/...` before the integration surface goes live. The current `/api/...` endpoints remain as aliases during transition.

This is cheap to do now, painful later. The moment a Slack bot or a CI job depends on a response shape, drifting the shape breaks production. Versioning + OpenAPI as published contract gives consumers a stable surface to build against.

**Don't undo:** trusting that "we'll version it later when we need to." We need to before, not after.

### 5.4 Webhooks, not a message bus

Push integration is via webhooks (HTTP POST with HMAC signature, retried). Not Kafka, not NATS, not Redis Streams. **Deliberate** — until there are multiple internal consumers with overlapping subscription patterns, point-to-point HTTP is sufficient and operationally trivial. The signal that you need a bus is "we have N internal services all subscribing to the same events." Until then, webhooks are the right tool.

Adopt GitHub's webhook conventions: signed payloads, `X-Console-Event` and `X-Console-Signature` headers, retry policy documented, delivery log per subscription. Subscribers must be idempotent — at-least-once delivery — and the docs must say so.

**Don't undo:** introducing a bus speculatively. It's not free; ops cost is real.

### 5.5 Console is participant, not orchestrator

The console triggers DAV runs, opens PRs, and surfaces status from both. It does not own the *flow control* of either. Tekton owns DAV runs; GitHub owns PRs.

This is **deliberate** for two reasons. First, the existing tools are good at flow control — better than the console will ever be — and the project gets more value from leaning on them than reimplementing them. Second, an orchestration role would put the console on the critical path for everything; if the console is down, the pipeline can't run and PRs can't merge. As a participant, console-down means "no UI, no proposals draft" but DAV still runs and PRs still merge.

**Don't undo:** the temptation to make the console "the brain." It's the dashboard and the assistant. The brain is distributed across Tekton, GitHub, and the engine.

### 5.6 GitHub/GitLab review is the second gate, DAV re-runs are the third

Three review layers compose:

1. **In-console**: human reviews model's proposed edit. Scope: is this proposal sensible.
2. **In GitHub/GitLab**: native PR review. Scope: is this change merge-ready (CODEOWNERS, repo CI, branch protection).
3. **Next DAV run**: framework re-evaluates the affected UC. Scope: did the merged change actually close the gap.

The third is the quietly-load-bearing one. Without it, "did the fix work" is a human judgment call subject to the same biases that produced the gap. With it, the trend view validates the fix mechanically.

**Don't undo:** adding a fourth review layer in the console (e.g., a second human approver before PR creation, or a workflow rule engine for "if severity = critical require two reviewers"). That's where extensible systems go to die. Any policy of that shape belongs in the spec repo's branch protection, not the console.

### 5.7 Single SPA file (for now)

The UI is a single 1400-line `index.html` served by NGINX. No build step. It works because the surface is small, the design system is consistent, and a no-build deploy is operationally simple.

This will not survive proposals + integrations. The estimate is ~2400 lines once those land, plus a markdown renderer and a diff viewer (third-party deps). The exit ramp is documented in §8.

**Don't undo:** preemptively splitting to a build before the size demands it. The current single-file form has paid for itself many times over in deploy simplicity.

## 6. The agent and the prompts (proposal subsystem)

(Planned — counterpart to parent doc §6 on the analysis agent.)

The proposal flow runs a frontier model on a different prompt contract than the analysis agent. Where the analysis agent is constrained to produce structured YAML conforming to a published schema, the proposal model produces a change set + reasoning + PR metadata.

Prompt structure:

- **System**: role (architecture-fix proposer), constraints (minimal diffs scoped to the gap, traceability footer required, refuse vague gaps explicitly), output format (JSON with `summary`, `reasoning`, `proposed_changes[]`, `pr_title`, `pr_body`, `outcome` enum).
- **User turn 1**: gap object (from `uc_gaps`), originating UC, relevant spec file contents (limited window to control tokens), per-sample rationale that flagged the gap.
- **Subsequent turns**: human follow-ups during iteration. Conversation persisted in `proposal_messages`.

Refusal as a first-class outcome: if the gap is too vague to act on, the model should say so explicitly and the proposal records `outcome: insufficient_information` rather than fabricating a fix. Same for rebuttals — the model can conclude the gap is invalid and produce a recorded rationale rather than a PR.

Token budgets per proposal are bounded. A stuck conversation does not burn unbounded cost.

**Don't undo:** removing the structured-output constraint to "let the model write naturally." The diff format and PR metadata are what make the rest of the flow work.

## 7. The integration surface (deeper dive)

§3.4 introduced this; this section captures the rationale.

### 7.1 OpenAPI as a contract

FastAPI emits OpenAPI for free. The work is treating it as a contract:

- Every endpoint has a description, every model field has a description.
- Response shapes are precise (no `dict[str, Any]` in v1 endpoints).
- Examples on requests and responses where they aid comprehension.
- Breaking changes require a version bump (`/api/v2/...`); v1 stays alive in parallel for a deprecation window.

If consumers will generate clients from the spec, the schema bar is high. v1 commits to that bar.

### 7.2 Scopes, not roles

Tokens carry scopes. Endpoints declare required scope. This is more flexible than role-based access (a token can have any combination of scopes; new scopes don't require user-model changes) and more explicit than path-based ACLs (the scope appears in code where the endpoint is defined).

v1 scope set is intentionally small; add more only when an integration needs them. Examples of scopes that might come later: `runs:trigger` (separate from `runs:read` because read-only dashboards don't need to start runs), `proposals:approve` (separate from `proposals:write` because some integrations might draft proposals but not approve them).

**Don't undo:** building a role hierarchy on top of scopes ("admin role = these 5 scopes"). A role is just a set of scopes; if you need it, store it in the integration's own configuration, not in the console's auth.

### 7.3 Webhook semantics, cloned from GitHub

GitHub solved webhook UX. Clone the answer:

- Subscribe a URL, get an HMAC secret returned once.
- POST events with `X-Console-Event` and `X-Console-Signature`.
- Retry on 5xx with exponential backoff over ~10 minutes; final failure persisted in `webhook_deliveries`.
- Delivery log is queryable per subscription (debugging is the second-most-common support question after "why isn't my token working").

### 7.4 Idempotency keys on writes

POSTs that create resources accept `Idempotency-Key`. The same key + same body returns the same result; same key + different body returns 409. This solves the CI retry case ("network blipped, retried, now we have two proposals") without making CI authors implement dedup themselves.

Retention: idempotency records live 24 hours. Long enough for reasonable retries, short enough to bound storage.

### 7.5 What's deliberately not in scope

- **Plugin runtime.** Third-party code does not execute inside the console.
- **Workflow customization in-console.** "User-configurable approval rules" → use webhooks + write your own logic.
- **GraphQL.** REST + OpenAPI is sufficient. Reconsider only on real over-fetch evidence.
- **Message bus.** Webhooks until the data says you need more.
- **Multi-tenant API.** Single tenant matches the rest of DAV (parent doc §9.4).

**Don't undo:** any of the above without first confirming the simpler answer doesn't work. They're tarpits.

## 8. Repository structure rationale

```
review-console/
├── README.md                     operational quickstart (run locally, deploy)
├── AI-PROMPT.md                  this file — design narrative
├── api/
│   ├── Containerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py               FastAPI app, routes, lifespan
│       ├── schema.sql            Postgres schema, idempotent
│       ├── corpus_loader.py      walks the spec corpus into files table
│       ├── sources.py            spec/corpus repo configuration
│       └── validations.py        Tekton PipelineRun integration
└── ui/
    ├── Containerfile
    └── index.html                single-file SPA (current; will split)
```

Notes:

- The single-file SPA is current. It will split into a Vite-based build with components when proposals land — that's when the size and the diff-viewer / markdown-renderer dependencies push it past the no-build threshold. The split is documented in §10 as not-yet-built.
- `schema.sql` applies idempotently on startup with an advisory lock (parent doc-equivalent rationale: race-safe under concurrent pod restarts). New tables go here; do not add separate migration tooling unless the project moves to a stage that genuinely needs it.
- `validations.py` is a stable name for what's now Tekton-only; broaden carefully if other CI systems show up.

## 9. Testing approach

Currently minimal. The console has no test suite. This is a gap and worth being honest about.

Target shape:

- **API tests** with `httpx` against a containerized Postgres. Fixtures seed the DB with realistic data; assertions are over response shapes and DB state. The test suite runs in CI before container build.
- **Schema migration test** — apply `schema.sql` against an empty DB, against a populated DB, against a partially-applied schema; all should succeed idempotently.
- **Integration tests for proposals** — mock the model proxy at the boundary; verify orchestration, persistence, conversation continuity.
- **UI tests** — Playwright if and when the SPA splits to a real build. Until then, manual.

The test gap is real and will need to be closed before the integration surface ships. Without tests, the API contract drifts under refactor; we'd ship breakage to integrations within weeks.

## 10. Things that are deliberately compromised

These are decisions that aren't great but are intentional:

- **Single-file SPA.** Will hit a complexity wall when proposals + integrations land. Documented exit ramp; don't preemptively split.
- **Polling for PR status, not webhooks from GitHub.** v1 polls every 30s for open PRs. Webhooks are better but require a public endpoint and webhook configuration on the spec repo. v2 work.
- **No webhook delivery debugging UI in v1.** Operators will read `webhook_deliveries` directly via psql for the first iteration. UI follows once the support pattern is clear.
- **No quota enforcement on proposal drafting in v1.** Just per-user token budget caps in the model proxy. Real quotas (per-user, per-day) need a usage table and a policy; deferred.
- **No assertion-mode rendering.** The analysis schema (parent doc §6 ref to spec 07 §10) supports assertion UCs; the console renders them generically until there's a real consumer. Acceptable.
- **Markdown rendering via CDN.** First runtime frontend dep. Worth the trade for now; revisit when SPA splits.
- **Tekton-only pipeline integration.** The console assumes Tekton. Other CI runners (GitHub Actions, GitLab CI, Jenkins) would each need their own adapter in `validations.py`. Single-CI-runner is an honest constraint.
- **Single GitHub App / PAT for PR creation.** All proposal-PRs come from one bot identity. Multi-identity (per-reviewer co-author trailer is fine; per-reviewer commit identity is not) is not supported. Adequate for v1; revisit if reviewer attribution becomes important.

## 11. How to extend

In order of difficulty and decreasing change-radius:

### Trivial (no schema change, no API contract change)

- Add a UI tab — drop a `<section class="view">` into `index.html`, add a tab button, wire `switchView`. Style follows the existing design tokens.
- Add a read-only endpoint for an existing table — copy an existing GET handler, add it to the v1 router.

### Small (touches schema or adds a new endpoint)

- Add a new event type in `proposal_events` or `review_events` — append a string to the action enum, document it, emit it from the relevant transition.
- Add a new webhook event — register the event name, emit it from the workflow point, add to the documented event list.

### Medium (touches the proposal flow)

- Add a new proposal status — extend the enum, define the transitions, update the events that produce it, update the UI rendering.
- Add a new resolution outcome (e.g., `outcome: backport_to_other_repo`) — touches the model prompt's output schema, the persistence shape, the UI.

### Large (touches integration auth or model proxy)

- Add a new model provider — the proxy abstraction is provider-agnostic; implement the new provider's call shape, plumb the env vars, document.
- Add a new auth method (e.g., mTLS for a specific high-trust integration) — the auth dependency takes a new branch. Document the trust model for the new path.

### Largest (architectural shifts)

- **Split the SPA into a build.** The shape: Vite + a small set of React or Solid components, kept simple. Same NGINX deploy with the build artifact. See §10 for trigger conditions.
- **Replace polling with webhooks from GitHub.** Adds an inbound webhook receiver, a public-facing endpoint, secret management. v2 work.
- **Add a second consumer.** The console is single-tenant by parent-project decision (parent doc §9.4). Multi-tenancy is a parent-project concern, not a console-only one.

## 12. Locked decisions (don't undo without strong reason)

For convenience, the consolidated list:

- **Postgres + append-only event log** for state. No mutable rows for review or proposal history.
- **OAuth proxy for humans, API tokens for machines.** Single auth dependency, two paths.
- **`/api/v1/...` versioning, OpenAPI as published contract.** No drift in v1 response shapes.
- **Webhooks, not a message bus.** Until the data says otherwise.
- **GitHub-style webhook semantics.** Signed payloads, retry policy, delivery log.
- **Idempotency-Key on POSTs.** All mutating endpoints support it.
- **Console is a participant, not orchestrator.** Tekton runs DAV; GitHub gates merges.
- **Three review layers, no fourth.** In-console → PR review → next DAV run.
- **Refusal as a first-class proposal outcome.** Vague gaps don't get fabricated fixes; rebuttals are valid resolutions.
- **Scopes, not roles.** v1 scope set kept small; expand on demand.
- **Plugin runtime out of scope.** Integrations are external processes.
- **Tekton-only CI integration in v1.** Multi-runner is future work.
- **Single GitHub App / PAT identity for PRs.** Multi-identity not supported.
- **Schema applied idempotently on startup with advisory lock.** No separate migration tooling.

## 13. What's built and what isn't

### Built (as of v0.8.x)

- **Runs tab** — PipelineRun list + trigger with full parameter control (pre-populates from `/api/sources` + UC subpath auto-detect). User-supplied session name + curated category + description per run. Cluster-wide energy chip in the top bar (`/api/runs/stats`: total kWh, last 24h, last 7d, run count). **Click any row** for a live run-detail drawer with **four selectable layouts** (detailed / stacked-tails / side-by-side / prompts-dominant) — picker chip in the drawer header persists choice in localStorage, with explicit "set current as default". Drawer shows:
  - Session metadata + finalized energy/token stats on terminal phase
  - Live per-UC progress counter + tri-color bar (from `<run_dir>/run-progress.yaml` written by the engine after each UC)
  - Tekton task ladder with inline failure block + log-tail expander on failed tasks
  - **Live prompts/responses panel** tailing `<run_dir>/turns/<uuid>.seed-<N>.jsonl` (engine emits per-turn JSONL with kind=start/response/tool); byte-offset cursor for incremental tail polling
  - AMD per-GPU tiles + vLLM aggregates (running/waiting/KV/throughput/TTFT); session token totals delta from persisted `run_sessions.baseline_*_tokens`
  - Freshness indicator + value-change flash on metric tiles
- **Results tab** — workspace PVC browser; run summary, per-UC verdict + findings + gaps, explore-mode per-sample variance. Three-panel split.
- **Use Cases tab** — managed UC CRUD (YAML editor), corpus UC browsing, lifecycle state machine (draft/ready/in_review/approved/deprecated), transition buttons, lifecycle audit history, set membership display.
- **Sets tab** — named UC sets, member add/remove, run-set scoping, bulk promote (all members from state A → B in one transaction), per-set export.
- **Import/export** — `.tar.gz` / `.zip` / `.tar` archives; archive structure encodes lifecycle stage and set name for round-trip; import creates/updates UCs and auto-creates sets.
- **Config tab** — three source kinds: spec repo, corpus repo, **inference (endpoint + model)**. Inference panel includes a Test button (validates `/models` + model presence) and Apply auto-validates first with an "Apply anyway" override path. Model selector dropdown auto-populates from the live endpoint with free-text override fallback. Corpus panel surfaces the auto-detected UC subpath.
- **Theming** — three palettes (Amber / Slate / Solarized) × light/dark modes, with Auto following `prefers-color-scheme`. Persisted in `localStorage` with an early-init script in `<head>` to avoid FOUC.
- **Cluster metrics integration** — `app/metrics.py` async Prometheus client queries `thanos-querier` via the API SA bearer token + service-CA bundle. Curated `snapshot()` runs 12 PromQL queries in parallel (AMD per-GPU + vLLM aggregates + cumulative token counters). Surfaced via `/api/metrics/snapshot`. Requires `cluster-monitoring-view` ClusterRoleBinding (provisioned by the role) and the `dav-review-service-ca` ConfigMap (annotated for `service.beta.openshift.io/inject-cabundle`).

### Not yet built (in rough priority order)

- **Time-series GPU + inference graphs** — the drawer renders point-in-time snapshots. For historical context ("throughput over the last hour"), the API would query Prometheus over a `[range]` and return time-series; UI would render a sparkline per tile. Defer until a single-snapshot view is no longer enough.
- **NL-assisted UC authoring** (task #29) — chat UC editor with model assist. Configurable model endpoint per Config tab: (a) DAV's primary inference, (b) an MCP-discovered model, or (c) an external endpoint the user enters with API/OAuth tokens. New `dav-source-uc-assist` ConfigMap + secrets plumbing.
- **FMS scraper Pi → smartwatch** (task #27) — separate project; FRC FMS published pages → live match stats to a watch + other consumers.
- **Analysis ingestion into Postgres** — cross-run gap aggregation and trend views require `analysis_runs`, `uc_analyses`, `uc_samples`, `uc_gaps` tables (see §4.3). The Results tab currently reads directly from the workspace PVC; this works but can't do multi-run queries.
- **Gap aggregation + trend views** — depend on ingestion above.
- **Model proxy + proposal subsystem** — server-side model calls, conversation persistence, in-console diff review (§4.5).
- **GitHub PR integration** — create PR from a proposal, poll status, surface in UI. The `POST /api/use-cases/{uuid}/promote` endpoint is not yet implemented; it needs a GitHub token Secret in the dav namespace.
- **Service-account tokens** + scopes + admin issuance UI (§4.6).
- **Webhook subsystem** — subscriptions, delivery, retry, log (§4.6).
- **API v1 cutover** — version existing `/api/...` endpoints, publish OpenAPI as contract.
- **Test suite** — API tests, schema migration test.
- **SPA build split** — when `index.html` complexity demands it.
- **PipelineRun ↔ results hard-link** — run directories don't embed the PipelineRun name today; correlate by timestamp.

Dependency order: ingestion → aggregation/trend → proposals → PRs → tokens → webhooks → version cutover → tests. Tokens and webhooks can run in parallel with PRs. Tests should land alongside each subsystem, not after.

## 14. Final thought

The review console is an opinionated application growing on top of an opinionated framework. Most of the opinions came from observing what wanted to happen as features landed and protecting the project from accidental complexity (an in-console workflow rule engine, a plugin runtime, a message bus, GraphQL — each of these has been considered and pushed back against here for reasons recorded above).

When you find yourself wanting to relax an opinion, you may be right. Check the corresponding section first; if the rationale doesn't apply anymore, document why and propose the change. If it does, the opinion is doing its job — the friction you're feeling is the architecture refusing to be misused.

The two most important orientation points for anyone extending the console:

1. The console is a **participant** in DAV's workflow, not its conductor. The framework is the source of truth on analyses; GitHub is the source of truth on merges. The console renders, assists, and routes — it does not decide.
2. The integration surface is a **published contract**, not an implementation detail. Treat it accordingly from the moment it goes live.

Read this document, then `../DAV-AI-PROMPT.md` for the framework's design, then `../specs/07-analysis-output-schema.md` for the data shape the console renders, in that order. Modify second; rebuild only if you have a fundamentally different goal.

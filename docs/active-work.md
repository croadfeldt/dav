# DAV — active work (session checkpoint 2026-06-08)

Resume scratchpad for the current batch of asks. Survives chat-context loss.
Repo: `/Users/chris/git/dav`. Big single files: `review-console/api/app/main.py`
(~466KB), `review-console/ui/index.html` (~725KB), `review-console/api/app/schema.sql`.
Design doc: `docs/review-console-design.md` (keep in sync per house rule).

## F1 — Additional-context text section for UC creation (esp. bulk)
**Status: backend DONE, UI only remaining.**
- Backend already supports it: `UCBulkExtractIn.context` (main.py:3568, max 4000) →
  endpoint `POST /api/use-cases/bulk-from-text` (main.py:3743) → `uc_assist.extract_bulk(context=…)`
  (uc_assist.py:299/318) injects "Additional context:\n…" into `_BULK_SYSTEM_PROMPT`.
  Single-UC assist path also has `context` (uc_assist.py:159/180).
- **TODO:** add an "Additional context" `<textarea>` to the BULK UC IMPORT MODAL
  (index.html ~2251, "M12a / ADR-008") and pass its value as `context` in the
  bulk-from-text POST body. Check whether single-UC create already shows a context
  field to mirror its styling/labeling. Keep it optional, ≤4000 chars.

## F2 — "Test evaluation" → run the single open UC directly + relabel
**Status: not started.**
- Today `testRunUC(uuid, ucPath, title, branchOverride)` (index.html:7051) builds a
  one-UC filter then calls `openNewRun(...)` — which opens the New Run config page
  (the "full use case run documentation" the user does NOT want).
- Runs are actually triggered by `submitNewRun()` (index.html:5601) → `POST /api/runs`
  with payload incl. `uc_handles`/`uc_uuids`/`managed_uc_uuids` from `_pendingRunFilter`,
  `selection_mode:'individual'`, model/endpoint via `_resolveEndpointModel`, defaults.
- **TODO:** make the button **submit the run immediately** for just the open UC
  (build the minimal /api/runs payload from the UC's filter + current project defaults,
  POST, then jump to the run detail) instead of opening the modal. Keep a path to the
  full config for power users (maybe shift-click or a small "configure…" affordance).
- **Relabel** the two buttons `▶ Test evaluation` (index.html:6806 corpus/managed,
  index.html:8362 managed-direct) — proposed new text **"Run this UC as well"**
  (⚠ CONFIRM wording with user — odd for a single-UC action; they asked for it
  literally). Update the `title=` tooltips at 7076-7077 too.
- Batch sibling: `#ucSelTestBtn "▶ Test selected"` (index.html:1190) — leave unless asked.

## F3 — Audit log (who did what + login/logout/timeout)
**Status: not started. Largest item.** Related: task #78 (login history in Users & Roles).
- Auth surface: `POST /api/auth/login` (main.py:2526, sets session cookie ~2377),
  `POST /api/auth/logout` (main.py:2544, deletes cookie), `/api/auth/sso` (2551),
  `/api/me` (1472). Sessions: `local_auth.py` (`make_session`/`read_session`,
  HMAC-signed, expiry baked into token → "timeout" = token expired). LDAP path:
  `ldap_auth.py`. Auth middleware around main.py:855-945.
- **Design (proposed):**
  - `audit_log` table in schema.sql: id, ts, actor_email, actor_source(local/ldap/sso),
    project_id (nullable=global), action (verb), object_type, object_id, summary,
    ip, user_agent, outcome(success/denied/error), detail JSONB.
  - **Action capture:** a small helper `record_audit(conn, request, action, …)` called
    at mutating endpoints (run trigger, UC create/approve, RBAC change, repo/cred edit,
    project change, etc.). Prefer an explicit helper over blanket middleware so we log
    intent + object, not just method/path. Optionally a middleware fallback for
    coverage of all non-GET 2xx.
  - **Auth events:** record login(success/fail), logout, and session-timeout (emit when
    a request arrives with an expired/invalid session cookie that had been valid — detect
    in the auth dependency). Capture ip + user_agent.
  - **UI:** an "Audit" view (platform-admin = all; project scope = members' actions),
    filter by actor/action/object/date; reuse the Users & Roles area (#78). RBAC-gate it.
- **Open Qs for user:** retention window? per-project visibility rules? include read
  actions or mutations + auth only? PII/IP storage ok?

## F4 — DISCUSSION: DAV for consulting priorities / capabilities / roadmaps
**Status: discussion, not code.** User intuition: DAV could derive priorities/
capabilities/roadmaps for consulting engagements — strong correlation to DAV's existing
**dual-pipeline product goal** (see memory project_dav_product_goal: "UC-driven dual
pipeline — architecture gap analysis + engineering capability roadmap; single-source =
analysis, two projections"). Engagement artifacts (transcripts, requirement docs) →
bulk-extract UCs (F1!) → gap analysis vs a target spec → capability/priority roadmap.
This may reshape feature priorities. Capture the conversation outcome here.

## F5 — Graphical UC ↔ capability map (bidirectional)
**Status: CONFIRMED (user agreed 2026-06-08); bidirectional.** Visualize use-cases ↔
capabilities both directions: pick a UC → its demanded capabilities; pick a capability
→ the UCs that demand it. Doubles as a **F4 consulting deliverable / "second
projection"** (gap + roadmap made legible at a glance).
- **Data already exists** (mostly a viz task): `uc_capabilities` (bipartite UC↔capability
  edges, "UC demands capability X"; schema.sql:234) and `uc_capability_deps`
  (capability→capability deps; schema.sql:256). Endpoints:
  `/api/analysis/capability-density` (main.py:6206, demand per capability) and
  `/api/analysis/foundational-capabilities` (main.py:6275, dependency ranking +
  leverage). Analysis libs: `capability_density.py`, `capability_graph.py`.
- **TODO:** likely one new endpoint returning the bipartite edge list (uc_uuid ↔
  capability_id) for a run/set, then a UI graph/matrix. Options: force-directed
  bipartite graph, or a UC×capability matrix/heatmap (demand count = cell weight),
  with click-through both ways. Size capability nodes by demand density; flag
  foundational ones (high leverage). Scope to a run/set (data is per-run).
- **Open Qs:** graph vs matrix as primary view? scope to current Set or cross-run
  aggregate? include capability→capability dep edges in the same view or a layer toggle?

## F4 outcome — Holistic vision & pillar expansion (foundational, 2026-06-08)
See `docs/holistic-vision.md` (mirrored in dcm/ + udlm/). DAV generalizes from
"DCM gap-analysis" to **AD (Architectural Design) mode** — validate any spec/plan can
support a UC, and if not, why + how. Three pillars realize UCs: **Platform** (built =
AD mode), **People/Process** (new), **Enablement** (new). Same engine, different
evaluation target + ingestion per pillar. Consulting flow = consume our existing
**assessment outputs** → cross-pillar gap analysis → strategy + roadmap, all anchored
to **customer-agreed outcomes/execution/operational details**.

**Pillar-expansion backlog (feasibility-gated, not yet scheduled):**
- **F6 — Generalize evaluation target** ("spec" → assessment target; current-state vs
  target/reference; selectable per pillar). Prereq for People/Process + Enablement.
- **F7 — Assessment-output ingestion** (consume our existing assessments: automation
  strategy, platform, hybrid cloud, AI capability). The primary new ingestion path.
- **F8 — Value Stream Mapping ingestion** (People/Process current-state; flow/waste/
  handoffs → gaps).
- **F9 — People/Process pillar view** (evaluate UCs vs org/process current-state).
- **F10 — Enablement pillar view** (adoption/change/operationalization readiness).
- **F11 — Prioritization lens** (business value × effort × risk × time-to-value,
  elicited not hallucinated) layered on existing foundational-leverage ranking.
- **F12 — Report/export projection** — the client-facing deliverable.
- **AI strategy (standalone)** — develop our AI capability/strategy deliberately; it is
  both an assessment lens (F7) and its own strategy artifact. *(user action item)*

## F7 detail — assessment ingestion (decisions 2026-06-08)
- **Pilot = Automation assessment/strategy** (most data + usage). Data volume order:
  automation > hybrid-cloud > AI. **A generalized DCM strategy is the SUPERSET** across
  all of them.
- **Capability catalog ↔ DCM Taxonomy** — independent catalog, normalized TO the
  taxonomy, **back-fills the taxonomy where gaps exist** (catalog drives taxonomy
  completeness). Taxonomy = normalization authority (form); catalog = living inventory
  (substance). DCM superset → sub-domains {automation, hybrid-cloud, AI}, pillar-namespaced.
  Resolves the free-form-capability dependency. **Full design + schema sketch:
  `docs/capability-catalog-design.md`** (the keystone — build first).
- **WORK/PERSONAL BOUNDARY (critical):** real assessment output is **work-confidential**
  — it must be parsed **inside** the work env (Chris will move/run DAV inside for that).
  DAV stays OSS in personal. So **here we build the GENERIC mechanism only**: the
  assessment schema, a parser/mapper **interface** (dispatch by assessment type), the
  assessment-target abstraction, and a **synthetic/example** automation fixture. The
  real per-format parsers + confidential data are a drop-in **inside**. No confidential
  data in the OSS repo. See [[feedback_account_split]].
- **Fundamentals buildable here now:** (1) canonical capability catalog seeded from the
  DCM taxonomy (keystone); (2) `assessments` + `assessment_findings` schema
  (pillar/domain-aware, catalog-anchored); (3) generic import framework + type-dispatch
  parser interface + synthetic automation fixture; (4) map findings → UCs/capabilities/
  gaps so the existing engine consumes them; (5) F6 evaluation-target generalization.

## Suggested build order
F1 (small, UI) → F2 (medium, UX) → F3 (large, schema+API+UI) — but F4 discussion may
re-rank. Update `docs/review-console-design.md` + version on each shipped feature.

# Customer Demand & Compatibility-Aware UC Dedup — Design

**Status:** Phase 1 shipped (2026-06-12); Phases 2–4 in design.
Living doc — build to it, update it after changes. Paired with the requirements inventory in
`review-console-design.md` (which links here).

## Why

Use cases arrive repeatedly — the same need, asked for by different customers, sometimes the same
customer many times. Today every ingested UC becomes its own row, which (a) clutters the catalog with
near-duplicates and (b) **poisons prioritization**: "I ingested the same UC for the same customer 10
times" makes it look 10× more important than it is. We want the opposite: ingestion should *recognize*
a UC it already has, attribute the new request to a customer, and let **genuine multi-customer demand**
raise importance — without letting one noisy customer inflate it.

The payoff is a UC corpus where **importance reflects distinct-customer demand (multi-tenancy)**, and
where ingestion is a deliberate disposition (skip / import / bump / adapt), not blind duplication.

## Core paradigm

### Customer is a first-class entity, orthogonal to Project (M:N)
- A **Customer** is not a property of a project. Customers and projects are **many-to-many**:
  - **DCM** — a single project, **many customers**.
  - **Assessments** — primarily **customer-focused**, but a customer spans **many projects**.
- So a Customer is a **platform-level entity** (like Project), associated to projects via a join.
  A UC's demand is attributed to customers; the **same customer can drive demand across projects**, and a
  project's importance picture is the union of its customers' demand.

### Demand = DISTINCT customers (not raw count)
- Importance signal = **COUNT(DISTINCT customer)** for a UC = how many tenants want it = the
  multi-tenant weight. Raw total requests is informational only.
- Re-logging the same customer is allowed (it's real repeat-demand signal and useful history) but does
  **not** increase the multi-tenant count → no poisoning.
- `multi_tenant` = distinct_customers > 1 → a "higher importance" marker that should feed roadmap weight
  alongside `priority_score`.

### Compatibility-aware dedup on ingest
When an incoming UC resembles an existing one, ingestion offers a **disposition** rather than always
creating a row. Two scores drive it:
- **Similarity** — *are these talking about the same thing?* (semantic; see Phase 2).
- **Compatibility** — *can the existing UC absorb this one as-is, or only with adaptation?* (a deeper,
  structured/LLM judgment over scenario/dimensions/success-criteria; see Phase 3).

Dispositions (per incoming UC, warn-and-confirm before the run launches):
1. **Skip** — it's a duplicate we don't want; drop from the run.
2. **Import as-is** — actually distinct; ingest as a new UC.
3. **Bump existing** — same requirement, another customer → **log a customer request on the existing UC**
   (raising distinct-customer importance), don't create a new row.
4. **Increase & adapt existing** — close but not fully compatible → bump demand **and** adapt the existing
   UC to also cover the variant (merge/extend scenario/criteria), keeping one canonical UC.

> Per Chris (2026-06-12): "skip, import as is, increase existing priority, increase and adapt existing if
> it's close but not entirely compatible … perhaps a compatibility score." And: importance must come from
> *distinct customers* so repeat asks from one customer don't poison it.

## Data model

**Shipped (Phase 1):**
- `managed_use_cases.customer_requests INTEGER` — denormalized **total** requests (kept in sync from the
  log; index `idx_managed_uc_demand`).
- `uc_customer_requests(id, uc_uuid→managed_use_cases ON DELETE CASCADE, project_id, customer TEXT,
  source, note, created_by, requested_at)` — one row per attributed request. **Importance =
  COUNT(DISTINCT customer); total = COUNT(*).** `customer` is free text **for now** — the forward-compatible
  seam to the Customer entity (Phase 2 adds `customer_id` and backfills from the text).

**Planned:**
- `customers(id, name, slug, …)` — platform-level entity (mirrors `projects`).
- `customer_projects(customer_id, project_id)` — the M:N association.
- `uc_customer_requests.customer_id BIGINT REFERENCES customers(id)` — replaces/augments the text key
  (text retained as the import-time raw label; resolved to an entity).
- `uc_embeddings(uc_uuid, project_id, content_sha, model_id, dim, vector, updated_at)` — semantic index
  (Phase 2). Re-embedded when `content_sha` changes.
- Assessments gain a `customer_id` (Assessments are customer-primary).

## API

**Shipped (Phase 1):**
- `GET  /api/use-cases/{uuid}/customer-requests` — the demand log + rollup
  (`total_requests`, `distinct_customers`, `multi_tenant`, `by_customer[]`, `requests[]`).
- `POST /api/use-cases/{uuid}/customer-requests` `{customer, source?, note?}` — log a request (syncs total).
- `DELETE /api/use-cases/{uuid}/customer-requests/{rid}` — correct a mis-attribution.
- `GET /api/use-cases` now returns `customer_requests` + `distinct_customers` per UC; `?sort=demand`.

**Shipped (Phase 2a):** customer CRUD (`/api/customers`), associations (`/api/customers/{cid}/projects`,
bulk `/api/customer-projects`), and **per-customer members** (`GET/POST/DELETE /api/customers/{cid}/members`
— grant/revoke `customer-viewer`/`customer-edit`, escalation-guarded, gated by `customer.edit`);
`GET /api/use-cases?customer_id=` filter.

**Planned:**
- LDAP/FreeIPA group→role auto-sync for the `<scope_type>.<entity_slug>.<level>` convention.
- `POST /api/use-cases/similar` / embeddings refresh (Phase 2).
- `POST /api/use-cases/compatibility` — score an incoming UC vs candidates (Phase 3).
- Ingest preflight returns near-duplicate clusters + suggested dispositions (Phase 4).

## UI

**Shipped (Phase 1):**
- UC list **demand badge** `👥 <distinct>·<total>` — highlighted when multi-tenant; tooltip explains.
- UC detail **Customer demand** panel: distinct/total rollup, per-customer chips, a request log with
  attribution + delete, and a "+ Log request" form. Logging refreshes the list badge.

**Planned:**
- Customers admin (platform): create customers, associate to projects (M:N), per-customer demand view.
- `≈ similar` badge + "similar only" filter; a cluster/merge review surface.
- New-Ingestion **warn-and-confirm**: show near-duplicate clusters with similarity + compatibility, pick a
  disposition per incoming UC (skip / import / bump / adapt).
- Demand feeds roadmap weight (multi-tenant ⇒ importance).

## Phased plan

- **Phase 1 — Demand foundation (SHIPPED).** Per-customer request log (text customer), distinct-customer
  importance, list badge + detail panel, `sort=demand`. Forward-compatible with the Customer entity.
- **Phase 2 — Customer entity + semantic index.** `customers` + `customer_projects` (M:N); migrate
  `uc_customer_requests.customer` → `customer_id`; embeddings index + `≈ similar` surface. *(Decision: chosen
  similarity method = semantic embeddings — needs an embeddings endpoint; the Add-Model probe now makes one
  easy to register.)*
- **Phase 3 — Compatibility score.** Evaluate incoming-vs-existing compatibility (structured dimension/criteria
  comparison and/or an LLM judgment) → compatible / close-needs-adaptation / distinct.
- **Phase 4 — Dedup-on-ingest disposition.** New-Ingestion warn-and-confirm with skip / import / bump / adapt;
  "bump" logs a customer request on the canonical UC; "adapt" merges the variant.

## Decisions (Chris, 2026-06-12)
1. **Customer attribution at ingest — BOTH:** prefer a customer declared on the source (UC YAML / source
   repo / assessment's customer); **fall back to a run-level customer selector** in New Ingestion when the
   source carries none.
2. **Compatibility method — HYBRID:** a fast deterministic compare of scenario dimensions / success-criteria
   for a cheap first signal, then an **LLM judgment** for the close calls → verdict
   `compatible | needs-adaptation | distinct` + rationale. Uses the in-cluster model.
3. **Customer admin — new RBAC + new domain:** add privileges **`customer.view`** / **`customer.edit`**
   (wired through the RBAC matrix like other privileges) and a **new left-rail "Customers / Projects" domain**
   with two top sub-tabs (**Customers**, **Projects**) — relocating today's Config→Platform project admin into
   it. Customers are platform-level with M:N project associations. (Broader "dynamic management model" is being
   designed by Chris in a separate thread — keep this slice aligned with that.)

### Still open
4. **Adapt semantics** — when "increase & adapt", is the merge **LLM-proposed for human approval** (safer,
   preferred) or rule-based field-union? (Default to LLM-proposed-for-approval unless decided otherwise.)
5. The broader **dynamic customer-management model** (Chris's separate thread) — fold its outcome in here.

## Access control — ONE RBAC model for projects + customers (proposed, 2026-06-12)

DAV's RBAC already scopes a role grant to either nothing (platform / cross-project) or a `project_id`
(`rbac_account_roles`, resolver `privileges_for(reviewer, project_id)`). Customers reuse the **same**
mechanism by generalizing the scope axis — no parallel system.

- **Generalize the binding to `(scope_type, scope_id)`.** `rbac_roles.scope` gains `'customer'`;
  `rbac_account_roles` (and `rbac_group_role_mappings`) gain `customer_id` alongside `project_id` (both
  nullable; exactly one set for a scoped grant). Resolver becomes
  `privileges_for(reviewer, project_id, customer_id)` = union of platform/cross-project +
  (project-scoped where `project_id` matches) + (customer-scoped where `customer_id` matches). Less
  invasive than a full polymorphic column, keeps FK cascades clean.
- **Privileges (mirror the project pair):** `project.view` / `project.edit` (project scope) and
  `customer.view` / `customer.edit` (customer scope). "Member of X" = holds an X-scoped role for X.
  `*.view` ⇒ see the entities you're a member of; `*.edit` ⇒ modify just those. Project management thus
  becomes "viable for all" — a non-platform-admin with `project.edit` on DCM manages **only** DCM.
- **Group-naming convention for FreeIPA-driven provisioning:** `<scope_type>.<entity_slug>.<privilege>`
  — e.g. `project.dcm.edit`, `customer.acme.view`. Scope-type first so a project and a customer of the
  same name don't collide and the name is unambiguously parseable. A group sync parses the name → resolves
  the entity by slug → derives the role grant. So **access is provisioned by creating a well-named IPA
  group** (convention over manual mapping; `rbac_group_role_mappings` already has the per-scope columns).
  Edge cases to define: slug stability + uniqueness within a scope type, reserved words (platform/global),
  case-insensitivity, and unknown-entity groups (ignore vs warn).

### Access model — a (customer × project) MATRIX, AND-composed (Chris, 2026-06-12)
Access to a resource is gated on its **`(customer, project)` cell**. By default you need **BOTH** axes:
- **customer axis** — a customer grant on that customer, **OR** a `project_all_customers` grant on that
  project (column-spanning: this project, every customer);
- **project axis** — a project grant on that project, **OR** a `customer_all_projects` grant on that
  customer (row-spanning: this customer, every project).

**Access = customer-axis satisfied AND project-axis satisfied** (platform-admin spans the whole matrix).
So each axis is met either directly or by the orthogonal spanning grant. Worked cases:
- Customer-team member with `customer:BankX` + `project:DCM` (neither spanning) → sees **only the
  (BankX, DCM) cell** — i.e. BankX's slice of the shared DCM project, *not* other customers' work in DCM.
  **This is exactly the shared-project isolation the AND buys us** (the earlier OR model leaked it).
- `customer_all_projects` on BankX → BankX's account team: every project BankX touches (project axis
  auto-satisfied), but still only BankX's customer slice unless also column-spanning.
- `project_all_customers` on DCM → the DCM consulting team: all customers within DCM (customer axis
  auto-satisfied).

**The two `*_all_*` grants are the spanning overrides the operator names explicitly** — they're the only way
to get a whole row or column without enumerating cells. The plain customer/project grants are the per-cell
building blocks; the M:N `customer_projects` defines which cells exist.

**Exclusivity carve-outs (special customers/projects).** A customer or project can be marked **exclusive**,
which **defeats the orthogonal spanning grant** so the entity can only be reached by an *explicit* per-entity
grant:
- **`project_exclusive`** (on project P) → `customer_all_projects` does **not** satisfy the project axis for
  P. Even a row-spanning customer grant must additionally hold an explicit `project:P` grant to enter P.
  (Protects a sensitive project from being swept in by broad customer grants.)
- **`customer_exclusive`** (on customer C) → `project_all_customers` does **not** satisfy the customer axis
  for C. A column-spanning project grant must additionally hold an explicit `customer:C` grant to see C's
  data. (Protects a sensitive customer's data inside an otherwise-open project.)

So the axis rules become: *project axis = explicit `project:P` **OR** (`customer_all_projects` on C **and**
P is not project_exclusive); customer axis = explicit `customer:C` **OR** (`project_all_customers` on P
**and** C is not customer_exclusive).* Direct per-entity grants always work — exclusivity only disables the
spanning shortcut. Modeled as a boolean on the customer/project row (`is_exclusive`).

**Exclusive = SEALED — explicit grant required for EVERYONE, platform-admin included (Chris, 2026-06-12).**
For an exclusive entity, the platform-admin superuser bypass does **not** apply to its *data plane*: even a
platform admin must hold an explicit `customer:C` / `project:P` grant to read or edit it. Two safeguards so
this can't cause a permanent lockout:
- **Creator auto-grant:** whoever marks an entity exclusive (or creates it exclusive) is granted an explicit
  per-entity role on it at that moment — there is never a zero-grant sealed entity.
- **Management plane stays with platform-admin, audited:** platform-admin retains the RBAC *administrative*
  ability to grant/revoke roles on an exclusive entity (so access can always be (re)provisioned) — but that
  grant action is **audit-logged** as a deliberate, visible break-glass. Reading/editing the entity's data
  still requires the explicit grant. (Separates "can hand out the key" from "can open the door.")

Modeling: privileges `project.view/edit` + `customer.view/edit`, plus a **spanning flag** on the grant
(`spans_all` — a `customer.view` grant flagged all-projects = `customer_all_projects`; a `project.view`
grant flagged all-customers = `project_all_customers`). Could equally be distinct roles; flag is fewer moving
parts. Resolver evaluates the two axes for the `(customer, project)` context of the request.

**Universal / internal customer (sentinel).** Not all work is customer-facing (internal tooling, DCM core
dev). Model as a reserved **universal/internal customer** row (mirrors the `default` project): every project
belongs to ≥1 customer; internal projects → the universal customer. No NULL-customer special cases, and
internal access is just a customer-axis grant on the universal customer.

### Matrices — the access model IS the matrix; the M:N grid is a separate (deferrable) editor
1. **Access matrix** = the `(customer × project)` cell model above — *this* is the "matrix" the operator
   asked about; grants are per-cell with row/column spanning overrides.
2. **Association grid** (customer × project membership) — the editor for `customer_projects`; defines which
   cells exist. Deferrable to the Customers/Projects domain build.
1. **Customer × Project association matrix** — the M:N itself: rows = customers, cols = projects, cell =
   associated. The natural editor for `customer_projects`, lives in the **Customers / Projects** domain.
2. **Access / grant matrix (RBAC)** — extends the existing roles-matrix / bindings view (#50): subjects
   (accounts/groups) × scoped resource (a project **or** a customer) → role. The generalized
   `(scope_type, scope_id)` binding makes this **one** matrix spanning both resource types, instead of two
   separate admin screens. OpenShift-style rolebindings, parameterized by scope type.

## Matrix UI paradigm — app-wide adaptation (#130, prioritized 2026-06-12)

Customer is now first-class and orthogonal to Project (M:N), so the UI's organizing primitive shifts from a
single **project scope** to a **(customer × project) cell**. Epic with many moving parts; design first, then
slice. Guiding idea: *the active context is a cell (customer?, project, set); every data surface either
filters to that cell or presents the matrix itself.*

### Surfaces to adapt
1. **Masthead context — add a customer axis.** Today: Project switcher + Scope (Scoping Set). Add a
   **Customer** selector beside Project → context = `(customer, project, set)`; default customer =
   **All / universal**. `customerQuery()` mirrors `scopeQuery()`; persists per-user (#129). Customer-attributed
   surfaces filter to the selected customer's cell.
2. **Association matrix grid** (Customers & Projects) — rows = customers, cols = projects, cell = associated
   (+ later sealed / demand). The M:N editor; reuse the **Cap-Map matrix component** (`renderCapMap` grid)
   + click-to-toggle. The two-pane manager (shipped) is the per-entity view; the grid is the cross-cutting view.
3. **RBAC grant matrix** — subject × scoped-resource (project **or** customer) → role, parameterized by scope
   type. Folds into the Role-bindings tab (#50) + the `(scope_type, scope_id, spans_all)` binding; shows
   `*_all_*` spans + `*_exclusive` seals.
4. **Customer-attributed data surfaces** — UC demand (shipped per-customer rollup), assessments
   (customer-primary), optionally roadmaps. Lists (Use Cases, Assessments) gain a **customer column + filter**.
5. **Reusable components** — one matrix-grid component (rows × cols × cell renderer, from `renderCapMap`),
   the two-pane manager (shipped), the membership popover (shipped). Add a "matrix grid" entry to the style guide.

### Open design questions (resolve before slicing)
- Masthead: **RESOLVED (Chris, 2026-06-12) → peer Project + Customer dropdowns** (simplest, matches the AND
  access model). Context = (customer, project, set); customer defaults to All/universal.
- **Customer-scoped vs project-scoped data:** project-level resources default to the universal-customer
  column; only customer-attributed data (demand, assessments) carries a real customer — confirm per surface.
- **Where the association grid lives** — a third tab, or a list⇄grid toggle on the existing tabs?

### Sliced plan
- **2b-i** Masthead customer axis (selector + `customerQuery()` + per-user persistence #129).
- **2b-ii** Association matrix grid (customer × project) in Customers & Projects.
- **2b-iii** Customer column/filter on customer-attributed lists. **UC list SHIPPED 2026-06-12**: the
  demand badge now carries the requesting-customer **names** (`array_agg(DISTINCT customer)` on the existing
  rollup → `customers[]`; UI shows up to 2 chips + `+N`, full list on hover). Filter = the masthead customer
  chip (2b-i). **Assessments deferred**: not customer-attributed in the data model yet (no customer linkage on
  the assessments table) — needs the Customer entity to attribute an assessment before a column makes sense.
- **2b-iv** RBAC grant matrix (role-bindings #50, scope-type-parameterized) + matrix enforcement on cell
  resources. **SHIPPED 2026-06-12.** `/api/rbac/bindings` now surfaces the **customer axis + spans_all**
  (customer grants were previously invisible there). The Role-bindings section (Config → Users & roles)
  gained a **List ⇄ Matrix** toggle: the matrix is **subject (account) × scoped-resource → role**,
  parameterized by a **Projects | Customers** axis toggle, plus an **All (spanning)** column and a
  ★platform marker for platform admins. Cells grant (role-picker popover → `assign_account_role` for
  projects / `add_customer_member` for customers — both escalation-guarded server-side) and revoke (per
  role badge, routed to the right axis endpoint via `_revokeBinding`). Reuses the `.capmap` sticky grid.
  This effectively also delivers **#50** (bindings tab).
  - **Seal enforcement — SHIPPED 2026-06-12.** `is_exclusive` (project + customer) is now **enforced**, not
    just stored: the three guards (`require_priv`, `_require_priv_conn`, `_require_customer_priv_conn`) gate
    the **platform.admin superuser bypass** on a sealed scope via `_project_sealed`/`_customer_sealed` — a
    sealed project/customer requires an **explicit grant for everyone, platform admins included**.
    Recoverable + non-destructive: default-false (no effect on existing scopes); the creator's auto-grant
    keeps them in; **break-glass** = `require_project_admin` keeps the platform-admin bypass, so an admin can
    always *manage* a sealed scope (grant themselves via the platform-scoped grant matrix) even with no
    *data* access. `/api/projects` now returns `is_exclusive`; the matrix shows 🔒 sealed columns + a project
    seal toggle (Projects admin) / customer seal (create + matrix). Only gates project/customer-scoped checks —
    platform operations (project_id=None) are never sealed, so Config/management stays reachable.
  - **Spanning (`spans_all`) — intentionally deferred.** In the current **per-axis** resolver, project
    privileges already span all customers (and vice-versa), so `spans_all` is a **no-op** until data is
    scoped to a true `(customer, project)` **cell** (which doesn't exist yet). The hollow "All (spanning)"
    matrix column was **removed**; spanning bindings render in their resource cell with a `⊞` marker. Build
    cell-level AND-composition + spanning enforcement when/if cell-scoped data lands — not before.

Each slice ships behind lint/e2e + the validation-currency rule (#128).

## Anti-poisoning invariant (do not regress)
Importance is **distinct customers**, never raw request count. Any future weighting (roadmap, multi-tenant
badge, dedup "bump") must derive from distinct customers so one customer's repeated asks cannot inflate a
UC's importance.

## Related
`review-console-design.md` (requirements inventory) · `uc-scoped-evaluation-design.md` (UC/Set scope, eval
cache) · `uc-driven-roadmaps-design.md` · `capability-catalog-design.md` (the `_cap_key` normalization
pattern) · `assessment` ingest (customer-primary, Phase 2+ linkage).

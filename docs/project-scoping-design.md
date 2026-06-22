# Project-scoping of repos and settings — design

## Context / problem
Settings must be **scoped per project**, with the same *definition* (e.g. the `dav` repo) usable
independently in more than one project. Today you can't: the `dav` repo namespace is registered
once (owned by project 20/DCM) and `managed_repos.namespace` is **globally UNIQUE**, so registering
`dav` again for the DAV project (727) fails with a duplicate-key error. More broadly: "nearly all
settings need to be scoped to the project."

## Current reality (audited 2026-06-21)
The DB is **already largely project-ready** — most settings carry a `project_id` and follow one
consistent pattern: **`project_id IS NULL` = platform default (shared); non-null = project-specific
override.** The gaps are narrow.

| Subsystem | Table | project_id? | Verdict |
|---|---|---|---|
| Models (`model_configs`) | yes (nullable) | filters by active project | ✅ scoped (default+override) |
| Prompts (`project_stage_context`) | yes (NOT NULL, PK) | ✅ scoped |
| Maturity frameworks / assessments / capability catalog | yes | ✅ scoped (NULL=seed) |
| Use cases (`managed_use_cases`) | yes (nullable) | ✅ scoped |
| RBAC (`rbac_account_roles`) | yes (nullable) | ✅ scoped (NULL=platform role) |
| Bundles | via `bundle_attachments(project_id,…)` | ✅ scoped at attach layer |
| **Managed repos** (`managed_repos`) | **column exists, but `namespace` is GLOBALLY UNIQUE and `list_repos` filtering is optional** | ❌ **the blocker** |
| **Sources** (spec/corpus/inference ConfigMaps → dav-docs-mcp) | **one global set per cluster** | ❌ **global** |

So two real gaps: **(1) managed_repos uniqueness + list filtering**, and **(2) the sources
ConfigMap/MCP projection is global** (spec/corpus are served by a single MCP fed from one ConfigMap
set, regardless of project).

## Tenancy model (decided 2026-06-21)
- **Tenant** — NEW entity, the strict isolation owner. **1 tenant → N projects.** Maps onto the
  vestigial `tenant_id`. (Engagements/orgs: DCM, a bank FSI, DAV-itself…)
- **Customer** — unchanged, **M:N with projects** — demand attribution ("who the analysis is *for*"),
  explicitly *not* the isolation owner.
- **Project** — the working/naming boundary inside a tenant (≈ a Kubernetes **Namespace**). Resource
  names (repo namespace, etc.) unique **within a project**, never globally.
- **Isolation: HARD / physical per tenant** (decided). Each tenant gets physically separate data —
  not just a `project_id` filter. See architecture below.

### Kubernetes mapping (the model we're copying)
- Project = **Namespace**: names unique within it; the unit of scope.
- `project_id NULL` platform default vs project row = **ClusterRole/Role** (+ bindings).
- Tenant→projects with propagation = **Hierarchical Namespaces (HNC) / Capsule**.
- Hard isolation = the **vCluster / separate-control-plane** end of the spectrum (we approximate it
  with schema-per-tenant + MCP-per-tenant rather than full separate clusters).

## Hard-tenancy architecture (target)
Recommended implementation = **schema-per-tenant data plane + MCP-per-tenant**, one OCP namespace,
one Postgres instance (database-per-tenant reserved for a compliance tenant that needs it):
- **Control schema** (shared, small): `tenants`, `projects`, `customers`, users, RBAC, and the
  `tenant → db-schema` + `tenant → mcp` mapping. The only globally-unique things live here.
- **Per-tenant schema** (`tenant_<id>`): ALL tenant data — repos, use_cases, runs, analyses, models,
  prompts, catalog, assessments, frameworks. The API sets `search_path = tenant_<id>, platform`
  per request from the authenticated tenant. **Postgres `search_path` gives the inheritance for
  free** — project/tenant rows resolve first, falling back to a shared **`platform`** schema of
  defaults (exactly the ClusterRole/Role fallthrough). Within a tenant schema, the
  platform-default+override pattern (nullable `project_id`) still applies *between projects*.
- **MCP-per-tenant**: each tenant gets its own `dav-docs-mcp` (or one MCP that selects the tenant's
  ConfigMap by authenticated tenant); spec/corpus served only from that tenant's repos. Resolves the
  global-MCP gap and the per-project branch-eval need.
- **Provisioning**: "create tenant" = create schema + run migrations into it + create MCP deployment +
  ConfigMap + per-tenant EgressFirewall. Needs automation (an operator/job).
- **Runs/observability/backup**: Tekton PipelineRuns labelled by tenant; metrics + backup per tenant.

### Honest cost (this is a major undertaking, not a column add)
- Every data-access path becomes tenant-aware (search_path / schema-qualified) — a broad sweep of
  `main.py`/`repos.py`/etc.
- The boot migration loader must run **per-tenant-schema**, not once.
- Existing single-schema (`public`) data must be migrated into the first tenant schema (+ a `platform`
  schema of defaults). High-blast-radius migration on live data.
- Per-tenant provisioning, backup/restore, capacity, and MCP lifecycle are new ops surface.
- This is weeks of work, not a slice. → **phase it; don't block the immediate need on the full build.**

## Design principle (recommended): adopt the existing inheritance pattern uniformly
Standardize every scoped setting on the pattern the codebase already uses for models/RBAC/maturity:
- **`project_id IS NULL` = platform default**, available to every project.
- **`project_id = P` = project P's definition**, which **overrides** the platform default of the same
  identity (e.g. same repo namespace / same model / same prompt stage).
- Resolution = "project-specific row if present, else platform default." Uniqueness is on
  **`(project_id, identity)`**, not `identity` alone — so the same `namespace` can exist once
  per project (and once at platform level).

This reuses what's already there (whole-system consistency), keeps the convenience of platform
defaults, and removes the global-uniqueness blocker. Alternative — *strict per-project, no sharing* —
is simpler conceptually but loses platform defaults and diverges from the rest of the system; not
recommended.

## Boundary rules (the tenant is the only hard wall) — RESOLVED 2026-06-21
Backed by deep research (sovereignty + SaaS practice; 22/25 claims adversarially confirmed). Full
report in this session; key cites inline.
- **Project ↔ tenant: STRICTLY TENANT-SCOPED, no crossing.** A project shared across two regulated-client
  tenants is prohibited under both sovereignty and standard SaaS practice — it commingles separately
  regulated clients' confidential engagement data, defeating hard isolation. (flightcontrol; Salesforce
  multi-org; AWS SaaS Lens.)
- **Customer ↔ tenant: STRICTLY TENANT-SCOPED, NO shared global customer directory.** The same real-world
  customer engaged under two tenants = two separate records. Critically, the *existence/name* of a customer
  in a tenant is itself confidential (bank-secrecy / who-works-with-whom), so a cross-tenant directory leaks
  client relationships between competing regulated institutions. DAV's existing Customer↔project M:N stays
  **within** a tenant. (Microsoft SaaS guidance; OWASP multi-tenant; Salesforce.)
- **Cross-project linking: ALLOWED within a tenant, never across.** Projects in the same tenant can
  reference/share UCs+repos/fork (#95/#43).
- **Sovereignty OVERRIDES convenience.** Jurisdiction follows corporate domicile, not server location
  (US CLOUD Act) — a shared cross-tenant object means a compelled disclosure or jurisdictional flaw on one
  client can expose another. Residency ≠ sovereignty; in-region storage doesn't cure commingling.
- **The ONLY cross-tenant mechanism = an MSP/parent delegated-access pattern**, NOT a shared data entity:
  the consultancy-vendor holds a parent role with explicit, audited, credential-mediated delegated access
  into each child tenant; project/customer DATA stays in the child. (F5 MSP; JumpCloud; AWS bridge model.)
- **Platform "shared-services" layer = de-identified vendor IP ONLY.** Taxonomy, prompt libraries, and
  assessment frameworks may live as platform defaults (project_id NULL) shared across tenants **iff** they
  carry no client-identifying data. Client data (customers, projects, UCs, assessments, findings) never
  pools. This is the safe reading of the "platform default + override" inheritance for a regulated context.

### Follow-ups flagged by the research (not blockers, but track)
- Confirm specific cert clauses (SecNumCloud / BSI C5 / EU Data Boundary) on operator/admin cross-tenant
  access — they may further constrain even the audited MSP delegated-access path. (Counsel per jurisdiction.)
- Define the **break-glass + audit** controls wrapping the vendor's delegated-access path so the
  consultancy's own cross-tenant reach isn't itself a sovereignty finding.
- Decide whether even a hashed/opaque cross-tenant customer reference is acceptable (likely not, by default).

## Access control — admin/edit/view at every scope (decided 2026-06-21)
A consistent **admin / edit / view** triad at each scope tier, extending the existing RBAC
(roles × privileges × bindings, #44–54) — not a new system. The substrate already has scope-nesting
and a project triad; we add the **tenant tier** and complete the triads.

### Scope hierarchy (nested; higher admin ⊇ lower — like ClusterRole ⊇ Role)
| Scope | Roles | Manages |
|---|---|---|
| **Platform** | platform-admin / **-edit** / **-view** (admin exists; add edit+view) | tenants, global defaults, all users |
| **Tenant** (NEW) | **tenant-admin / -edit / -view** | the tenant's projects, tenant-level repos/settings, users *within* the tenant |
| **Project** | project-admin / -edit / -view (exist) | the project's settings/repos/members |
| **Customer** (orthogonal, M:N) | customer-edit / -view (exist) | attribution only — not isolation |

### What the triad means at any level
- **admin** — manage the entity + its **access**: create/delete children, grant roles *at or below*
  this scope (escalation-bounded, #100), manage members.
- **edit** — change content/settings (repos, UCs, runs, prompts) but **not** access/membership.
- **view** — read-only.

### User management = a function gated by `admin` at the relevant scope
Platform-admin manages any user; **tenant-admin manages users/memberships within its tenant**;
project-admin manages project membership. No separate "user-admin" tier — it's the admin role's
member-management privilege, scoped. (Self-service create + escalation-bounded grants already exist.)

### Group-based RBAC (decided 2026-06-21) — users → groups → roles, at each scope
We don't bind users to roles directly (except as an admin convenience). The model is **groups**,
scoped per tier, mapped to the admin/edit/view roles — the OpenShift/LDAP pattern, which DAV already
half-has (`rbac_group_role_mappings` maps LDAP group keys → roles today). Generalize it to first-class
internal groups:

- **Groups** (`groups` table): `id, name, scope ∈ {platform,tenant,project,customer}, scope_id
  (tenant/project/customer id; NULL for platform), source ∈ {internal,ldap}, created_by`. So you get
  **Tenant groups, Project groups, Customer groups** (+ platform groups), each owned by its scope.
- **Membership** (`group_members`: group_id, reviewer): users belong to groups. (Optional later:
  group-in-group nesting.)
- **Binding** (generalize `rbac_group_role_mappings`): a group → a role (admin/edit/view) at the
  group's scope. e.g. group "acme-tenant-admins" (scope=tenant, scope_id=acme) → role `tenant-admin`.
- **Resolution** (`rbac.privileges_for`): a user's privileges in a context = UNION of
  (a) direct `rbac_account_roles` bindings (kept, admin convenience) **+**
  (b) roles via every group the user is a member of, whose group→role binding matches the context's
      tenant/project/customer. Higher-scope admin still subsumes lower (platform ⊇ tenant ⊇ project).

"Then we start mapping appropriately" = the flows to create groups at each scope, add users to them,
and bind groups → admin/edit/view. The existing Users & roles + bindings matrix becomes a **groups +
memberships + bindings** surface with the tenant/project/customer axes.

### Implementation (RBAC extension, group-based)
- New `groups` + `group_members`; add `tenant_id` to `rbac_account_roles` AND to the group-binding
  table (it already has `project_id`; add `tenant_id` + `customer_id` so a group binds at any scope).
- Add `scope='tenant'`; seed `tenant-admin/edit/view`; add `platform-edit/view`.
- Extend `rbac.privileges_for()` to UNION direct + group-derived roles, matching on
  tenant/project/customer; higher-scope admin subsumes lower.
- UI: groups-and-memberships management at each scope + group→role bindings matrix.
- Lands across **Phase 1** sub-slices (1a tenant entity + roles + resolver; 1b groups + membership;
  1c management UI), each independently shippable.

> Assumption (confirm): **admin nests downward** — a tenant-admin is implicitly admin of every project
> in the tenant; platform-admin is admin everywhere. (Standard; matches existing behavior.)

## Phase plan (sequenced so the immediate need isn't blocked on the full hard-tenancy build)

### Phase 0 — Project-scoped repos *within the current shared schema* — ✅ SHIPPED 2026-06-21 (commit b55650f, API build deployed)
Done: migrate_026 (per-(project,namespace) unique); projector scoped to the shared-MCP source
project (env `DAV_MCP_SOURCE_PROJECT_SLUG`=dcm) so other projects can't pollute the shared ConfigMap.
Verified: `dav` registered for project 727 alongside project 20's `dav` (no collision); shared
spec ConfigMap md5 unchanged (DCM unaffected); 727 registry shows only its repos. Deferred to Phase 1:
`repos.get_repo` namespace-string lookups are still global (UI uses uuid, so no impact today).
Original plan retained below for reference:
The dav-repo-in-two-projects need and the branch test don't need physical isolation — they need
project-unique repo names. This slice is a strict subset of the hard-tenancy target (project_id stays
meaningful inside a per-tenant schema later), so it's not throwaway.
- Migration: drop `managed_repos_namespace_key`; add UNIQUE on `(COALESCE(project_id,0), namespace)`.
- `repos.py list_repos()`: always filter by active project `(project_id=$P OR project_id IS NULL)`,
  project row wins on collision. `create/update_repo`: stamp the active project.
- ConfigMap/source projection: project-aware (P's spec/corpus built from P's repos).
- UI: Multi-repo Registry shows the active project's repos (+ "platform default" badge).
- Verify: register `dav` for project 727 with no collision; run a 727 self-eval branch run.

### Phase 1 — Tenant entity + control plane
- `tenants` table; `projects.tenant_id` FK (1:N); Customer untouched (M:N). Control schema holds the
  globally-unique identities (tenant, project, user, RBAC) + `tenant → schema/mcp` mapping.
- Existing `public` data → designated the first tenant; carve a `platform` schema for shared defaults.

### Phase 2 — Schema-per-tenant data plane (the hard-isolation core)
- Per-request `search_path = tenant_<id>, platform`; per-tenant migration runner; data-access sweep.
- Tenant provisioning job (create schema + migrate + seed).

### Phase 3 — MCP-per-tenant + per-tenant sources / branch eval
- Per-tenant `dav-docs-mcp` + ConfigMap; spec/corpus served only from the tenant's repos; wire the
  branch-targeting eval against the tenant's repos.

### Phase 4 — Per-tenant egress/quota/backup; optional database-per-tenant for a compliance tenant.

## Open decisions
1. **Sequencing:** OK to ship **Phase 0 now** (unblocks the dav-repo + branch test) while Phases 1–4
   (hard tenancy) are designed/built behind it? Or hold all code until the full tenancy design is signed off?
2. **Hard-isolation depth:** schema-per-tenant (recommended, one instance) as the default, with
   database-per-tenant reserved for specific compliance tenants — confirm.
3. **Provisioning:** is a per-tenant provisioning **operator/job** acceptable (it's required for hard
   tenancy), or do you want tenants provisioned manually/by gitops at first?

## Verification (Phase 0)
- Register `dav` repo for project 727 → succeeds (no collision with project 20's `dav`).
- `GET /api/repos` under project 727 returns 727's repos (+ platform defaults), not 20's.
- A run on 727 grounds against 727's spec repos; existing 91 DCM runs unaffected.

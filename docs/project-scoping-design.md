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

### Implementation (RBAC extension)
- `rbac_account_roles` already carries `project_id` + `customer_id`; **add `tenant_id`** (same pattern).
- Add `scope='tenant'`; seed `tenant-admin/edit/view`; add `platform-edit/view`.
- Extend `rbac.privileges_for()` OR-chain with the tenant match (`ar.tenant_id = $tenant`), and make
  higher-scope admin subsume lower (platform ⊇ tenant ⊇ project) — DAV already does "platform roles
  apply everywhere"; tenant slots in between.
- UI: the existing Users & roles + role-bindings matrix gains the **tenant axis**; a unified
  **Access** surface manages users · tenants · projects · bindings with the triads.
- Lands with **Phase 1** (the tenant entity); the tenant roles ship with the tenant.

> Assumption (confirm): **admin nests downward** — a tenant-admin is implicitly admin of every project
> in the tenant; platform-admin is admin everywhere. (Standard; matches existing behavior.)

## Phase plan (sequenced so the immediate need isn't blocked on the full hard-tenancy build)

### Phase 0 — Project-scoped repos *within the current shared schema* (unblocks now; forward-compatible)
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

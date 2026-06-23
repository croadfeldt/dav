# Use cases: tenant-scoped assets, applied to projects (sovereignty + #42/#43)

**Status: design directive, 2026-06-23 (Chris).** Resolves the project-agnostic-corpus issue the
masthead pill surfaced, and aligns UC scoping with the sovereignty model.

## The model
- **The use case is a TENANT-scoped asset.** The tenant is the hard sovereignty/isolation boundary;
  a UC lives entirely inside its tenant schema (`tenant_<slug>`) and never crosses a tenant. (Already
  physically true — `managed_use_cases` + the corpus `files` live in `tenant_flightpath`.)
- **Within the tenant, a UC is APPLIED to specific projects (M:N).** A UC is not owned by one project;
  it's a tenant-level asset that can be applied to zero or more of the tenant's projects. The
  **UC↔project "applied-to" edge lives in the tenant** — the same rule as `uc-sov-010` (a relationship
  between co-equal entities is owned by + inherits the isolation of the more-restrictive domain). So
  the application itself obeys tenancy.

This replaces today's `managed_use_cases.project_id` (a UC belongs to exactly one project) and fixes the
corpus being **project-agnostic** (the same corpus files currently appear in every project's count — see
the masthead `7/18` vs `84/100` discrepancy): a project's UCs become exactly those **applied** to it.

## Current state → target
| | today | target |
|---|---|---|
| UC isolation | tenant schema (physical) ✓ | unchanged — tenant is the wall |
| UC ↔ project | `managed_use_cases.project_id` (1 project) | M:N `use_case_projects(uc_uuid, project_id)` in the tenant |
| corpus UCs | global within tenant (shown in every project) | tenant assets, shown in a project only when **applied** |
| project's UC list | `WHERE project_id = $active` | UCs whose uuid is applied to `$active` (managed + corpus) |

## Plan (phased; additive + reversible first)
**Phase 1 — schema + backfill (safe, additive):**
- Add `use_case_projects(uc_uuid TEXT, project_id BIGINT REFERENCES public.projects(id), applied_by,
  applied_at, PRIMARY KEY(uc_uuid, project_id))` to `schema_client.sql` + a routed migration.
- Backfill from `managed_use_cases.project_id` (each UC → its current project). Keep `project_id` during
  transition (dual-read), don't drop yet.

**Phase 2 — reads scope by application:**
- `/api/use-cases`, `/api/freshness`, `_resolve_scope_uc_uuids`: a project's UCs = uuids in
  `use_case_projects` for the active project (managed + corpus alike). Corpus UCs stop auto-appearing in
  every project; they appear in a project only once applied.

**Phase 3 — UI: apply / unapply (the #43 matrix):**
- A UC↔project apply control (apply a tenant UC — managed or corpus — to one/many projects; remove).
- "Available to apply" view = tenant UCs not yet applied to the active project.

**Phase 4 — cut over + cleanup:**
- Writes target `use_case_projects`; drop `managed_use_cases.project_id` once nothing reads it.

## Notes / open
- RBAC: applying a UC to a project is a tenant-scoped, project-affecting action — guard with a
  project-edit privilege; the application rows are tenant-side (relationship-revealing, per uc-sov-009).
- A UC applied to multiple projects is one asset (single source of truth in the tenant), not a copy —
  edits propagate; "fork" (#43) is a separate explicit action that creates a new UC.
- Subsumes/advances #42 (git round-trip) + #43 (UC↔project matrix). Sovereignty UCs uc-sov-004/008/009/010
  already gate the tenant-isolation half; consider a uc-sov for "UC applied-to-project edge is tenant-owned".

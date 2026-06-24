# Use cases: tenant-scoped assets, applied to projects (sovereignty + #42/#43)

**Status: SHIPPED 2026-06-23/24 (Chris).** Aligns UC scoping with the sovereignty model, fixes the
masthead pill, and adds informed/audited deletion. This doc is **as shipped**. Note the **corpus
correction** in §Corpus (Chris, 2026-06-23): corpus is **repo-driven and always counted in the total**,
NOT apply-gated as this doc's first draft proposed.

## The model
- **The use case is a TENANT-scoped asset.** The tenant is the hard sovereignty/isolation boundary;
  a UC lives entirely inside its tenant schema (`tenant_<slug>`) and never crosses a tenant. (Physically
  true — `managed_use_cases` + the corpus `files` live in `tenant_<slug>`.)
- **Within the tenant, a managed UC is APPLIED to specific projects (M:N).** A managed UC is not owned by
  exactly one project; it's a tenant-level asset that can be referenced into zero or more of the tenant's
  projects. The **UC↔project "applied-to" edge lives in the tenant** — same rule as `uc-sov-010` (a
  relationship between co-equal entities is owned by + inherits the isolation of the more-restrictive
  domain). So the application itself obeys tenancy.
- **Corpus UCs are repo-driven, not apply-driven** (see §Corpus).

## Corpus — the masthead pill is the complete story (Chris, 2026-06-23)
The pill shows the **total UCs available to ingest for the active project, regardless of current ingest
status** — drift/changes in either source should re-run, so both count.

- **Corpus is sourced from the project's corpus-role repos**: `managed_repos WHERE 'corpus'=ANY(roles)`,
  matched by **namespace** against the `files.folder` prefix, and **included in the project total**
  (deduped against managed UUIDs). A project with no corpus repo shows 0 corpus.
- This **supersedes** the apply-gated corpus idea below: corpus is repo-list-driven, NOT apply-driven.
  Fine-grained control lives in the UC / Scoping-Sets views, not the pill.
- Applied to `/api/use-cases` (corpus branch) and `/api/freshness` (corpus counted in `total`). Popover
  reads **Corpus (from repos)**.
- Verified after deploy: **dav (727) = 7** (7 managed, 0 corpus — no corpus repo); **dcm (20) = 114**
  (103 managed + 11 corpus-only; managed grew 89→103 from a bulk transcription import — 114 is correct).

## Managed UC ↔ project (the M:N) — as shipped
| | before | after |
|---|---|---|
| UC isolation | tenant schema (physical) ✓ | unchanged — tenant is the wall |
| managed UC ↔ project | `managed_use_cases.project_id` (home, 1 project) | home `project_id` **plus** M:N `use_case_projects(uc_uuid, project_id)` references |
| "in this project" (managed) | `WHERE project_id = $active` | home `project_id = $active` **OR** referenced via `use_case_projects` |
| corpus UCs | global within tenant (shown in every project) | **from the project's corpus-role repos**, in the total (§Corpus) |

- **`use_case_projects(uc_uuid TEXT, project_id BIGINT REFERENCES public.projects(id) ON DELETE CASCADE,
  applied_by, applied_at, PRIMARY KEY(uc_uuid, project_id))`** — shipped via routed migration
  `t001` (tenant schema). `managed_use_cases.project_id` is retained as the UC's **home** project (not
  dropped); membership is home OR referenced.
- **Apply button (#43, the "available to apply" pool)** — per-row Apply/Remove on managed UCs. The
  available pool (`GET /api/use-cases?source=managed&applied=0`) = managed UCs homed in **other**
  projects of the same tenant, not yet referenced here. Because the tenant boundary **is** the schema,
  the pool is sovereignty-safe by construction. Apply/unapply via `POST /api/use-case-projects[/remove]`
  (RBAC `P_PROJECT_USECASES`). Rows carry `home_project_id` + `referenced` so the UI shows
  `↪ ref` + Remove on referenced rows, `+ Apply` in the pool.
- **Corpus is excluded from apply** — it's repo-driven (§Corpus), so the apply control is managed-only.

## Server owns UC identity (extraction/authoring hygiene)
- **UUID** is assigned by the server on save (`uc-<uuid4>`); extraction/assist no longer emit one. A
  routed migration (`t002`) refreshed earlier fabricated, uuid-shaped-but-not-uuid4 ids to real uuid4 and
  repointed references. Handle-style ids (`uc-sov-004`) are deliberately untouched.
- **Handle** is auto-derived on save when missing (`namespace/profile/slug`, via `_derive_uc_handle`,
  mirroring `/repair`) **before** engine validation — handle is mechanically computable, so extraction
  drafts that omit it are repaired rather than hard-failing the save. Semantic fields (dimension enums,
  intent, success_criteria) stay strict; the bulk-extraction prompt's enum lists were verified to match
  the engine validator's `_DCM_*` exactly, so the prompt can already produce valid UCs.

## Reference by ID, not denormalized copies (set-name invariant)
Cross-entity references resolve the **current** value by joining on the ID; a stored copy is a provenance
fallback only. Concretely, runs/experiments stored a `set_name`/`eval_set_name` snapshot, so a scoping-set
rename never propagated. Fixed: every **display** site (runs list, analysis summary, rerun-config,
experiments list+detail; export already did) resolves the live `use_case_sets.name` via `set_id`/
`eval_set_id`. The snapshot is kept only as a fallback for a deleted set (`run_sessions.set_id` FK is
`ON DELETE SET NULL`) or the synthetic "All Use Cases"/custom selections (no `use_case_sets` row). Run/
experiment CREATE still capture the snapshot deliberately (immutable record of what was evaluated).

## Deletion — right-to-erase, propagation-warned, audited
Deleting data is **allowed** (sovereignty/security), but informed and visible.

- **Non-obvious cascade fact:** a managed-UC delete only FK-cascades `lifecycle_events` +
  `uc_customer_requests`. `use_case_set_members` and `use_case_projects` have **no FK** to
  `managed_use_cases` (corpus UCs share those tables), so they used to dangle — the delete now removes
  them explicitly in the transaction.
- **Impact preview:** `GET /api/use-cases/{uuid}/delete-impact` and `GET /api/sets/{id}/delete-impact`
  return propagation counts (removed vs retained / detached). The UI shows a propagation warning before
  the destructive click (`_confirmDeleteImpact`).
- **UC delete:** removes the dangling join rows; **retains** historical `uc_analyses` by default
  (provenance, surfaced); **audits** (`audit_log` action `use_case.delete`) with the full impact.
  - **Sovereignty erasure:** `DELETE /api/use-cases/{uuid}?purge_analyses=true` also deletes
    `uc_analyses` (cascades to `uc_capabilities`/`uc_gaps`/`uc_capability_deps` via `analysis_id`) and
    clears `analysis_output_cache`. The UI asks a second confirm when analyses exist (OK = erase them;
    Cancel = keep as historical record, UC still deleted). The audit records `purge_analyses` + count.
- **Set delete:** members detached (the UCs themselves are kept); past runs keep their recorded set name
  but lose the live link (`set_id → NULL`). Audited (`use_case_set.delete`).

## Notes / remaining
- RBAC: applying a managed UC to a project is a tenant-scoped, project-affecting action — guarded by
  `P_PROJECT_USECASES`; the `use_case_projects` rows are tenant-side (relationship-revealing, per
  uc-sov-009).
- A managed UC referenced into multiple projects is one asset (single source of truth in the tenant), not
  a copy — edits propagate. **"Fork"** (#43 — copy into a project as a new editable UC) is a separate
  explicit action, not yet built.
- Advances #42 (git round-trip) + #43 (UC↔project matrix; reference shipped, fork pending). Sovereignty
  UCs uc-sov-004/008/009/010 gate the tenant-isolation half.

# Scope & bundles — multi-axis config/capability scoping + reusable composition

_Chris 2026-06-10: "[MCP servers like a GitHub-repo source] will be a platform scoped MCP
server. So again we need to have platform scoped config / capabilities and project scoped
capabilities, but we also have a third scoping which is DAV use categorization, eg;
assessment scoped config or UC gap analysis scoped config." + "I like the concept of
creating bundles for this. Having the ability to bundle different configs / capabilities /
output, etc is powerful."_

Status: **DESIGN — all four open questions resolved with Chris 2026-06-10; ready to plan
the build.** Task #107. Schema-level + security-sensitive
(platform/use-category resources are usable across *all* projects). Reconciles with
`blueprint-projects-design.md` (#95), `capability-catalog-design.md` (#104), and the UDLM
contract (#106). Decisions locked with Chris are marked **[DECIDED]**.

## Problem
Every config + capability entity in DAV is currently `project_id NOT NULL` — scoped to
exactly one project (the v0.14 config-tenancy work). That breaks two real needs:

1. **Platform infrastructure** that is shared across every project — a GitHub-repo source
   MCP, the `dav-docs-mcp` spec server, a house LLM endpoint. Trapping these in one project
   (the bug that surfaced this: `dav-docs-mcp` lived only in project 20) means every other
   project re-creates them, and there is no single source of truth.
2. **DAV "use categorization"** — config that belongs to a *kind of work*, not a project:
   an *assessment* needs one ingest model + taxonomy + prompts; a *UC gap analysis* needs a
   different set. Today that config is copied into each project doing that kind of work.

And a composition need: operators want to **bundle** configs + capabilities + outputs into
named, reusable units they can attach to projects or use-categories — the same instinct
that drives blueprints (#95), one level lower.

## Two orthogonal scope axes  **[DECIDED — orthogonal axes]**
Every scoped entity gains **two nullable axes**, where `NULL` means "any":

| Column        | Meaning                                                              |
|---------------|---------------------------------------------------------------------|
| `project_id`  | nullable FK → projects. NULL = applies to all projects.             |
| `use_category`| nullable TEXT. NULL = applies to all use-categories.                |

An item **matches** the active context `(project P, use-category C)` when
`(project_id IS NULL OR project_id = P) AND (use_category IS NULL OR use_category = C)`.

The four resulting scope kinds fall out of the two axes:

| project_id | use_category | scope kind            | example                                   |
|------------|--------------|-----------------------|-------------------------------------------|
| NULL       | NULL         | **platform**          | GitHub source MCP, `dav-docs-mcp`         |
| set        | NULL         | **project**           | a client's private data-source MCP        |
| NULL       | set          | **use-category**      | the assessment ingest model + taxonomy    |
| set        | set          | **project × category**| assessment-only config inside project X   |

This is strictly more expressive than mutually-exclusive levels and needs only two nullable
columns + a match predicate — no new join tables for the scope itself.

### Use-category taxonomy
A small controlled vocabulary aligned to DAV's pipeline stages / workflow families:
`arch-review`, `enhancement`, `assessment`, `uc-gap-analysis`, `uc-authoring`, `evaluation`.
Stored as a seed list (extensible). The active use-category is supplied by the operation
(a run's pipeline family, the assessment ingest path, the gap-analysis view) — analogous to
how `_active_project_id` supplies the active project today, via an `X-DAV-UseCategory`
request hint defaulting per view.

## Resolution rules  (proposed — confirm)
Two patterns by entity nature:

- **Additive collections** → **UNION** of every matching item across scopes. MCP servers,
  managed repos, model lists. Effective set for `(P, C)` = all items whose axes match.
  So the platform GitHub/docs MCP (`NULL,NULL`) always appears, plus any project- or
  category-scoped ones. You never "lose" the platform sources.
- **Singular settings** → **CASCADE, most-specific match wins.** Model defaults, stage
  prompts/context. Specificity score = `(project_id set ? 2 : 0) + (use_category set ? 1 : 0)`;
  highest wins; tie-break by most-recently-updated. So `project×category` (3) >
  `project` (2) > `use-category` (1) > `platform` (0).

(Chris leaned toward making composition explicit via **bundles** rather than only implicit
resolution — see next. Bundles layer *on top of* these rules: a bundle contributes items,
those items still carry axes and resolve by the same union/cascade.)

## Bundles — first-class reusable composition  **[DECIDED — build bundles]**
A **bundle** is a named, reusable package that groups heterogeneous items — configs
(models/MCP/repos), capabilities (taxonomy terms, catalog entries), and outputs
(report/template artifacts) — so a methodology can be assembled once and attached anywhere.

**Versioned & immutable (pinned).  [DECIDED]** A bundle's contents live in immutable
**versions**; attachments pin a specific version; editing a bundle creates a new version;
consumers upgrade explicitly. This keeps engagements stable across confidentiality
boundaries — editing a methodology never silently mutates a live engagement.

```
bundles            (id, name, slug, description, kind, created_by, created_at,
                    current_version_id)          -- pointer to the latest version
                    kind ∈ {config, capability, output, mixed}
bundle_versions    (id, bundle_id, version_no, status, note, created_by, created_at)
                    -- IMMUTABLE snapshot. status ∈ {draft, published}; only published
                    --   versions are attachable. version_no monotonic per bundle.
bundle_items       (bundle_version_id, item_type, item_ref_or_snapshot, position)
                    -- belongs to a VERSION, not the bundle. item_type ∈ {mcp_server,
                    --   model_config, managed_repo, model_default, capability_term,
                    --   capability_entry, output_template, …}. See snapshot note below.
bundle_attachments (bundle_version_id, project_id NULL, use_category NULL, attached_by, attached_at)
                    -- pins a specific published version at any (project × use-category) scope
```

**Snapshot vs reference (the key impl detail):** to make a version truly immutable, a
published version **snapshots** its items' definitions (the config/capability/output content
is copied into `bundle_items` at publish time) rather than referencing live rows that could
later change. Attaching a version materializes those snapshotted items into the effective
set. (Standalone directly-scoped rows still exist independently — see resolution below.)

**Effective set** for `(project P, use-category C)` =
`directly-scoped items matching (P,C)`  ∪  `items from bundle versions attached to (P,C)`,
then resolved by the union/cascade rules above (a directly-scoped, more-specific singular
setting still wins over a bundle-contributed broader one). Bundles are the *substrate*
`blueprint-projects-design.md` (#95) builds on: a **blueprint** = a curated set of bundle
versions (+ project `kind`/inheritance) applied to spin up isolated engagement projects.
Names stay distinct — *bundle* = atomic reusable grouping; *blueprint* = project template
that references bundle versions — and bundles ship first so blueprints compose them.

### Use-category source of truth  **[DECIDED — hybrid]**
- **At run time:** derived from the run's **pipeline family** (an arch-review run →
  `arch-review`, the assessment-ingest path → `assessment`). Unambiguous; no user input.
- **In config/consumption views** (authoring "this MCP belongs to assessments", with no run
  in flight): an explicit **use-category selector**, carried as an `X-DAV-UseCategory`
  request hint — mirrors the `X-DAV-Project` header + `_active_project_id` pattern. A new
  `_active_use_category(request, run?)` resolver: run-derived first, else the header, else
  NULL (= all categories).

## Entities in scope
Add the two axes (`project_id` relaxed to NULL-able + new `use_category`) to:
`mcp_server_configs`, `model_configs`, `managed_repos`, `model_defaults`,
`capability_catalog`, `capability_taxonomy_terms`, `project_stage_context`, **and
`output_templates`** (outputs get the axes directly too — **[DECIDED]** — for a uniform
scope model, not bundle-membership-only). Data — UC sets, assessments, findings, runs — is
**never** platform/use-category scoped; it stays strictly project-isolated (confidentiality
boundary).

## RBAC
- New privilege **`usecat.manage`** (cross-project) — **[DECIDED]**: defined as a real,
  delegatable privilege and **seeded onto Platform Admin only** in v1. All gating references
  it from day one, so later delegation (a "methodology owner" role that curates use-category
  / platform config without full platform admin) is just a **role-binding**, no gating
  rewrite or migration.
- **platform** items (both axes NULL) and **use-category** items (cross-project) → require
  `usecat.manage` (⇒ platform admins in v1) to create/edit. Bundles + bundle versions +
  attachments at platform/use-category scope likewise require `usecat.manage`.
- **project** items → `project.integrations` (unchanged); a bundle attached *only* to a
  project is managed by that project's admin.
- Reads resolve the **union** the caller is entitled to: project items require
  `project.data.read` in that project; platform/use-category items are readable by any
  authenticated member (they're shared infra), but **never expose secrets** (tokens stay
  masked, as `_mcp_public` already does).
- Write/execute on a platform or use-category item that a project user does not own → 403.
- Every bundle publish / attach / detach is **audited** (object_type `bundle`/
  `bundle_attachment`), reusing the audit log (#103 substrate).

## Migration & rollout (phased — each its own change + deploy + verify)
**Delivery status (2026-06-10):** Phase 1 ✅ + Phase 2 ✅ shipped + verified live (DCM).
Phase 1b (model_defaults PK + capability/stage-context axes) and Phase 2b (models/repos
lists + run-time model/MCP consumption resolution) remain; Phases 3–6 pending.

1. **Schema:** ✅ **DONE** (in `schema.sql` tenancy block, not a pre-schema migration —
   it must run after the tables exist). `use_category` + NULL-able `project_id` on the
   config registries (mcp/models/repos); retired the NULL→DCM backfill; scope-aware NULL-safe
   name uniqueness; `use_categories` vocab + `output_templates`; `usecat.manage` → Platform
   Admin. _Deferred (1b): model_defaults (PK on project_id), capability_catalog/
   capability_taxonomy_terms/project_stage_context (keying)._
2. **Resolver:** ✅ **DONE (read path).** `_active_use_category(request)` (X-DAV-UseCategory
   hint; run-derived later) + `_scope_where(pid,cat)` UNION predicate; MCP list + health
   rewired. Backward-compatible. _Deferred (2b): models/repos lists + run-time consumption
   (model resolution by id/name, MCP tool calls)._
3. **Promote platform infra:** move `dav-docs-mcp` + GitHub-style source MCPs to platform
   (`project_id = NULL`). Add the egress allow for any platform MCP LB IP (ties to #59).
4. **Bundles (versioned):** `bundles` + `bundle_versions` (immutable, publish-to-snapshot) +
   `bundle_items` + `bundle_attachments` (pin a published version); CRUD + publish + attach/
   detach endpoints (gated `usecat.manage` / `project.integrations`); effective-set join.
   UI: a Bundles manager (versions + publish) + per-project/-category "attached bundles".
5. **Blueprints (#95) recompose** onto pinned bundle versions.
6. **Docs:** fold into `review-console-design.md` (config-tenancy section), update
   `blueprint-projects-design.md` to reference bundle versions, bump versions.

## Security considerations
- A platform/use-category resource is reachable from **every** project's run context — so
  creating one is a privileged, audited action, and the network egress it implies (e.g. a
  GitHub MCP → internet, a docs MCP → its LB IP) must be in `dav_egress_allow_cidrs`. The
  per-pod firewall is namespace-wide (see review-console-design egress section); per-project
  network isolation is not enforceable, so app-layer scoping is the control.
- Secrets on shared items (MCP bearer tokens, repo PATs) remain Fernet-encrypted and
  masked on read regardless of scope.
- Use-category is **not** a confidentiality boundary — only `project_id` is. Never scope
  engagement data by use-category.

## UDLM alignment (#106)
Scope axes + bundles are part of the UDLM contract: every Knowledge/Config entity carries
`{project_id?, use_category?}` and may be packaged into a `Bundle` aggregate. Version the
contract when these land.

## Resolved decisions (Chris, 2026-06-10)
1. **Use-category source — hybrid.** Run-derived at execution; explicit `X-DAV-UseCategory`
   selector in config/consumption views. `_active_use_category(request, run?)`.
2. **Outputs get the two axes directly** (not bundle-membership-only) — uniform scope model
   across configs, capabilities, and outputs.
3. **`usecat.manage` is a real privilege**, seeded onto Platform Admin only in v1; later
   delegation is a role-binding, no gating change.
4. **Bundles are versioned & immutable** — attachments pin a published version; editing
   creates a new version; consumers upgrade explicitly (publish-to-snapshot model).

## Remaining open (smaller, can settle during build)
- Use-category **vocabulary** final list + whether it's a fixed seed or admin-editable
  (lean: seed now, make editable under `usecat.manage` later).
- Whether a bundle version's **snapshot** stores full item content or a content-hash +
  copy-on-publish of the source rows (perf/storage detail; doesn't change the contract).
- Bundle-attachment **upgrade UX** — manual "upgrade to v4" per attachment vs. a bulk
  "upgrade all consumers" action for a bundle owner.

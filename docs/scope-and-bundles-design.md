# Scope & bundles — multi-axis config/capability scoping + reusable composition

_Chris 2026-06-10: "[MCP servers like a GitHub-repo source] will be a platform scoped MCP
server. So again we need to have platform scoped config / capabilities and project scoped
capabilities, but we also have a third scoping which is DAV use categorization, eg;
assessment scoped config or UC gap analysis scoped config." + "I like the concept of
creating bundles for this. Having the ability to bundle different configs / capabilities /
output, etc is powerful."_

Status: **DESIGN — review before build.** Task #107. Schema-level + security-sensitive
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

```
bundles            (id, name, slug, description, kind, created_by, created_at, updated_at)
                    kind ∈ {config, capability, output, mixed}
bundle_items       (bundle_id, item_type, item_id, position)
                    item_type ∈ {mcp_server, model_config, managed_repo, model_default,
                                 capability_term, capability_entry, output_template, …}
bundle_attachments (bundle_id, project_id NULL, use_category NULL)
                    -- attach a bundle at any scope, same two-axis model as items
```

**Effective set** for `(project P, use-category C)` =
`items matching (P,C) directly`  ∪  `items contributed by bundles attached to (P,C)`,
then resolved by the union/cascade rules above. Bundles are the *substrate*
`blueprint-projects-design.md` (#95) builds on: a **blueprint** = a curated set of bundles
(+ project `kind`/inheritance) applied to spin up isolated engagement projects. We keep the
names distinct — *bundle* = atomic reusable grouping; *blueprint* = project template that
references bundles — and implement bundles first so blueprints compose them.

## Entities in scope
Add the two axes to: `mcp_server_configs`, `model_configs`, `managed_repos`,
`model_defaults`, `capability_catalog`, `capability_taxonomy_terms`, `project_stage_context`.
(`project_id` already exists and is NOT NULL on these — the migration relaxes it to NULL-able
and adds `use_category`.) Data — UC sets, assessments, findings, runs — is **never**
platform/use-category scoped; it stays strictly project-isolated (confidentiality boundary).

## RBAC
- **platform** items (both axes NULL) and **use-category** items (cross-project) → managed
  by **platform.admin**, or a new cross-project `usecat.manage` privilege.
- **project** items → `project.integrations` (unchanged).
- Reads resolve the **union** the caller is entitled to: project items require
  `project.data.read` in that project; platform/use-category items are readable by any
  authenticated member (they're shared infra), but **never expose secrets** (tokens stay
  masked, as `_mcp_public` already does).
- Write/execute on a platform or use-category item that a project user does not own → 403.

## Migration & rollout (phased — each its own change + deploy + verify)
1. **Schema:** idempotent `ALTER … ADD COLUMN use_category TEXT`, relax `project_id` to
   NULL-able on the seven entities; seed the use-category vocabulary. No data moves yet.
2. **Resolver:** central `resolve_scoped(entity, project_id, use_category)` implementing
   union/cascade; rewire the config consumption + list endpoints through it. Keep current
   behaviour when `use_category` is absent (project-only) for backward compat.
3. **Promote platform infra:** move `dav-docs-mcp` + GitHub-style source MCPs to platform
   (`project_id = NULL`). Add the egress allow for any platform MCP LB IP (ties to #59).
4. **Bundles:** the three tables, CRUD endpoints (RBAC as above), attach/detach, and the
   effective-set join. UI: a Bundles manager + per-project "attached bundles".
5. **Blueprints (#95) recompose** onto bundles.
6. **Docs:** fold into `review-console-design.md` (config-tenancy section), update
   `blueprint-projects-design.md` to reference bundles, bump versions.

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

## Open questions
1. Use-category source of truth — request hint (`X-DAV-UseCategory`) per view, or derived
   strictly from the run's pipeline family? (Leaning: derived where a run exists, hint for
   config-management views.)
2. Do **outputs** (reports/templates) need the same two axes now, or is bundle-membership
   enough initially?
3. `usecat.manage` as a distinct privilege vs. folding into `platform.admin` for v1.
4. Bundle versioning/immutability — do attachments pin a bundle version, or always track
   latest? (Matters once blueprints depend on them.)

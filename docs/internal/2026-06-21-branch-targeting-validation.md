# DAV test session — branch targeting + agent identity validation (2026-06-21)

Autonomous session while Chris was away. Goal: validate the two newly-deployed features
(branch targeting, agent identities) and start a per-branch comparison test using the
existing + new DAV use cases.

## TL;DR
- ✅ **Agent identity** feature: validated end-to-end in production.
- ✅ **Branch targeting — capture + surface**: validated (branch persists on the run row and
  shows in `/api/runs`).
- ⚠️ **Branch targeting — SHA rollup at ingest**: code-correct but **unverifiable in this
  deployment** — its input (the #114 per-UC `source_repo_shas`) is **null on 0/753 analyses
  ever**. Pre-existing #114 gap, not my rollup. Validation run `dav-stage2-console-052983`
  (project 20) **Succeeded** end-to-end with a branch override, ingested, and the rollup
  correctly propagated the upstream null. Detail in "SHA rollup" below.
- ❌ **The per-branch comparison Chris wanted can't run meaningfully yet** — three blockers
  below. I did **not** fan out 4 branch runs because nothing varies per-branch for the DAV
  self-project as currently wired (it would burn GPU on identical results).

## What was validated

### Agent identities (kind='agent') — PASS
Full lifecycle via API against the live system:
1. `POST /api/accounts {kind:'agent'}` → created, `kind=agent`, **login-less** (`has_password=false`,
   `source=agent`), no activation invite.
2. Minted a PAT for it; `/api/me` → authenticated as the agent, `roles=[]`, only `project.create`.
3. Granted `project-edit` (role 3) on project 727 → `/api/me` now carries the role with
   `project.usecases` + `project.runs.execute`. **A role-bound agent authenticates and carries
   exactly its roles** — the design goal.
4. Cleaned up (account deleted). No residue.

### Branch targeting — capture + surface — PASS
Run `dav-stage2-console-052811` triggered with `spec_repo_branch`/`corpus_repo_branch`
overrides → `run_sessions` persisted both, and `GET /api/runs` returned them (the UI chip data).

### Branch targeting — SHA rollup — see run 052983
The SHA rollup (`corpus_repo_sha`/`spec_repo_sha`) resolves at ingest from the project's
**registered** repos (`_resolve_project_repo_shas(project_id)`). Project 727 (DAV) has **no
registered repos**, so its runs never populate the SHA. Run `dav-stage2-console-052983` was
triggered on **project 20 (DCM)**, which *does* have registered repos, to validate the rollup.
→ Check: `SELECT run_name, spec_repo_sha FROM run_sessions WHERE run_name='dav-stage2-console-052983';`

## Blockers found (this is the signal)

### 1. The 7 DR self-eval use cases don't conform to DAV's live UC schema — can't load
The files `examples/dav-self/.../uc-fr-001..007.yaml` (committed in `c85a1a7` on
`feat/findings-resolution-design`) use a bespoke shape: `use_case_uuid:`, `uc_type: analytical`,
`domain: findings_resolution`, `analytical.scenario`. The live API/engine requires the **DCM
consumer profile** schema: `uuid:`, `handle:`, `generated_by.{mode,source}`,
`scenario.{description,intent,success_criteria[],actor.{persona,profile},profile,dimensions.{lifecycle_phase,
resource_complexity,policy_complexity,provider_landscape,governance_context,failure_mode}}` — all
enum-constrained to DCM infrastructure values. `POST /api/use-cases` → **400 "must have a non-empty
'uuid' field"** for all 7.
- They can't be mechanically coerced: the required `dimensions` are DCM-infra enums
  (`new_request`, `single_eligible`, …) that are semantically wrong for DAV's own meta-capabilities.
- **Root issue:** the validator is "Hardcoded to the DCM consumer profile for now (single-consumer
  install)." DAV can't represent use cases for a *different* consumer (DAV-itself / findings_resolution).
  This is the multi-consumer-profile gap — ties to #197 and the `_validate_uc_yaml` TODO.
- **Fix path:** either (a) author the DR UCs in the DCM-profile schema, or (b) add a
  findings-resolution consumer profile so the bespoke shape validates. (b) is the real fix.

### 2. The DAV self-project (727) isn't wired to evaluate against the dav repo
- No registered repos; spec source = the **DCM/cost repos** served by the shared `dav-docs-mcp`
  (multi-source ConfigMap); corpus = the managed UCs (in-DB). **Neither depends on the dav branch.**
- So evaluating the existing 18 UCs across dav branches yields ~identical results — there is nothing
  branch-dependent to compare. A real "compare across branches" needs the **dav repo registered as a
  spec (or corpus) source** for project 727, then one run per branch.

### 3. `spec_repo_branch` override is applied against the *resolved* spec URL, not coupled to a URL
Canary `dav-stage2-console-052811` failed at `sync-spec`:
`fatal: Remote branch feat/dcm-uc-prioritization not found in upstream origin` — because it cloned
`croadfeldt/dcm.git` (the ConfigMap spec URL) at my override branch, which doesn't exist in DCM.
Overriding branch **without** also overriding `spec_repo_url` points the wrong branch at the wrong
repo. The New-Run flow should couple them (or warn). Minor but real UX sharp edge.

### 4. #114 `source_repo_shas` capture is non-functional in this deployment (0/753)
The per-UC `source_repo_shas` (#114, marked completed) is **null on every one of 753 analyses
ever ingested** — it has never populated here. `_resolve_project_repo_shas` needs (a) repos
returned by `_repos.list_repos(role, project_id)` and (b) a GitHub token from `corpus_push.push_token()`.
The 5 `managed_repos` rows are `tenant_id='default'` (not bound to a project_id), so the
project-scoped lookup returns nothing → null. Consequence: the eval-cache staleness/drift detection
(#114/#115) and **my run-level SHA rollup** both have no SHA to work with. Fixing #114's resolver
(project-scope the repos, or resolve from the resolved run params) lights up both.

### Minor: `X-DAV-Project` header only accepts numeric IDs (`hdr.isdigit()`), not slugs
Slug "dcm" silently fell back to the default project. The UI always sends IDs, so harmless in-app,
but API callers must use the numeric id.

## Feature gap in MY branch-targeting code (follow-up)
The SHA rollup keys off the project's **registered** repos, not the **resolved run params'**
repo URL+branch. So an ad-hoc run that uses per-run `spec_repo_url`/`spec_repo_branch` overrides
against an *unregistered* repo captures the branch but **not** the SHA. Ideally resolve the SHA from
what was actually evaluated (the resolved params), so override-based runs get full provenance too.

## To actually run the per-branch comparison Chris wants (decision needed)
Register the **dav repo** as a source for project 727 and run one eval per branch:
- Option A (corpus-varies): register `croadfeldt/dav.git` as a **corpus** repo for 727; the dav
  branches then carry different UC corpora → meaningful diff. But the UCs are managed in-DB, so this
  competes with the managed-UC model.
- Option B (spec-varies): register `croadfeldt/dav.git` as a **spec** source (root_path = `specs/`),
  evaluate the existing 18 UCs against the DAV architecture at each branch → shows which DAV features
  are covered/gap per branch. This is the natural self-eval and the best comparison. Needs the dav
  `specs/` to be MCP-ingestable (unverified).
- Either way: fix blocker #1 (DR UC schema) before the *new* UCs can join.

## Artifacts / cleanup
- Test agent account: created + **deleted** (no residue).
- Runs left as evidence: `052811` (failed — finding #3), `052983` (project 20 — SHA-rollup validation).
- Stray canary `052911` deleted.
- `/tmp/drucs/` holds the 7 extracted DR UC YAMLs (transient).
- Nothing committed; no live config changed (no repos registered, no source ConfigMaps edited).

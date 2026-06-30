# DAV — bring up to speed (full pass) — plan

_2026-06-29. DAV took a back seat to UDLM/DCM; bring it current with (a) the recent spec changes and
(b) requested changes from others. Chris's scope: **full pass, all three in order**; **A/B everything,
Chris reviews each** eval-affecting change. Tracks #237 (sync/re-ingest) + #128 (validation current) +
new repositioning work._

DAV is **live** (dav.roadfeldt.com:8843, auth-gated). The gap is freshness/relevance, not a dead tool:
its corpus + vocab predate this cycle's UDLM/DCM changes, so its assessments measure an **old** spec.

## The four inputs
1. **Spec drift** — UDLM/DCM changed a lot this cycle: GateKeeper→**Gating Policy**; provider
   **kinds-vs-capabilities** (auth/creds are capabilities); **trust broker** (ADR-022); **DecisionRecord**
   scope/validation; **Composite/Atomic Resource** (#232); **registry published (20 resource types)**.
   DAV has 34 stale `GateKeeper` refs across corpus/schema/code/prompts.
2. **Requested changes from others (Piotr / pkliczewski)** — 26 unaddressed review comments in the DAV
   inbox (project 727): **9 on PR #65** (the DAV→DCM validation-corpus import — UC-level feedback),
   **~8 on PR #64** (taxonomy/concepts), **rest on #18** (taxonomy/AEP). These are the stakeholder asks.
3. **DAV feature-request UCs** — 13 self-referential UCs in project 727 (trusted-host %, provider-fitness
   scoring, cross-UC pattern detection, cost-of-gap, rehydration-readiness, CVE→corpus query, recording
   pipeline, multi-customer tags, structured-spec ingest, capability demand aggregate, UC priority,
   spec-diff/comparison, headless CLI/CI mode).
4. **Pending DAV task backlog** — ~30 open #-tasks (e.g. #95 blueprints, #96 prompt-assistant, #102-104
   assessment/capability, #118-120 publish/rebuild/synthesis, #125-126 prompt-per-model/cap-edges,
   #143-144 CI/CD/formalize, #151-153 ingest, #177 projection-builder, #181-183 dedup/schema-constrained/
   WebLLM, #184 apply-DCM-ideals-to-DAV, #197 F&R, #216-217 reconcile locked-decisions/tenancy).

## Sequence (full pass) — A/B gates marked ⏸

### Phase 1 — Sync to current spec + re-ingest (#237)
- **1a [SAFE, staged]** additive consumer-profile vocab — DONE on branch `chore/udlm-vocab-refresh`
  (single_gating_policy, provider kind `resource`, provider_capabilities, catalog_item_classes). No regression.
- **1b** confirm project-20 corpus-repos point at the **updated croadfeldt** UDLM/DCM (vs dcm-project) — *config check, needs Chris confirm.*
- **1c ⏸ A/B** re-ingest the corpus from updated specs → run vs current baseline → Chris reviews pass/fail delta.
- **1d ⏸ A/B** tested schema-enum + code vocab update (compare/diagnose/uc_assist/main: accept `gating`,
  capabilities, composite_resource) behind A/B.
- **1e ⏸ A/B** prompt refresh (prompts.py) — A/B, Chris reviews (stage-2 prompt edits have regressed 32/32 before).

### Phase 2 — Re-run validation + triage (#128)
- **2a ⏸** run DAV validation end-to-end against current specs; capture failures.
- **2b** triage + fix the breaks (schema/handle/health), green the suite.
- **2c** keep-in-sync mechanism so DAV re-ingests on spec change (freshness chip already exists; wire a trigger).

### Phase 3 — Reposition + exercise the new model
- **3a** address **Piotr's PR-65 UC feedback** (9): clarify/split the flagged UCs (tenancy-too-broad → split;
  identity-delegation; discovery-management model; historical-consistency; sovereign-rehydrate rationale;
  tenant↔profile mapping). These are UC edits → re-ingest (A/B).
- **3b** exercise new UDLM functionality in DAV: Composite/Atomic Resource, capabilities, DecisionRecord as
  first-class in assessments (overlaps #197 F&R, #184 apply-DCM-ideals-to-DAV).
- **3c** triage the 13 feature-request UCs + the pending backlog into a ranked DAV roadmap (separate from this
  bring-current pass).

## What I can do NOW (no eval risk, no deploy)
- Triage **Piotr's 26 comments** into an addressable per-UC action list (done below / next).
- Inventory the **13 feature-request UCs** + map to tasks.
- Confirm corpus-repo targets (read-only check).
- Keep `chore/udlm-vocab-refresh` ready to merge.

## What WAITS for Chris (per "A/B everything, review each")
- Every re-ingest + code/prompt change that affects eval output (1c/1d/1e/2a/3a) runs as an A/B he reviews.
- No deploy/merge without his go.

---

## Prep findings (2026-06-29, read-only)
- **Phase 1b RESOLVED — corpus/spec already targets croadfeldt main.** Project-20 spec+corpus repos:
  `github.com/croadfeldt/dcm.git@main` (corpus,spec), `github.com/croadfeldt/udlm.git@main` (corpus,spec),
  plus `croadfeldt/dav` (corpus/uc-store). `dcm-project/dcm` is **issue-source only** (not spec). → a
  **re-ingest will pull THIS cycle's merged spec** with no repointing needed. (Also: pgarciaq/cost-dcm-provider
  + project-koku/koku carry spec role for the cost/FOCUS provider corpus.)
- **BUG to confirm+fix:** the dav corpus repo URL is **malformed** — stored as `https://croadfeldt/dav.git`
  (no `github.com/`). It won't resolve, so dav-self-corpus ingestion would fail. Likely a typo of
  `https://github.com/croadfeldt/dav.git`. Fix = update the repo record (Repos admin / `/api/repos`).
  *Flagging, not auto-changing a corpus-config DB record.*

## Net: ready to run Phase 1c (re-ingest A/B) on Chris's go
Spec source confirmed current; vocab branch staged; the re-ingest is the first A/B gate. Recommend:
fix the dav-URL bug → re-ingest project 20 from croadfeldt main → A/B vs current baseline → review the
pass/fail delta (that *measures* the real drift before any code/prompt edits).

---

## ROOT CAUSE — why DAV doesn't flag UCs stale after DCM/UDLM/DAV changed (2026-06-29)
Chris observed: changed the arch repos, but DAV still says UCs valid; and staleness should be **targeted**
(which UCs depend on *what changed*). Investigation:

1. **The drift-staleness system is NOT on the deployed build.** It lives only on the unmerged
   `feat/tenant-aware-migrations` branch (#114–117): two axes — `stale_edited` (UC content_sha changed) +
   `stale_drifted` (eval's captured `source_repo_shas` != current repo HEADs, via `_repo_drifted` +
   `_current_project_repo_shas_cached`, /api/freshness). **Deployed `main` has only the manual human
   review status `stale`** — no automated drift detection. → nothing can auto-flag on spec change.
2. **Even on the branch, drift is WHOLE-REPO-HEAD, not dependency-aware.** `source_repo_shas = {repo: HEAD}`;
   `_repo_drifted` = True if ANY captured repo HEAD moved. So once croadfeldt/dcm main moves at all,
   **every** UC drifts — the *opposite* over-flagging failure vs Chris's requirement ("which UCs are stale
   should depend on what updates were done and if the UC was affected").
3. **Known data bug:** `source_repo_shas` was null (0/753) — so even coarse drift didn't compute.

### Required design — DEPENDENCY-AWARE (path/element-level) staleness
A UC is `stale_drifted` iff the **spec files/elements it depends on** are in the **changed set** between the
captured eval SHA and current HEAD. Build:
- **Capture per-UC spec dependencies at eval time** — the set of spec file paths (+ content SHAs) the UC was
  actually evaluated against. Source: the UC's declared `spec_refs` and/or the engine's
  `capabilities_invoked`/anchors that resolve to spec files. (Today only repo HEADs are captured.)
- **Compute the changed-file set** captured→current per repo (GitHub compare / `git diff --name-only`).
- **Stale iff** `{UC's dependency paths} ∩ {changed paths} ≠ ∅`. Optionally element-level (anchor/section)
  for finer than file granularity.
- Surface `stale_drifted` + **which files caused it** (the "affected by" explanation Chris wants).
- This supersedes the whole-repo-HEAD check; keep `stale_edited` as-is.

### Net work (proposed)
(1) **Deploy the existing freshness system** (merge/cherry-pick #114–117 from `feat/tenant-aware-migrations`
to main, fix the null `source_repo_shas` capture). (2) **Upgrade drift to dependency-aware** per above.
(3) Then re-run so evals capture the new per-UC dependency fingerprint. This is the core of #128 +
"make DAV relevant again." Decision needed: how to determine UC→spec-file dependency (declared spec_refs
vs model-emitted anchors vs both).

---

## DEPLOYED 2026-06-29 — dependency-aware staleness (correctly baselined on the LIVE branch)

**Correction to the root-cause note above:** the live DAV is NOT `main` — it runs `feat/tenant-aware-migrations`
(deployed `main.py` is byte-identical to that branch; migrations to **026**, tenancy Phase-2 schema-per-tenant
via `db_bootstrap.py`). So the whole-repo-HEAD freshness (#114-117) IS deployed — but **inert**, because
`source_repo_shas` is NULL on **0/819** analyses (the capture bug). That's why Chris saw "all valid."

A first attempt built off `main` (migration 017) **crashed the new pod** (collided with deployed
`migrate_017_capability_catalog`; `main`'s read_text() boot path is obsolete). No outage — old pod kept serving.
Stabilized by rebuilding `:latest` from clean `feat/tenant-aware-migrations`, then **re-baselined the feature**:

- Branch `feat/dependency-aware-staleness-v2` off `feat/tenant-aware-migrations`, commit `8295b61`.
- Migration is a **CLIENT_MIGRATION** (`migrate_t003_uc_spec_deps.sql`, registered in `db_bootstrap.CLIENT_MIGRATIONS`)
  because `uc_analyses`/`files` are tenant-schema tables. Applied per tenant (default/acme_val/flightpath), ledgered.
- `uc_analysis_spec_deps` table + `uc_spec_drift` view (content-SHA-per-file, mirrors `review_drift`).
- Ingest captures each analysis's emitted `spec_refs` → resolved corpus file path + content SHA at eval time.
- `/api/freshness` + `uc-latest` now compute `stale_drifted` from `uc_spec_drift` (TARGETED), return the
  drifted-file list (`affected_files`/`drifted_files` = the "affected by"), and demote whole-repo-HEAD to the
  informational `stale_repo_moved` (NOT folded into `stale`). Both endpoints stay project-scoped.
- Built (binary build 357), rolled out clean, migration verified in all 3 tenant schemas. DEPLOY COMPLETE.

### The re-run is REQUIRED and is a baseline RESET (not a drift-surfacer) — HELD for Chris's A/B review
The 819 existing analyses (143 UCs; project 20 = 118, project 727 = 13) have **`source_repo_shas` NULL 0/819**
AND the `files` table was **re-synced to the current spec TODAY** (all `last_seen_at`=2026-06-29). So there is
**no recoverable record of which spec version they ran against** — they cannot be retroactively flagged
drift-stale by any mechanism, and a *re-ingest* would falsely stamp `file_sha256_at_eval` = today's SHA → "fresh".
→ **Only a stage-2 re-run** (eval against current spec) populates correct dependency fingerprints; it both brings
DAV current AND makes every FUTURE spec change targetedly flag only the affected UCs. It's a heavy GPU job
(~118 UCs × samples) and the **A/B-gated step Chris watches** (stage-2 has regressed 32/32 before) — so it was
NOT fired unattended. **Recommended:** kick from the UI on project 20, a **bounded Set first** (validate the
new build's eval quality + confirm `uc_analysis_spec_deps` populates + `/api/freshness` lights up `affected_files`),
A/B vs the current baseline, then the full project-20 sweep on Chris's go.

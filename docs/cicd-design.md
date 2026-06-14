# DAV CI/CD pipeline — design

_Captured 2026-06-13. Status: design (to build, staged in gitops, not yet applied). Tracks #143._

## Why
Today DAV deploys via a manual loop: an operator hand-runs the gate
(`python -m compileall` · `check_routes.py` · `bash lint.sh`) then `ansible-playbook --tags
review-console|engine`, which **binary-builds from the working tree** (`DAV_REPO_DIRTY=true`).
That's fine for one operator but it's manual, ungated, and ships uncommitted trees. CI/CD makes
every deploy a **green, committed SHA**, and makes the gate the **merge gate**. Subsumes #99.

## Choice: OpenShift Pipelines (Tekton)
RH-native, and DAV already runs Tekton in ns `dav` (the run-corpus tasks) — so this reuses the
platform rather than adding GitHub Actions. (GitHub Actions remains the lighter fallback for the
CI/validate half if cluster-webhook wiring is undesirable.)

## Pipeline
**Trigger** — GitHub webhook → Tekton `EventListener`/`TriggerBinding`/`TriggerTemplate` on push +
pull_request to `croadfeldt/dav`.

**CI (every push + PR — gated):**
1. `clone` — git-clone the PR/commit.
2. `validate` —
   - `python -m compileall review-console/api/app engine/src/dav/ai/prompts.py`
   - `cd review-console/api && python check_routes.py` (route-shadow guard)
   - `cd review-console/api && python check_migrations.py` (**migration-wiring guard** — every
     `migrate_0NN_*.sql` is declared + executed in `lifespan`, numbering contiguous, BEGIN/COMMIT
     balanced; migrations run on boot with no isolation, so an unwired/unbalanced one is
     outage-class. Added with the maturity-wall epic / migration 021; wired into the deploy play.)
   - `cd review-console/ui && bash lint.sh` (node --check + eslint + jsdom e2e — the 60+ assertion gate)
   - _(future)_ **migration-applies smoke** — spin a throwaway Postgres, apply `schema.sql` + all
     migrations, assert clean (catches SQL that `check_migrations.py`'s static checks can't — the
     reason migration 021 is wrapped in try/except + verified via boot logs until this exists).
3. report status back to GitHub (PR check). **Red blocks merge.**

### Per-epic test surface (keep the gate matching the code — #144 / "update CI/CD testing to match")
As each epic lands, the gate grows with it:
- **Maturity Wall (#147):** `check_migrations.py` covers migration 021 wiring; new endpoints
  (`/api/assessment-frameworks*`, `/api/assessments/{id}/maturity-wall`, `/score`) are covered by
  `check_routes.py` (route count) + targeted `e2e.mjs` assertions for the wall sub-view as slices
  2–3 ship.
- The validate gate is the **single source of "is it shippable"** — every new view gets an
  `e2e.mjs` assertion, every new migration is caught by `check_migrations.py`, every new route by
  `check_routes.py`.

**CD (on merge to the release branch / a tag — only if CI green):**
4. `build-api` / `build-ui` — binary builds of the API + UI images (what ansible does today),
   tagged with the **commit SHA** → push to the internal registry / ImageStreams.
5. `deploy` — apply `schema.sql` (idempotent, on boot) + roll out the new images; gate on
   `oc rollout status` for `dav-review-api` + `dav-review-ui`. Reuse the existing ansible
   `review-console`/`engine` logic (wrapped as a Tekton task) or port the rollout steps directly.
6. (optional) tag the release + update the `review-console-design.md` version.

**Result:** no dirty-tree deploys; the hand-run gate becomes the merge gate; every rollout is a
green commit; rollback = redeploy a prior SHA's images.

## To build / what's needed from Chris
- A GitHub **webhook secret** + the `EventListener` route reachable from GitHub (or a **poller**
  Task on a schedule if exposing the route is undesirable).
- **Registry push creds** (the registry the API/UI ImageStreams pull from).
- The **deploy-trigger branch** (everything is currently on `feat/dcm-uc-prioritization`; pick the
  branch that triggers CD — likely `main` after this feature branch merges).
- Decision: **Tekton end-to-end**, or **GitHub Actions for CI + ansible/Tekton for CD** (hybrid).

Staged in the **gitops repo** (`ocp-cluster`), reviewed + applied by Chris (same pattern as the KMM
+ other cluster changes) — not auto-applied.

## Related
`#99` (automated QA validation — this IS it) · `#144` (proper-project umbrella) ·
`review-console-design.md` (the gate + deploy details) · `user-guide.md`.

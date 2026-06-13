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
   - `cd review-console/ui && bash lint.sh` (node --check + eslint + jsdom e2e — the 57+ assertion gate)
3. report status back to GitHub (PR check). **Red blocks merge.**

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

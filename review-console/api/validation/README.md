# review-console validation harnesses

The QA validation pipeline (task #99). Three layers:

1. **Code (pre-deploy, static).**
   - Python: `python3 -c "compile(open(f).read(), f, 'exec')"` on changed modules
     (catches `await`-outside-`async` that `ast.parse` misses).
   - **UI: `review-console/ui/lint.sh`** — `node --check` (syntax) **+ ESLint `no-undef`**
     on the inline JS. The `no-undef` step catches the class `node --check` misses: a
     handler calling a function that doesn't exist (e.g. `hasPriv(...)` instead of `can`,
     or `log_warn?.(...)`) → a runtime `ReferenceError` that can abort a whole init path.
     Both of those real bugs were found the day the lint was added. **Run before every UI
     deploy.**
   - Schema/migrations: ephemeral-Postgres harness (spin `postgres:16` via podman, apply
     migrations + schema.sql, assert) — currently ad-hoc; formalize here.
2. **Output / integration (post-deploy):** the in-pod smoke harness below.
3. **UI/UX (planned):** headless-browser e2e — assert key elements render per role, smoke
   each nav view, fail on console errors at boot (would have caught the presence-chip /
   `hasPriv` regression).

## In-pod smoke harnesses

Permanent **in-pod integration / post-deploy smoke** harnesses — distinct from the
pytest unit tests in `review-console/api/test_*.py`. These run inside the deployed API pod
and exercise the real code against the **live Postgres + run-workspace PVC**. Read-only
checks against live data; every write test runs inside a **throwaway project deleted
(CASCADE) at the end**, so production data is never touched.

## Run after a deploy
```sh
POD=$(oc get pods -n dav -o name | grep review-api | grep -v build | head -1 | cut -d/ -f2)
oc cp review-console/api/validation/qa_validate.py "dav/$POD:/tmp/qa_validate.py"
oc exec -n dav "$POD" -- python3 /tmp/qa_validate.py
```
Prints `N PASS / M FAIL` plus per-check lines (detail shown for non-PASS).

## Harnesses
- **`qa_validate.py`** — capability-catalog collapse, F7 assessment ingestion, F8 prompt
  management (incl. Review/Enhancement split + `prompt.manage` RBAC), capability taxonomy
  + normalize, and the static-A/B comparator over real runs.

## Adding checks
Call `ck(name, condition, detail)` in the relevant section; group a new feature under its
own `── <feature> ──` block. Keep any write test inside the throwaway-project scope so it
self-cleans. The harness should grow with every feature — run it after each deploy.

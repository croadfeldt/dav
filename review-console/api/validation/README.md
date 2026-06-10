# review-console validation harnesses

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

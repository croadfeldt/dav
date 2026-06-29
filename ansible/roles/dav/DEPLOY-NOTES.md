# DAV deploy notes (review-console) — operational facts

_2026-06-29. How DAV is deployed + the gotchas that bite. Keep current._

## How it deploys
- **Ansible role `ansible/roles/dav`** owns the deploy (NOT a standalone script).
  - `tasks/review_console.yaml` creates the BuildConfigs (binary, Containerfile),
    Deployments (from `templates/review-console-{api,ui,db}-deployment.yaml.j2`),
    Services, routes.
  - Applied with `kubernetes.core.k8s` **server-side apply** (field_manager: ansible,
    force_conflicts: true) — so it will **reassert** template values over live edits.
  - On update it runs **`oc rollout restart deployment/dav-review-{api,ui}`**.
- **Fast iteration without a full ansible run** = in-cluster **binary build from the
  working tree**, then the imagestream trigger rolls the deploy:
  ```
  oc start-build dav-review-api -n dav --from-dir=review-console/api    # Containerfile build
  oc start-build dav-review-ui  -n dav --from-dir=review-console/ui
  ```
  The API runs schema.sql + migrations on startup (tenant-aware via `db_bootstrap.py`).

## Namespace / workloads
- Namespace **`dav`**. Cluster **api.ocp.roadfeldt.com**. URL **dav.roadfeldt.com (LAN LB 10.0.90.22:8843)**.
- Deployments: `dav-review-api`, `dav-review-ui`, `dav-review-db`, `dav-docs-mcp`,
  `dav-recording-worker`. BuildConfigs are binary.

## Storage — RWX CephFS (external ODF)
- `dav-review-api` mounts **RWX CephFS** PVCs: **`dav-workspace`**, **`dav-uc-repos`**.
  RWX = ReadWriteMany → **multiple pods mounting simultaneously is supported by design**,
  so **RollingUpdate is correct** (two pods briefly during a roll is fine). Strategy is
  RollingUpdate (maxSurge 1 / maxUnavailable 0). UI also RollingUpdate (configMaps only).
- Storage is **`ocs-external-storagecluster` (external ODF)** — Ceph/MDS/mons/**mgr** live
  OUTSIDE this OCP cluster. So **no MDS/mon/mgr/tools pods in `openshift-storage` is NORMAL**;
  check health via `oc -n openshift-storage get cephcluster -o jsonpath='{.items[0].status.ceph.health}'`
  (but note it can read a **stale HEALTH_OK** — verify the external Ceph directly when in doubt).
- **Do NOT churn API pods** (repeated `oc delete pod`) when a rollout is stuck on a mount —
  pod-churn spawns more stuck CSI mount ops and makes it worse. Fix the backend first (below).

> **2026-06-29 misdiagnosis, corrected:** a stuck rollout was first blamed on RollingUpdate
> "multi-attach contention" and the strategy was switched to Recreate. That was WRONG — RWX
> supports multi-attach. The real cause was a **downed external Ceph mgr** stalling all CSI
> mounts. Once an active mgr was restored, the pod mounted and RollingUpdate worked. Strategy
> reverted to RollingUpdate (Recreate also caused needless deploy-time downtime).

## Recovering a stuck CephFS mount (Init:0/1, FailedMount)
Symptom: new `dav-review-api` pod stuck `Init:0/1`; events show
`MountVolume.MountDevice failed ... DeadlineExceeded` then `... already exists`.

**FIRST, check the Ceph backend — `DeadlineExceeded` is usually the cluster, not the node.**
If CephFS mounts hang *cluster-wide*, the external Ceph likely has **no active mgr** (CSI
operations stall without it). `status.ceph.health` can still read stale `HEALTH_OK`. Verify on
the external Ceph (active mgr present?) before touching anything in-cluster. With mgr restored,
the stuck pod mounts on its own (Recreate = one pod retrying) — no node action needed.
(2026-06-29 incident: the hang was a downed Ceph mgr, not a node op-lock.)

If it's genuinely node-local (one node only, backend healthy), order of remedies (least → most invasive):
1. **Wait** ~5–15 min — the hung kernel mount op times out and the pod's own retry
   succeeds (Recreate means only one pod is trying).
2. If it won't clear, **restart the CephFS CSI nodeplugin on the affected node** to
   drop the stuck op-lock (needs cluster-admin; affects other CephFS mounts on that
   node briefly):
   ```
   oc -n openshift-storage get pods -o wide | grep cephfsplugin   # find pod on the node
   oc -n openshift-storage delete pod csi-cephfsplugin-<id>
   ```
   The `csi-cephfsplugin` DaemonSet pods have a history of frequent restarts on this
   cluster — latent CephFS-CSI flakiness, watch for it.
3. Confirm: `oc rollout status deploy/dav-review-api -n dav`.

## Smoke test after deploy
```
TOK=$(cat ~/.claude-personal/.dav-token)
curl -sk -H "Authorization: Bearer $TOK" -H "X-DAV-Project: 20" \
  https://10.0.90.22:8843/api/use-cases?source=corpus | python3 -m json.tool | head
```

## Corpus refresh (UCs not showing after adding a repo)
- Corpus UCs are read from the **`files`** table, populated by `sync_corpus_files()`:
  boot + **hourly** loop + corpus webhook + manual. As of 2026-06-29, **adding/projecting
  a corpus repo auto-resyncs** (POST/PUT `/api/repos`, `/api/repos/project`).
- Manual refresh: **Config → "↻ Resync corpus cache"** button, or `POST /api/corpus/resync`.
- Filter UCs to one repo/branch: UC list **repo/branch dropdown** (→ `/api/use-cases?namespace=<ns>`);
  with a namespace filter, same-uuid UCs are NOT collapsed, so a branch's edited versions show.

# Tenancy collapse runbook — schema-per-tenant → single `public`

**Status:** STAGED. Operating-model DR §5. One-time, **supervised, backup-first**. Do **not** run
under an active analyze run or while users are mid-edit. ~1–2 min API blip during the cutover.

## Why
DR §5 ratified: collapse schema-per-tenant to a single `public` schema + `project_id` scoping (real
isolation later = separate deployment, not schema-per-tenant). Removes the per-tenant `search_path`
plumbing that has cost reliability for zero current benefit.

## ⚠️ The coupling you must understand first
The **deployed API image is from the never-merged tenant-aware (Phase-2) branch**: it pins
`SET search_path = tenant_flightpath, public` on every DB connection (`_pool_setup`). **Current `main`
does not** — it uses a plain `public` pool. Therefore:

> **Rebuilding/deploying the API from `main` IS the search_path cutover.** If you deploy `main` while
> the data is still in `tenant_flightpath`, DAV comes up **empty**. The data move and the API deploy
> must happen together, in the order below.

(This also means the deferred "PRs #9/#10 deploy on next API build" must NOT happen until this runbook
runs — rebuilding from main = this cutover.)

## Live state (verified 2026-06-30)
- `public` — 39 control-plane tables (projects, rbac_*, customers, frameworks, capability taxonomy…).
- `tenant_flightpath` — 35 client tables + 17 sequences + 1 view = **the live data** (132 managed UCs,
  89 analysis_runs, 909 uc_analyses). **0 name collisions** with public → clean `SET SCHEMA` move.
- `tenant_default`, `tenant_acme_val` — test tenants, no real data → dropped.

## Pre-flight
- [ ] Announce a short maintenance blip; ensure **no analyze run is in flight** (`oc get pipelinerun -n dav`).
- [ ] Confirm `main` is the intended API source and the image builds clean.

## Step 1 — FRESH backup (non-negotiable)
```
oc exec -n dav <pg-pod> -- pg_dump -U dav_review -d dav_review -Fc -f /tmp/dav-pre-collapse.dump
oc cp dav/<pg-pod>:/tmp/dav-pre-collapse.dump ./dav-pre-collapse-$(date +%Y%m%dT%H%M%S).dump
# verify the file is non-trivial in size before proceeding
```
(There is also a daily pg_dump, but take a fresh one immediately before.)

## Step 2 — Move client data → public (atomic, metadata-only)
```
oc exec -n dav -i <pg-pod> -- psql -U dav_review -d dav_review < review-console/deploy/tenancy-collapse.sql
```
The script is transactional and self-checks (tenant_flightpath empty after; public.managed_use_cases
visible). It does **not** drop the tenant schemas yet (that's Step 5, after verify).

## Step 3 — Deploy the API from `main` (the search_path switch)
```
oc start-build dav-review-api -n dav --from-dir=review-console/api --follow
oc set env deploy/dav-review-api -n dav DAV_RUNTIME_SEARCH_PATH-     # remove the now-moot env
oc rollout restart deploy/dav-review-api -n dav
oc rollout status deploy/dav-review-api -n dav --timeout=180s
```
(Removing the env is cosmetic on a `main` image — it doesn't read it — but keeps the deploy clean.)

## Step 4 — Verify
- [ ] `GET /api/health` 200; UI loads with all projects/UCs/analyses present.
- [ ] Row counts match the backup: `SELECT count(*) FROM public.managed_use_cases;` (= 132), analysis_runs (89), uc_analyses (909).
- [ ] A scoped analyze run lists/loads UCs.
- [ ] No `relation does not exist` / `search_path` errors in `oc logs deploy/dav-review-api -n dav`.

## Step 5 — Drop the empty tenant schemas (only after Step 4 passes)
Uncomment the `DROP SCHEMA …` block at the bottom of `tenancy-collapse.sql` (verify the
`public.tenants` slug names first), then run it.

## Rollback (if Step 4 fails)
The move is reversible, but the clean path is restore-from-backup:
```
# scale API to 0, restore, then redeploy the PREVIOUS (tenant-aware) image
oc scale deploy/dav-review-api -n dav --replicas=0
oc exec -n dav -i <pg-pod> -- pg_restore -U dav_review -d dav_review --clean --if-exists < ./dav-pre-collapse-*.dump
oc rollout undo deploy/dav-review-api -n dav    # back to the tenant-aware image
oc scale deploy/dav-review-api -n dav --replicas=1
```
(Or manual reverse-move: `ALTER TABLE public.<t> SET SCHEMA tenant_flightpath` for the 35 client
tables + re-set `DAV_RUNTIME_SEARCH_PATH` — but restore-from-backup is safer.)

## Follow-ups (separate slices)
- Code cleanup on `main`: the search_path/per-tenant machinery is already absent from `main`; confirm
  the boot path + migration runner have no dangling tenant assumptions.
- Drop the `tenants` table + tenant RBAC scope once nothing references it (DR §5 supersedes #217/#199/#200).
- Reconcile main vs the deployed Phase-2 code so the repo is the source of truth again.

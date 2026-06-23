# Tenancy Phase 2 — the tenant-aware boot runner (resolves the post-move boot crash)

**Status: 2026-06-23 — IMPLEMENTED + dry-run-validated against a restored prod copy. NOT deployed
(needs the lifespan wire-in below + a boot-test, and ONE design decision — customer placement).**
Supersedes the "revert recommended" path in `tenancy-phase2-runbook.md`: Chris chose to build the
genuine schema-aware boot ("use case sets could live in any tenant — DAV needs to handle that cleanly").

## The crash (recap)
After the Mon-night data-move, the 36 client tables live in `tenant_flightpath`; control stays in
`public`. The legacy boot ran every migration + `schema.sql` under `search_path=public`, so
`migrate_004`'s `ALTER TABLE use_case_sets …` throws `relation "use_case_sets" does not exist`
(unguarded → CrashLoopBackOff). Runtime was already fine (`DAV_RUNTIME_SEARCH_PATH='tenant_flightpath,
public'`); only the **boot/schema-management path** was never made tenant-aware.

## The fix — schema-aware, run-once bootstrap
New module `review-console/api/app/db_bootstrap.py`:

- **Control pass** (`search_path=public`): applies `app/schema_control.sql` once.
- **Per-tenant pass**: for every non-archived `public.tenants` row, `CREATE SCHEMA IF NOT EXISTS
  tenant_<slug>` + applies `app/schema_client.sql` once under `search_path=tenant_<slug>,public`.
- **Tracking**: `public.schema_migrations(schema_name, version)` records what's applied. A schema that
  already has base tables (the live `public` / `tenant_flightpath`) is **adopted** — marked applied
  WITHOUT re-running base DDL — so the first boot is a no-op on existing data. Empty schemas (a new
  tenant) get the base applied.
- **On-demand**: `provision_tenant(conn, slug)` for the tenant-create path.
- Seeds are routed by the caller: `control_seeds(conn)` once in public; `client_seeds(conn, schema)`
  per tenant (search_path pre-set).

### Why generated base schemas (not a hand-split of schema.sql)
`schema.sql` is 1161 lines of interleaved control+client DDL + seeds (the DO block at 483–521 even
mixes `model_configs`/`model_defaults` (control) with `managed_repos` (client)). Hand-splitting is the
error-prone path the dry-run finding warned about. Instead `schema_control.sql` / `schema_client.sql`
are **generated from the live, validated DB** via `scripts/gen_base_schema.sh` →
`scripts/process_schema.py` (per-schema `pg_dump --schema-only`, then: client file strips
`tenant_flightpath.` so client tables + client→client FKs resolve via the per-tenant search_path while
`public.*` cross-schema FKs to control stay qualified; control→client cross-schema *views* — the legacy
`review_current`/`review_drift`/`file_current_status`, derived from the client `files`/`review_events` —
are relocated to the client file, transitively). Correct-by-construction.

## Cross-schema coupling audit (the real finding)
Enumerating every cross-schema FK on the live schema surfaced that the data-move **over-relocated 3
tables the design intends as control** (`tenancy-phase2-runbook.md` partition lists customers/
customer_projects as control, bundles* as platform), creating **5 control→client FKs** — control rows
depending on one tenant's data, which is incoherent under hard tenancy:

| control table | → client table (wrong) |
|---|---|
| `model_configs.bundle_attachment_id` | `tenant_flightpath.bundle_attachments` |
| `mcp_server_configs.bundle_attachment_id` | `tenant_flightpath.bundle_attachments` |
| `rbac_account_roles.customer_id` | `tenant_flightpath.customers` |
| `rbac_group_role_mappings.customer_id` | `tenant_flightpath.customers` |
| `rbac_groups.customer_id` | `tenant_flightpath.customers` |

**Reclassification fix (validated):** move `customers`, `customer_projects`, `bundle_attachments` back
to `public`. Dry-run on the restored copy → **0 control→client FKs remain**, 20 healthy client→control
FKs intact. New partition: **38 control / 33 client (+3 relocated views)**.

### ⚠ DECISION NEEDED FROM CHRIS — customer placement
There is a genuine tension the reclassification surfaces, and it's yours to settle:
- The sovereignty research baked into `schema.sql` (line ~388) says **"project + customer are strictly
  tenant-scoped"** → customers would be **client** (per-tenant).
- But global **RBAC** (`rbac_*`, control) references `customers.customer_id` for the customer×project
  access matrix → that needs customers in **control**.

These can't both hold as-is. Two coherent resolutions:
- **(A) customers = control (shared registry).** Simplest, unblocks now, what the runbook partition +
  the validated reclassification assume. Trade-off: a customer identity is visible across tenants
  (vendor-side registry), which softens the "customer is tenant-scoped" stance.
- **(B) customers = client (per-tenant), and move the customer×project RBAC association rows
  (`rbac_*.customer_id` grants) into the tenant schema too.** Honors sovereignty strictly; larger
  change (RBAC resolver must read tenant-scoped customer grants). Defer to tenant #2.

The committed base schemas + `db_bootstrap` assume **(A)** as the validated default. If you choose (B),
re-run `gen_base_schema.sh` after re-partitioning and bump the versions.

## Dry-run evidence (local podman postgres + prod dump, 2026-06-23)
- Fresh install: control pass exit 0 → 38 control tables; client pass exit 0 → 33 client tables + 3
  views in the tenant schema; **0 shadows both directions**; 20 client→control FKs resolve.
- `db_bootstrap.bootstrap()` against a reclassified prod restore: existing data **intact**
  (`uc_analyses=779`, `users=18` unchanged), `tenant_flightpath` adopted untouched, `tenant_default` +
  `tenant_acme_val` provisioned (33+3 each), `schema_migrations` populated; **idempotent** on re-run;
  `provision_tenant('newco')` → 33 base tables. `py_compile` clean.

## Deploy plan (Chris, eyes-on — DO NOT auto-apply)
1. **Backup** (already taken tonight: a fresh `-Fc` dump; the new `dav-db-backup` CronJob also runs daily).
2. **Reclassify** (eyes-on, in a txn) on prod, IF choosing option (A):
   ```sql
   BEGIN;
   ALTER TABLE tenant_flightpath.customers          SET SCHEMA public;
   ALTER TABLE tenant_flightpath.customer_projects  SET SCHEMA public;
   ALTER TABLE tenant_flightpath.bundle_attachments SET SCHEMA public;
   COMMIT;
   ```
   (FKs survive `SET SCHEMA` — tracked by OID — so cross-schema refs stay valid.)
3. **Wire `lifespan`** (see patch below), build from the working tree, deploy, **watch boot logs**
   (same discipline as migrations 021–026 "DB-verified post-deploy via boot logs").
4. **Verify**: `/api/me`, `/api/use-cases`, `/api/runs` return data; `public.schema_migrations` has rows
   for public + each tenant; `tenant_default`/`tenant_acme_val` exist (empty client schemas).
5. **Rollback**: the bootstrap only ADDS empty tenant schemas + a tracking table (it adopts existing
   data untouched), so rollback = redeploy the prior image + `DROP SCHEMA tenant_default, tenant_acme_val
   CASCADE; DROP TABLE public.schema_migrations;` (and reverse step 2 if needed). Backup is the backstop.

## The `lifespan` wire-in (apply with eyes-on + boot-test)
Replace the migration block (main.py ~364–436: the `MIGRATE_002..026` calls + `SCHEMA_PATH`) and the
seed calls (~437–449) with a single bootstrap call, routing the seeds by where their tables live:

```python
import db_bootstrap   # near the other app imports

async def _control_seeds(conn):
    await _seed_docs_mcp(conn)                                   # mcp_server_configs (control)
    try: await _capability_catalog.seed_dcm_taxonomy(conn)      # capability_taxonomy_* (control)
    except Exception: log.exception("DCM taxonomy seed failed (non-fatal)")
    try: await _maturity_seed.seed_default_framework(conn)      # assessment_frameworks (control)
    except Exception: log.exception("default maturity framework seed failed (non-fatal)")
    await _migrate_code_repo_configs(conn)                      # code_repo_configs (control)

async def _client_seeds(conn, schema):
    await _seed_corpus(conn)                                    # managed_use_cases (client)
    await _seed_managed_repos(conn)                             # managed_repos (client)
    await _backfill_uc_projections(conn)                        # managed_use_cases (client)

# in lifespan, replacing the old migration+schema+seed block:
async with pool.acquire() as conn:
    await db_bootstrap.bootstrap(conn, control_seeds=_control_seeds, client_seeds=_client_seeds)
```

**Check before boot-test:** confirm `_seed_corpus` / `_seed_managed_repos` / `_backfill_uc_projections`
operate on the **passed `conn`** (not a freshly-acquired pool connection — which would reset search_path
to `DAV_RUNTIME_SEARCH_PATH` and seed `tenant_flightpath` regardless of the loop's tenant). If any
acquires its own connection, pass the target schema or set its search_path explicitly. This is the one
spot the podman dry-run couldn't exercise (it has no FastAPI app), so it needs your boot-log check.

## Other changes still needed (not done tonight)
1. **Per-request search_path routing** (the runbook's "tenant #2" work): today a single global role/env
   search_path points every connection at `tenant_flightpath, public`. With ≥2 active tenants, the pool
   must set `search_path = tenant_<active>, public` per request (after auth resolves the tenant from the
   active project). Until then, the newly-provisioned `tenant_default`/`tenant_acme_val` schemas exist
   but aren't *served* — provisioning is inert/safe, exactly Phase-2a's intent.
2. **Customer placement decision** (A vs B above) — gates whether the committed base schemas are final.
3. **Legacy review-view relocation on the EXISTING tenant** (cosmetic): `bootstrap` adopts
   `tenant_flightpath` as-is, so its `review_*` views stay in `public` (cross-schema but functional).
   New tenants get them in-schema. Optional: relocate flightpath's to match.
4. **Fold the migration history**: migrations 002–026 are now captured in the generated base; they stay
   in the repo for provenance but the runner no longer iterates them. Future schema changes ship as
   routed migration files (control vs client) tracked in `schema_migrations`.

## Files
- `review-console/api/app/db_bootstrap.py` — the runner (validated).
- `review-console/api/app/schema_control.sql`, `schema_client.sql` — generated base schemas (validated).
- `review-console/api/scripts/gen_base_schema.sh`, `scripts/process_schema.py` — regeneration tooling.

# Tenancy Phase 2 — hard schema-per-tenant data plane (execution runbook)

**Status: ✅ RESOLVED 2026-06-23 — boot is now restart-safe via the tenant-aware runner.**
Neither revert (A) nor schema-qualify-in-place (B) below was taken; instead the boot path was rebuilt
to be schema-aware (control pass in `public` + per-tenant pass under `search_path=<tenant>,public`,
each tracked once in `public.schema_migrations`; existing schemas are *adopted* without re-running base
DDL). Deployed build #341+; DAV restarts cleanly against the post-move schema; pg_dump backup CronJob
live. See **`tenancy-phase2-tenant-aware-runner.md`** and `review-console/api/app/db_bootstrap.py`. The
landmine writeup below is retained for history — it is the problem this runner fixed.

---

**Historical (the landmine, 2026-06-21): ⛔ APPLIED BUT NOT RESTART-SAFE — REVERT RECOMMENDED.**
The 36 client tables ARE in `tenant_flightpath` and runtime queries work (the live pod routes via
`DAV_RUNTIME_SEARCH_PATH='tenant_flightpath, public'`). BUT the "verified live" was only ever tested on
the pod that booted *before* the table move (seamless cutover, no restart). The boot path was never
re-run against the post-move schema — and **it crashes when it is.** First post-move deploy (build #340,
2026-06-21) CrashLoopBackOff: `lifespan` runs all migrations + `schema.sql` under `search_path=public`
(the anti-shadow choice), but migrations 002-020 + schema.sql + seeds reference CLIENT tables
(`use_case_sets`, `run_sessions`, …) that now live in `tenant_flightpath` → `UndefinedTableError:
relation "use_case_sets" does not exist`. Worse, had it reached `schema.sql` line 436, its
`CREATE TABLE IF NOT EXISTS <client_table>` would have recreated EMPTY client shadows in `public`
(the same shadow flaw, mirrored). **Net: the API cannot restart.** Only the pre-move pod (`#339`,
`maxUnavailable=0` protects it) keeps it up; any eviction → unrecoverable boot crash.

This is precisely the state the DRY-RUN FINDING below said "must NOT run until schema.sql is
schema-qualified." The move got applied anyway; the boot-qualification work was not. **Two ways out:**
- **(A) Revert the data move** (recommended now): `ALTER TABLE tenant_flightpath.<t> SET SCHEMA public`
  for all 36, then `DAV_RUNTIME_SEARCH_PATH=public`. Mechanical, reversible (inverse of a validated
  move), removes the landmine, costs nothing (ONE tenant → physical isolation has nothing to isolate
  yet). Phase 1 logical tenancy (tenant entity, RBAC tier, groups, FlightPath owns dav+dcm) stays
  intact. Ready SQL + fresh backup below. Blocked from autonomous apply by the safety classifier
  (correct — needs eyes-on or a dry-run-on-restored-copy); Chris runs it eyes-on.
- **(B) Make boot schema-aware** (the genuine Phase 2 work, defer to tenant #2): add a
  `schema_migrations` tracking table so applied migrations don't re-run, AND split/schema-qualify
  `schema.sql` (control DDL → `public`/`control`, client DDL → `tenant_<id>`) so no single search_path
  can shadow either category. Only then is the physical split restart-safe.

**Fresh pre-revert backup:** `/Users/chris/dav-backups/dav-prerevert-20260621-231739.dump` (1.1M, -Fc).
**Ingest unblock (already live, independent of all the above):** the spec-decode 400 that started this
was fixed by inserting a project-727 `model_configs` row with `{"speculative_decoding":true}` (id=8) +
a platform-default NULL row (id=9); the running #339 pod's exact-match caps lookup now succeeds, so
re-running the DAV-project ingest works today. (A resolver NULL-fallback fix at `main.py:~3614` is
committed in the working tree but can only deploy once the boot landmine above is resolved.)

### (Original status, now historical) ✅ data move applied 2026-06-22 (commit fb497eb)
FlightPath's 36 client tables moved to `tenant_flightpath`; control/platform stay in `public`; runtime
routes via `DAV_RUNTIME_SEARCH_PATH='tenant_flightpath, public'`; boot forces `public` (no shadow).
Verified at the time: auth, 727=18 UCs, DCM=85 UCs, runs, writes, 0 data loss, 0 errors — but ONLY on
the pre-move-booted pod (the restart-safety gap above was not exercised). Dry-run on a restored copy
(podman) caught + fixed the boot-time CREATE-TABLE-shadow flaw for the control tables, but the
*symmetric* client-table shadow + the migration-references-client-table crash on a fresh boot were not
covered. Remaining: per-request search_path routing for tenant #2.
Phase 1 (logical tenant + groups + RBAC + UI) is shipped/verified. Phase 2 makes the isolation
*physical* (schema-per-tenant). This is a high-blast-radius migration of the live production DB that
DCM depends on (uc_analyses 753, uc_capabilities 1971, uc_gaps 1354, run_sessions 88, etc.), so it is
decomposed into a **safe additive 2a** and a **destructive windowed 2b**.

## Critical design finding (corrects the design doc)
`SET search_path = tenant_x, platform, public` resolves a table *name* to the FIRST schema that has
it — it does **NOT** union rows across schemas. So the "platform default + tenant override" pattern
(nullable project_id rows shared across tenants) does **not** survive a physical schema split for free.
Three options for the default-bearing tables (model_configs, assessment_frameworks, capability_taxonomy*,
output_templates, bundles, use_categories, rbac_roles/privileges):
- **(A) Keep default-bearing tables in a shared `platform`/`control` schema** (soft isolation for those
  rows only); per-tenant data tables are physically siloed. Pragmatic, recommended — the shared tables
  hold de-identified vendor IP (taxonomy, frameworks, role catalog), which the sovereignty research says
  MAY be shared. Client data tables are hard-siloed.
- (B) Denormalize: copy platform defaults into every tenant schema at provision + on change. Full
  isolation, but defaults drift / fan-out writes.
- (C) Explicit cross-schema UNION in every resolver. Most code churn; brittle.
→ **Recommend (A):** hard-silo the **client-data** tables per tenant; keep **control-plane** +
**de-identified platform-default** tables in shared schemas. This matches the research (client data
never pools; vendor IP may) and is the least brittle.

## Table partition (from the live schema)
- **control** schema (shared, one copy — identity/RBAC/tenancy): tenants, projects, customers,
  customer_projects, users, account_identities, user_invitations, user_settings, project_members,
  rbac_* (roles, privileges, role_privileges, account_roles, groups, group_members, group_roles,
  group_role_mappings), api_tokens, app_settings, credentials, code_repo_configs.
- **platform** schema (shared, de-identified vendor IP / defaults): capability_taxonomy_terms,
  capability_aliases, capability_antipatterns, assessment_frameworks + framework_categories/capabilities/
  states (seed templates), use_categories, output_templates, bundles/bundle_versions/bundle_items,
  model_configs/model_defaults/model_use_profiles/mcp_server_configs **where project_id IS NULL**
  (the project-specific rows move to the tenant — see split note).
- **tenant_<id>** schema (per tenant, client data, HARD siloed): managed_use_cases, use_case_sets,
  use_case_set_members, uc_customer_requests, run_sessions, analysis_runs, uc_analyses, uc_capabilities,
  uc_gaps, uc_capability_deps, run_diagnoses, analysis_output_cache, assessments + assessment_findings/
  capability_scores/framework_link, goals/goal_measures/goal_targets, themes, capability_catalog,
  project_stage_context, improvement_proposals, experiments, files, recording_jobs, review_events,
  lifecycle_events, pr_comments/pr_comment_poll_state/uc_pr_comment_links, bundle_attachments,
  managed_repos, and the **project-scoped rows** of the model/mcp config tables.
- **Split tables** (hold BOTH platform-default and project rows): model_configs, model_defaults,
  model_use_profiles, mcp_server_configs, assessment_frameworks, capability_catalog, output_templates,
  bundles. Resolver reads tenant rows; falls back to platform defaults via an explicit cross-schema
  read (option A keeps the default rows in `platform`, project rows in `tenant_<id>`).

## Routing
Per-request `SET search_path = tenant_<active>, platform, control` set right after auth resolves the
tenant (from the active project's tenant). The connection pool must reset search_path per checkout
(asyncpg: set on acquire / via a wrapper). The default tenant maps to `tenant_default` (or keeps
`public` renamed). Service/engine paths set the tenant explicitly.

## Decomposition
### Phase 2a — SAFE, additive, reversible (build first; no existing data moves)
1. Create `control` + `platform` schemas; create `tenant_default` (alias/rename of current `public`,
   OR a view layer) — but DON'T move data yet; existing app keeps using `public`.
2. Provisioning: on tenant-create, `CREATE SCHEMA tenant_<id>` + run the per-tenant table DDL into it
   (schema-parameterized schema.sql). New tenants get a real empty siloed schema.
3. search_path middleware behind a feature flag (`DAV_SCHEMA_PER_TENANT=off` by default) — no behavior
   change until flipped.
This is inert/reversible: new schemas exist but nothing reads them until 2b + the flag.

### Phase 2b — DESTRUCTIVE, windowed (explicit go/no-go each step)
1. **Backup** the DB (pg_dump) + verify restore on a scratch instance. Non-negotiable.
2. **Dry-run** the whole migration on a restored copy; diff row counts per table per target schema.
3. Maintenance window (DCM runs paused): move control/platform/tenant tables to their schemas;
   existing `public` data → `tenant_default` (it's all the default tenant today). Repoint FKs.
4. Flip `DAV_SCHEMA_PER_TENANT=on`; deploy the tenant-aware data layer (the code sweep: every raw
   `FROM <table>` stays as-is since search_path resolves it; only cross-schema default-fallback queries
   change — bounded to the split tables).
5. Verify: DCM project runs/analyses intact; a second tenant's data invisible from the first; residency
   query per tenant. Rollback = restore backup + flag off.

## The code sweep (smaller than it looks)
Because routing is via search_path, most `SELECT … FROM managed_use_cases` need **no change** (resolved
to the active tenant schema). The real changes: (a) pool sets search_path per request; (b) the
~8 split-table resolvers do an explicit tenant+platform read; (c) provisioning + migration scripts;
(d) cross-tenant control-plane queries (RBAC, tenants, projects) must qualify `control.` explicitly.

## ⛔ DRY-RUN FINDING (2026-06-22): the naive "move + search_path" migration is UNSAFE — do NOT apply
A full dry-run on a restored copy (local podman postgres, prod dump) proved the data move itself is
clean (753 analyses / 94 runs intact, FKs preserved, queries resolve). **But simulating an API reboot
broke it:** `schema.sql` runs on every boot and `CREATE TABLE IF NOT EXISTS` only checks the **first
schema in search_path**, not the whole path. So with `search_path = tenant_flightpath, public`, the
boot recreates EMPTY shadow copies of the **control tables** (users, projects, tenants, rbac_*) in
`tenant_flightpath`, which then shadow the real rows in `public` → the app sees **0 users / wrong
projects → total auth+data outage.** No search_path ordering avoids it (whichever schema is first, the
other category's tables get empty shadows). Verified minimal repro: a table only in `s2`, with
`search_path=s1,s2`, gets a shadow created in `s1` by `CREATE TABLE IF NOT EXISTS`.

**Conclusion:** schema-per-tenant requires **`schema.sql` (and the migrations) to be split / made
schema-qualified** — control+platform DDL targets the shared schema, client-table DDL targets the
tenant schema — so a reboot can't shadow. That is a real change to DAV's schema-management layer (how
tables get created/seeded), NOT a pure data move. It's the genuine Phase 2 work; the data move is the
easy 10%. Until that's built, the prod migration must NOT run. **Prod is untouched** (only the safe
FlightPath-tenant assignment was applied). The validated data-move SQL is retained below for when the
schema-split is in place.

## FlightPath migration — data-move SQL (validated on a copy; BLOCKED on the schema-split above)
State as of 2026-06-22: FlightPath tenant created; dav + dcm assigned to it; **100% of client data is
FlightPath's, default project empty** → no row-splitting; move the client-data tables wholesale to
`tenant_flightpath`, leave shared/control in `public`, set the role search_path. **No app code change**
(search_path makes table location transparent). Backup taken: `/Users/chris/dav-backups/dav-pretenancy-20260622-030403.dump`.

**Why it's paused:** the in-place `SET SCHEMA` on the live shared DB was blocked by the auto-apply
safety classifier (correct — no dry-run-on-copy was done), and the DB role lacks CREATEDB so a scratch
dry-run isn't possible from here. Execute with eyes-on (Chris) or grant the permission.

**Eyes-on procedure** (`oc -n dav exec -it deploy/dav-review-db -- psql -U $POSTGRESQL_USER -d $POSTGRESQL_DATABASE`):
```sql
BEGIN;
CREATE SCHEMA IF NOT EXISTS tenant_flightpath;
DO $$ DECLARE t text; client_tables text[] := ARRAY[
  'managed_use_cases','use_case_sets','use_case_set_members','uc_customer_requests','run_sessions',
  'analysis_runs','uc_analyses','uc_capabilities','uc_gaps','uc_capability_deps','run_diagnoses',
  'analysis_output_cache','assessments','assessment_findings','assessment_capability_scores',
  'assessment_framework_link','goals','goal_measures','goal_targets','themes','capability_catalog',
  'project_stage_context','improvement_proposals','experiments','files','recording_jobs','review_events',
  'lifecycle_events','pr_comments','pr_comment_poll_state','uc_pr_comment_links','bundle_attachments',
  'managed_repos','customers','customer_projects','audit_log'];
BEGIN FOREACH t IN ARRAY client_tables LOOP
  IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=t)
  THEN EXECUTE format('ALTER TABLE public.%I SET SCHEMA tenant_flightpath', t); END IF;
END LOOP; END $$;
-- verify counts look right BEFORE commit:
SELECT count(*) AS in_flightpath FROM pg_tables WHERE schemaname='tenant_flightpath';   -- expect ~36
SELECT count(*) FROM tenant_flightpath.uc_analyses;   -- expect 753
COMMIT;
ALTER ROLE <POSTGRESQL_USER> SET search_path = tenant_flightpath, public;
```
Then: `oc -n dav rollout restart deploy/dav-review-api` (picks up the new search_path), and verify
`/api/me`, `/api/runs`, `/api/use-cases` return data (the app's `FROM run_sessions` resolves to
`tenant_flightpath.run_sessions`). **Rollback:** `ALTER ROLE … SET search_path = public;` +
`ALTER TABLE tenant_flightpath.<t> SET SCHEMA public;` for each, or restore the dump.
FKs are preserved across SET SCHEMA (tracked by OID), so cross-schema FKs to public.projects/users stay valid.

> Note: with ONE tenant, a global role search_path suffices. The **per-request** search_path routing
> (the connection-model refactor) is only needed when tenant #2 onboards — and that's also when the
> isolation first has anything to isolate. So this migration physically siloes FlightPath's data now;
> the cross-tenant enforcement layer lands with tenant #2.

## Open decisions for Chris
1. **Approve option (A)** (hard-silo client data; keep control + de-identified defaults shared)?
2. **Build Phase 2a now** (safe/additive/flagged-off — provisioning + schemas, no data move)?
3. **2b is windowed + backup-gated** — confirm you want me to schedule/execute it with a maintenance
   window + a dry-run on a restored copy first (NOT inline on live prod without that).

-- tenancy-collapse.sql — collapse schema-per-tenant → single `public` schema (operating-model DR §5).
--
-- ONE-TIME, SUPERVISED, BACKUP-FIRST. NOT a boot migration (fresh installs create everything in public
-- from the start). Run by an operator during a quiet window, AFTER a fresh pg_dump, as part of the
-- runbook (docs/tenancy-collapse-runbook.md). Do NOT wire into the migration runner.
--
-- Context (verified live 2026-06-30):
--   • public            = 39 control-plane tables (projects, rbac_*, customers, frameworks, …).
--   • tenant_flightpath = 35 client tables (managed_use_cases, analysis_runs, uc_analyses, …) + 17
--                          sequences + 1 view — THE LIVE DATA (132 UCs / 89 runs / 909 analyses).
--   • tenant_default, tenant_acme_val = test tenants (no real data) → dropped.
--   • 0 name collisions between tenant_flightpath and public (client/control sets are disjoint),
--     so the move is a metadata-only ALTER … SET SCHEMA (no data copy, fast, atomic).
--
-- CUTOVER COUPLING: the DEPLOYED API image (tenant-aware Phase-2 branch) pins
-- `SET search_path = tenant_flightpath, public` per connection; current `main` uses a plain `public`
-- pool. So rebuilding/deploying the API from `main` IS the search_path switch. Sequence is therefore:
--   1) fresh backup  2) THIS script (move data → public)  3) deploy API from main (public pool)
--   4) verify  5) drop the now-empty tenant schemas (commented out below — run only after verify).
-- Running this WITHOUT then deploying main would leave the old pods looking for moved tables → errors,
-- so do them together in the window.

\set ON_ERROR_STOP on
BEGIN;

-- Move every table from tenant_flightpath → public. SET SCHEMA carries indexes, constraints, triggers,
-- and table-owned sequences along with the table; cross-schema FKs (client → public.projects) keep
-- resolving (now same-schema). Dynamic loop = robust to the exact table set.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'tenant_flightpath' ORDER BY tablename LOOP
    EXECUTE format('ALTER TABLE tenant_flightpath.%I SET SCHEMA public', r.tablename);
    RAISE NOTICE 'moved table %', r.tablename;
  END LOOP;
  -- Any standalone sequences not already moved with an owning table.
  FOR r IN SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'tenant_flightpath' LOOP
    EXECUTE format('ALTER SEQUENCE tenant_flightpath.%I SET SCHEMA public', r.sequence_name);
    RAISE NOTICE 'moved sequence %', r.sequence_name;
  END LOOP;
  -- Views last (depend on the now-moved tables).
  FOR r IN SELECT viewname FROM pg_views WHERE schemaname = 'tenant_flightpath' LOOP
    EXECUTE format('ALTER VIEW tenant_flightpath.%I SET SCHEMA public', r.viewname);
    RAISE NOTICE 'moved view %', r.viewname;
  END LOOP;
END $$;

-- Sanity inside the txn: tenant_flightpath must now be empty of relations.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
   WHERE ns.nspname='tenant_flightpath' AND c.relkind IN ('r','S','v');
  IF n <> 0 THEN
    RAISE EXCEPTION 'tenant_flightpath still has % relation(s) after move — aborting', n;
  END IF;
  -- And the live client data must now be visible in public.
  PERFORM 1 FROM public.managed_use_cases LIMIT 1;
END $$;

COMMIT;

-- ── After deploying the API from main + verifying (separate runbook step) — DROP the empty tenants.
-- Left commented so this script is safe to run before the verify gate. Uncomment + run when ready:
--   DROP SCHEMA IF EXISTS tenant_flightpath CASCADE;   -- now empty (moved above)
--   DROP SCHEMA IF EXISTS tenant_default     CASCADE;   -- test tenant
--   DROP SCHEMA IF EXISTS tenant_acme_val    CASCADE;   -- test tenant
--   DELETE FROM public.tenants WHERE slug IN ('default','acme_val');  -- control-plane tenant rows (verify names first)

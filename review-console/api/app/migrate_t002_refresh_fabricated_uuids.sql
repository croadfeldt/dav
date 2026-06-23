-- Tenant migration t002 — refresh fabricated UC ids (#199 / uuid hygiene).
-- The bulk-extraction LLM emitted uuid-SHAPED but non-uuid4, templated, collision-prone ids
-- (e.g. uc-8a2f5c3b-1234-5678-90ab-cdef12345678). The server now owns UC identity; this refreshes
-- the existing ones to real uc-<uuid4> and repoints every reference. Idempotent: once all such ids
-- are valid uuid4, the loop is a no-op.
--
-- Detection: uuid-shaped (8-4-4-4-12 hex) AND NOT a valid uuid4 (version nibble 4, variant 8/9/a/b).
-- This deliberately EXCLUDES handle-style ids (uc-sov-004, uc-vm-provision-happy) — those are
-- intentional and must NOT change.
--
-- Cascade safety: the only FK-bearing children (lifecycle_events, uc_customer_requests) have no rows
-- for the affected set, so updating managed_use_cases first then the FK-less uc_uuid columns is safe.
-- We repoint EVERY table in the tenant schema that has a uc_uuid column (dynamic — no missed table).
BEGIN;

DO $$
DECLARE r RECORD; t TEXT; new_id TEXT;
BEGIN
  FOR r IN
    SELECT uuid FROM managed_use_cases
    WHERE uuid ~ '^uc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND uuid !~ '^uc-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  LOOP
    new_id := 'uc-' || gen_random_uuid()::text;
    UPDATE managed_use_cases
       SET uuid = new_id,
           yaml_content = regexp_replace(yaml_content, '^([ \t]*uuid:[ \t]*).*$', '\1' || new_id, 'm')
     WHERE uuid = r.uuid;
    FOR t IN
      SELECT table_name FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND column_name = 'uc_uuid'
        AND data_type = 'text'            -- skip non-text uc_uuid (e.g. uc_pr_comment_links is uuid;
        AND table_name <> 'managed_use_cases'  -- it can't hold a uc-<id> string anyway)
    LOOP
      EXECUTE format('UPDATE %I SET uc_uuid = $1 WHERE uc_uuid = $2', t) USING new_id, r.uuid;
    END LOOP;
  END LOOP;
END $$;

COMMIT;

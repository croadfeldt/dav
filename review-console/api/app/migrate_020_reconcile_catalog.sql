-- Migration 020: collapse the two capability catalogs into ONE (clean reconciliation).
--
-- The keystone (migration 017) created a parallel `capability_inventory` table that
-- duplicated the app's existing `capability_catalog` (schema.sql) — same concept, both
-- empty. We unify onto the EXISTING `capability_catalog`, extended additively into the
-- UDLM Knowledge-family Capability entity (the ADD COLUMNs live in schema.sql so they
-- apply to both fresh and deployed DBs, after migration 017 provides the taxonomy FK).
-- Here we only retire the duplicate.
--
--   handle      = cap_key (established natural key / UDLM handle)
--   lifecycle   = status (reused; 'observed' added for the four-state Discovered)
--   deps/spec   = depends_on / spec_refs (reused)
--   + UDLM      = family, normalized_to_term_id, normalization_status, created_via,
--                 evidence, provenance, classification (see schema.sql)
-- Identity = cap_key + the existing BIGINT surrogate (whole-system reuse; UUID is the
-- cross-system ideal, relaxed to reuse the established, UI-wired table). project-scoped;
-- the shared vocabulary lives in capability_taxonomy_terms. See capability_catalog.py.

BEGIN;
DROP TABLE IF EXISTS capability_inventory CASCADE;
COMMIT;

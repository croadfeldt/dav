-- t009: the corpus index — scope and predicted quarantine BEFORE launch.
--
-- P1 of docs/scope-first-class-plan.md. DAV never knew what a run would analyze
-- until the engine had started: scope was discovered at stage 2, quarantine in
-- a YAML on the PVC. One row per UC file per namespace, populated by the same
-- sweep that maintains the files cache, dimension-validated at index time
-- against the corpus's published vocabulary, SHA-stamped so staleness is
-- visible (ruling: sync-refresh + staleness marker, never a blocking re-index
-- at trigger).
--
-- valid IS NULL means "no vocabulary was available to validate against" —
-- unvalidated, NOT passing. Consumers must not render NULL as green.
CREATE TABLE IF NOT EXISTS corpus_index (
  id                BIGSERIAL PRIMARY KEY,
  namespace         TEXT NOT NULL,
  path              TEXT NOT NULL,
  uc_uuid           TEXT,
  handle            TEXT,
  family            TEXT,
  success_semantics TEXT,
  dimensions        JSONB,
  valid             BOOLEAN,
  invalid_reason    TEXT,
  repo_sha          TEXT,
  indexed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (namespace, path)
);

CREATE INDEX IF NOT EXISTS idx_corpus_index_ns_valid ON corpus_index (namespace, valid);

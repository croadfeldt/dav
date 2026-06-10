-- Migration 019: assessment ingestion (F7) — UDLM Knowledge family · Assessment + Finding.
--
-- Consumes the OUTPUTS of an existing assessment process (automation strategy,
-- hybrid-cloud, AI, …) and lands each finding on the capability catalog as an
-- OBSERVED capability (the four-state 'Discovered' — what the field shows),
-- normalized onto the taxonomy or flagged as a gap. The gap between OBSERVED
-- (assessed reality) and CANONICAL (target vocabulary) is the analysis.
--
-- The GENERIC mechanism only — no confidential data. Real per-format parsers and
-- engagement data live inside the work env (WORK/PERSONAL BOUNDARY, active-work.md).
-- See assessment_ingest.py, capability_catalog.py, udlm/entities/knowledge-family.md.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Assessment (a typed assessment import) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS assessments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle           TEXT NOT NULL,                       -- title / name
    version          INTEGER NOT NULL DEFAULT 1,
    is_current       BOOLEAN NOT NULL DEFAULT true,
    owned_by         TEXT,
    created_by       TEXT,
    created_via      TEXT NOT NULL DEFAULT 'manual',      -- import:<type> | manual
    lifecycle_state  TEXT NOT NULL DEFAULT 'OBSERVED',    -- assessments record observed reality
    family           TEXT NOT NULL DEFAULT 'dcm',
    assessment_type  TEXT NOT NULL DEFAULT 'generic',     -- automation | hybrid-cloud | ai | dcm | generic
    pillar           TEXT NOT NULL DEFAULT 'platform',
    source           TEXT,                                -- where it came from
    summary          TEXT,
    provenance       JSONB NOT NULL DEFAULT '{}'::jsonb,
    classification   TEXT NOT NULL DEFAULT 'client-confidential',
    scope_tier       TEXT NOT NULL DEFAULT 'project',
    project_id       BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    scope_tags       TEXT[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_assess_tier  CHECK (scope_tier IN ('global','shared','domain','project')),
    CONSTRAINT chk_assess_pillar CHECK (pillar IN ('platform','people-process','enablement'))
);
CREATE INDEX IF NOT EXISTS idx_assess_project ON assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_assess_type    ON assessments(assessment_type);

-- ── Finding (one observed capability state within an assessment) ─────────────
CREATE TABLE IF NOT EXISTS assessment_findings (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id        UUID NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    capability_handle    TEXT NOT NULL,                   -- the capability the finding is about
    category             TEXT,                            -- grouping of capabilities (anchors the UI columns)
    state                TEXT NOT NULL DEFAULT 'absent',  -- present | partial | absent | n/a (not asked/applicable)
    maturity             INTEGER,                         -- pure maturity 1..5 (NULL = none; see state for disposition)
    evidence             TEXT,
    notes                TEXT,
    pillar               TEXT NOT NULL DEFAULT 'platform',
    family               TEXT NOT NULL DEFAULT 'dcm',
    domain_prefix        TEXT,
    -- catalog linkage (normalization onto the controlled vocabulary)
    normalized_to_term_id UUID REFERENCES capability_taxonomy_terms(id) ON DELETE SET NULL,
    -- logical reference to capability_catalog(id); not a hard FK because that table
    -- lives in schema.sql (runs after migrations) so the constraint can't bind on a
    -- fresh DB. Set/cleared by assessment_ingest.py.
    catalog_capability_id BIGINT,
    normalization_status TEXT NOT NULL DEFAULT 'unmapped', -- normalized | proposed-taxonomy-gap | unmapped
    classification       TEXT NOT NULL DEFAULT 'client-confidential',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_finding_state CHECK (state IN ('present','partial','absent','n/a')),
    CONSTRAINT chk_finding_norm  CHECK (normalization_status IN ('normalized','proposed-taxonomy-gap','unmapped'))
);
-- Reconcile already-deployed assessment_findings (F7 shipped before these fields):
-- add `category`, and widen the state CHECK to include 'n/a'. Idempotent on every boot.
ALTER TABLE assessment_findings ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE assessment_findings DROP CONSTRAINT IF EXISTS chk_finding_state;
ALTER TABLE assessment_findings ADD CONSTRAINT chk_finding_state CHECK (state IN ('present','partial','absent','n/a'));
CREATE INDEX IF NOT EXISTS idx_finding_assessment ON assessment_findings(assessment_id);
CREATE INDEX IF NOT EXISTS idx_finding_capability ON assessment_findings(lower(capability_handle));
CREATE INDEX IF NOT EXISTS idx_finding_state      ON assessment_findings(state);
CREATE INDEX IF NOT EXISTS idx_finding_term       ON assessment_findings(normalized_to_term_id);
CREATE INDEX IF NOT EXISTS idx_finding_category   ON assessment_findings(category);
COMMENT ON TABLE assessments IS 'UDLM Knowledge family · Assessment (OBSERVED). Generic mechanism; confidential data inside work env.';
COMMENT ON TABLE assessment_findings IS 'UDLM Knowledge family · Finding. state=present|partial|absent; gaps drive the roadmap.';

COMMIT;

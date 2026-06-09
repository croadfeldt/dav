-- Migration 017: Capability Catalog ↔ Taxonomy — UDLM Knowledge family (the keystone).
--
-- Realizes the UDLM **Knowledge entity-type family** (udlm/entities/knowledge-family.md):
-- Capability, TaxonomyTerm, Alias, Antipattern as UDLM-conformant Data. Resolves the
-- "free-form capability strings, no controlled vocabulary" dependency.
-- Design: dav/docs/capability-catalog-design.md. Family concept + disambiguation:
-- udlm/foundations/entity-type-families.md. Case study:
-- udlm/docs/case-study-dav-knowledge-realization.md.
--
-- UDLM CONFORMANCE — every table carries the universal Data properties:
--   • id UUID                      — stable identifier across the lifecycle (identifier contract)
--   • artifact-metadata block      — handle, version, is_current, owned_by, created_by, created_via
--   • lifecycle_state              — the Knowledge family's CURATION four-state interpretation:
--                                    OBSERVED (Discovered) · PROPOSED (Intent) · UNDER_REVIEW
--                                    (Requested) · CANONICAL (Realized) · DEPRECATED (terminal)
--   • family                       — the per-row VOCABULARY/disambiguation namespace
--                                    (Drive [Computing] vs Drive [Automotive]); DCM-seeded = 'dcm'.
--                                    Distinct from the ENTITY-TYPE family (Knowledge) these tables
--                                    realize, which is a table-level property (documented here).
--                                    Grouping ≠ boundary: definitions are universal/free-to-use.
--   • provenance / classification  — field-level lineage (JSONB) + per-field classification;
--                                    canonical public vocab defaults 'public', engagement-derived
--                                    instances default 'client-confidential'.
--   • scope (ownership)            — INSTANCE concern only (ownership-sharing-allocation):
--                                    scope_tier global|shared|domain|project + project_id + scope_tags.
--
-- NOTE: the four states are realized here as one row + lifecycle_state (pragmatic pilot).
-- Full UDLM four-DOMAIN storage (append-only intent/requested, versioned realized w/ is_current,
-- ephemeral per-run discovered) is the next conformance increment, esp. for OBSERVED records
-- refreshed per assessment run. is_current/version are carried now so that step is additive.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ── TaxonomyTerm — the normalization authority (canonical vocabulary) ─────────
CREATE TABLE IF NOT EXISTS capability_taxonomy_terms (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- artifact-metadata block
    handle              TEXT NOT NULL,                       -- the term (stable handle)
    version             INTEGER NOT NULL DEFAULT 1,
    is_current          BOOLEAN NOT NULL DEFAULT true,
    owned_by            TEXT,
    created_by          TEXT,
    created_via         TEXT NOT NULL DEFAULT 'manual',      -- taxonomy-seed:dcm | backfill | manual
    -- lifecycle (curation four-state)
    lifecycle_state     TEXT NOT NULL DEFAULT 'PROPOSED',
    -- disambiguation namespace (vocabulary family), NOT the entity-type family
    family              TEXT NOT NULL DEFAULT 'dcm',
    -- domain payload
    definition          TEXT NOT NULL DEFAULT '',
    pillar              TEXT NOT NULL DEFAULT 'platform',    -- platform|people-process|enablement
    domain_prefix       TEXT,                                -- DCM Part-4 prefix: IAM, PRV, LCM, …
    domain              TEXT,
    category            TEXT,                                -- source section, e.g. 'Provider Types (11)'
    parent_id           UUID REFERENCES capability_taxonomy_terms(id) ON DELETE SET NULL,
    normalization_rules TEXT NOT NULL DEFAULT '',
    -- provenance + classification (UDLM contracts)
    provenance          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {field: {origin, source, by, at}}
    classification      TEXT NOT NULL DEFAULT 'public',
    field_classification JSONB NOT NULL DEFAULT '{}'::jsonb, -- per-field overrides
    -- instance scope / ownership (ownership-sharing-allocation) — NOT a definition constraint
    scope_tier          TEXT NOT NULL DEFAULT 'project',
    project_id          BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    scope_tags          TEXT[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_taxterm_state CHECK (lifecycle_state IN ('OBSERVED','PROPOSED','UNDER_REVIEW','CANONICAL','DEPRECATED')),
    CONSTRAINT chk_taxterm_tier  CHECK (scope_tier IN ('global','shared','domain','project')),
    CONSTRAINT chk_taxterm_pillar CHECK (pillar IN ('platform','people-process','enablement')),
    CONSTRAINT chk_taxterm_class CHECK (classification IN ('public','internal','confidential','client-confidential','classified'))
);
-- Disambiguation key is term + family (+ pillar/scope). One CURRENT CANONICAL per identity;
-- OBSERVED/PROPOSED may coexist (the four states are parallel — that gap is the analysis).
CREATE UNIQUE INDEX IF NOT EXISTS idx_taxterm_canonical
    ON capability_taxonomy_terms(family, pillar, scope_tier, COALESCE(project_id,0), lower(handle))
    WHERE lifecycle_state = 'CANONICAL' AND is_current;
CREATE INDEX IF NOT EXISTS idx_taxterm_family  ON capability_taxonomy_terms(family);
CREATE INDEX IF NOT EXISTS idx_taxterm_state   ON capability_taxonomy_terms(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_taxterm_project ON capability_taxonomy_terms(project_id);
CREATE INDEX IF NOT EXISTS idx_taxterm_domain  ON capability_taxonomy_terms(domain_prefix);
CREATE INDEX IF NOT EXISTS idx_taxterm_tags    ON capability_taxonomy_terms USING GIN(scope_tags);
COMMENT ON TABLE capability_taxonomy_terms IS
    'UDLM Knowledge family · TaxonomyTerm. Per-row family = vocabulary disambiguation namespace.';

-- ── Capability — the living inventory ─────────────────────────────────────────
-- The Capability entity is NOT a new table: it is the app's existing
-- `capability_catalog` (schema.sql), extended additively into the UDLM Knowledge
-- family by migration 020 (cap_key = handle, status = lifecycle, + family/
-- normalized_to_term_id/etc.). An earlier draft created a parallel
-- `capability_inventory` here; that duplicated capability_catalog and is retired by
-- migration 020. Gap analysis = CANONICAL taxonomy vs OBSERVED catalog (UDLM drift).

-- ── Alias — anti-vocabulary + discovered synonyms (normalization rules) ───────
CREATE TABLE IF NOT EXISTS capability_aliases (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle           TEXT NOT NULL,                          -- the string to normalize away ('avoid')
    version          INTEGER NOT NULL DEFAULT 1,
    is_current       BOOLEAN NOT NULL DEFAULT true,
    owned_by         TEXT,
    created_by       TEXT,
    created_via      TEXT NOT NULL DEFAULT 'manual',         -- taxonomy-antivocab | discovered | manual
    lifecycle_state  TEXT NOT NULL DEFAULT 'CANONICAL',
    family           TEXT NOT NULL DEFAULT 'dcm',
    use_instead      TEXT NOT NULL DEFAULT '',
    resolves_to_term_id UUID REFERENCES capability_taxonomy_terms(id) ON DELETE SET NULL,
    reason           TEXT NOT NULL DEFAULT '',
    pillar           TEXT NOT NULL DEFAULT 'platform',
    provenance       JSONB NOT NULL DEFAULT '{}'::jsonb,
    classification   TEXT NOT NULL DEFAULT 'public',
    scope_tier       TEXT NOT NULL DEFAULT 'project',
    project_id       BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    scope_tags       TEXT[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_alias_state CHECK (lifecycle_state IN ('OBSERVED','PROPOSED','UNDER_REVIEW','CANONICAL','DEPRECATED')),
    CONSTRAINT chk_alias_tier  CHECK (scope_tier IN ('global','shared','domain','project'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alias_canonical
    ON capability_aliases(family, pillar, scope_tier, COALESCE(project_id,0), lower(handle))
    WHERE lifecycle_state = 'CANONICAL' AND is_current;
CREATE INDEX IF NOT EXISTS idx_alias_family  ON capability_aliases(family);
CREATE INDEX IF NOT EXISTS idx_alias_term    ON capability_aliases(resolves_to_term_id);
CREATE INDEX IF NOT EXISTS idx_alias_tags    ON capability_aliases USING GIN(scope_tags);
COMMENT ON TABLE capability_aliases IS
    'UDLM Knowledge family · Alias. avoid(handle) → use_instead, resolves_to a TaxonomyTerm.';

-- ── Antipattern — patterns to avoid relative to the taxonomy/architecture ─────
CREATE TABLE IF NOT EXISTS capability_antipatterns (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle           TEXT NOT NULL,                          -- antipattern name
    version          INTEGER NOT NULL DEFAULT 1,
    is_current       BOOLEAN NOT NULL DEFAULT true,
    owned_by         TEXT,
    created_by       TEXT,
    created_via      TEXT NOT NULL DEFAULT 'manual',
    lifecycle_state  TEXT NOT NULL DEFAULT 'PROPOSED',
    family           TEXT NOT NULL DEFAULT 'dcm',
    description      TEXT NOT NULL DEFAULT '',
    why              TEXT NOT NULL DEFAULT '',
    instead          TEXT NOT NULL DEFAULT '',
    related_term_id  UUID REFERENCES capability_taxonomy_terms(id) ON DELETE SET NULL,
    pillar           TEXT NOT NULL DEFAULT 'platform',
    domain_prefix    TEXT,
    provenance       JSONB NOT NULL DEFAULT '{}'::jsonb,
    classification   TEXT NOT NULL DEFAULT 'public',
    scope_tier       TEXT NOT NULL DEFAULT 'project',
    project_id       BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    scope_tags       TEXT[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_antipattern_state CHECK (lifecycle_state IN ('OBSERVED','PROPOSED','UNDER_REVIEW','CANONICAL','DEPRECATED')),
    CONSTRAINT chk_antipattern_tier  CHECK (scope_tier IN ('global','shared','domain','project'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_antipattern_canonical
    ON capability_antipatterns(family, pillar, scope_tier, COALESCE(project_id,0), lower(handle))
    WHERE lifecycle_state = 'CANONICAL' AND is_current;
CREATE INDEX IF NOT EXISTS idx_antipattern_family ON capability_antipatterns(family);
CREATE INDEX IF NOT EXISTS idx_antipattern_term   ON capability_antipatterns(related_term_id);
CREATE INDEX IF NOT EXISTS idx_antipattern_tags   ON capability_antipatterns USING GIN(scope_tags);
COMMENT ON TABLE capability_antipatterns IS
    'UDLM Knowledge family · Antipattern. Patterns to avoid relative to the taxonomy/architecture.';

COMMIT;

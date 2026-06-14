-- Migration 021: Maturity Wall — goal-driven, backward-chained assessment substrate.
--
-- Models the Red Hat FlightPath-style assessment: a configurable maturity framework
-- (categories -> capabilities, 0-5 Function Appraisal scale), scored per assessment across
-- states (Current / phase targets / Desired), under an apex of GOALS (the desired business
-- outcomes everything derives backward from). See docs/maturity-wall-design.md.
--
-- Structural DDL only (tables/indexes/constraints) — additive, IF NOT EXISTS, idempotent.
-- The FlightPath framework seed + per-capability data land via a separate, verifiable pass
-- (Python seeder) so a seed bug can never roll back these tables. Builds on migration 019
-- (assessments / assessment_findings already carry category + capability_handle + maturity).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ══════════════ The apex: Goals / Outcomes ══════════════
-- Themes = Focus Areas (the deck's 3); group goals + carry theme-level desired-state rollups.
CREATE TABLE IF NOT EXISTS themes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT,
    ord         INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_themes_project ON themes(project_id);

-- A goal is a desired business outcome (apex). Unifies with the #120 Outcome
-- {statement, desired, current}. origin records which of the three sources it came from.
CREATE TABLE IF NOT EXISTS goals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    theme_id    UUID REFERENCES themes(id) ON DELETE SET NULL,
    statement   TEXT NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'human',   -- human | derived | customer
    description TEXT,
    owner       TEXT,
    priority    INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'open',     -- open | committed | achieved | dropped
    target_date DATE,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_goal_origin CHECK (origin IN ('human','derived','customer')),
    CONSTRAINT chk_goal_status CHECK (status IN ('open','committed','achieved','dropped'))
);
CREATE INDEX IF NOT EXISTS idx_goals_project ON goals(project_id);
CREATE INDEX IF NOT EXISTS idx_goals_theme   ON goals(theme_id);

-- ══════════════ The configurable maturity framework (the template) ══════════════
-- scale = ordered appraisal levels [{value,label,color}] (FlightPath: 0 Manual .. 5 Highly
-- Optimized, '-' Not Assessed). project_id NULL = a global seed template, copied per project.
CREATE TABLE IF NOT EXISTS assessment_frameworks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'active',
    is_seed     BOOLEAN NOT NULL DEFAULT false,
    scale       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_framework_project ON assessment_frameworks(project_id);
-- Idempotent seed anchor: at most one seed template per key (project_id NULL).
CREATE UNIQUE INDEX IF NOT EXISTS uq_framework_seed_key
    ON assessment_frameworks(key) WHERE project_id IS NULL;

-- Categories = the wall columns, grouped into bands, ordered, split by the Inflection Point.
CREATE TABLE IF NOT EXISTS framework_categories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    framework_id    UUID NOT NULL REFERENCES assessment_frameworks(id) ON DELETE CASCADE,
    key             TEXT NOT NULL,
    label           TEXT NOT NULL,
    band            TEXT,                              -- e.g. Automation as a Product / Platform Operating Model / Strategy
    ord             INTEGER NOT NULL DEFAULT 0,
    inflection_side TEXT NOT NULL DEFAULT 'pre',       -- pre | post (the Inflection-Point divider)
    CONSTRAINT chk_cat_inflection CHECK (inflection_side IN ('pre','post')),
    CONSTRAINT uq_cat_key UNIQUE (framework_id, key)
);
CREATE INDEX IF NOT EXISTS idx_cat_framework ON framework_categories(framework_id);

-- Capabilities = the wall rows within a category. catalog_capability_id is a LOGICAL link to
-- capability_catalog(id) (not a hard FK — that table lives in schema.sql, applied after
-- migrations) so the maturity wall shares the capability spine with the architecture roadmap.
CREATE TABLE IF NOT EXISTS framework_capabilities (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id           UUID NOT NULL REFERENCES framework_categories(id) ON DELETE CASCADE,
    key                   TEXT NOT NULL,
    label                 TEXT NOT NULL,
    ord                   INTEGER NOT NULL DEFAULT 0,
    catalog_capability_id BIGINT,
    CONSTRAINT uq_cap_key UNIQUE (category_id, key)
);
CREATE INDEX IF NOT EXISTS idx_cap_category ON framework_capabilities(category_id);

-- States = the rendered columns of the wall (Current / Phase 1/2/3 / Desired). Configurable.
CREATE TABLE IF NOT EXISTS framework_states (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    framework_id UUID NOT NULL REFERENCES assessment_frameworks(id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    label        TEXT NOT NULL,
    ord          INTEGER NOT NULL DEFAULT 0,
    kind         TEXT NOT NULL DEFAULT 'target',   -- current | target | desired
    CONSTRAINT chk_state_kind CHECK (kind IN ('current','target','desired')),
    CONSTRAINT uq_state_key UNIQUE (framework_id, key)
);
CREATE INDEX IF NOT EXISTS idx_state_framework ON framework_states(framework_id);

-- ══════════════ Per-assessment scoring (capability × state -> 0..5) ══════════════
-- Generalizes assessment_findings.maturity (single current value) to multi-state. The
-- 'current' state back-fills from findings; targets/desired are new. source guards LLM vs human.
CREATE TABLE IF NOT EXISTS assessment_capability_scores (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id           UUID NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    framework_capability_id UUID NOT NULL REFERENCES framework_capabilities(id) ON DELETE CASCADE,
    state_key               TEXT NOT NULL,           -- references framework_states.key
    maturity                SMALLINT,                -- 0..5; NULL = '-' Not Assessed
    rationale               TEXT,
    source                  TEXT NOT NULL DEFAULT 'human',  -- llm | human
    updated_by              TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_score_maturity CHECK (maturity IS NULL OR (maturity >= 0 AND maturity <= 5)),
    CONSTRAINT chk_score_source   CHECK (source IN ('llm','human')),
    CONSTRAINT uq_score UNIQUE (assessment_id, framework_capability_id, state_key)
);
CREATE INDEX IF NOT EXISTS idx_score_assessment ON assessment_capability_scores(assessment_id);
CREATE INDEX IF NOT EXISTS idx_score_capability ON assessment_capability_scores(framework_capability_id);

-- A goal's desired state = target maturity per capability (the Desired-State wall).
-- Gap(capability) = max(goal_targets.target_maturity) - current score.
CREATE TABLE IF NOT EXISTS goal_targets (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id                 UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    framework_capability_id UUID NOT NULL REFERENCES framework_capabilities(id) ON DELETE CASCADE,
    target_maturity         SMALLINT,
    weight                  REAL NOT NULL DEFAULT 1.0,
    rationale               TEXT,
    CONSTRAINT chk_target_maturity CHECK (target_maturity IS NULL OR (target_maturity >= 0 AND target_maturity <= 5)),
    CONSTRAINT uq_goal_target UNIQUE (goal_id, framework_capability_id)
);
CREATE INDEX IF NOT EXISTS idx_goal_target_goal ON goal_targets(goal_id);

-- How a goal is known to be achieved (the deck's "Measure By").
CREATE TABLE IF NOT EXISTS goal_measures (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id     UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    statement   TEXT NOT NULL,
    metric      TEXT,
    baseline    TEXT,
    target      TEXT,
    ord         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_goal_measure_goal ON goal_measures(goal_id);

-- Which framework an assessment is scored against (+ its chosen states live on the framework).
CREATE TABLE IF NOT EXISTS assessment_framework_link (
    assessment_id UUID PRIMARY KEY REFERENCES assessments(id) ON DELETE CASCADE,
    framework_id  UUID NOT NULL REFERENCES assessment_frameworks(id) ON DELETE CASCADE,
    linked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE goals IS 'Apex outcome (goal-driven backward chain). origin: human|derived|customer. See maturity-wall-design.md.';
COMMENT ON TABLE assessment_frameworks IS 'Configurable maturity framework (categories->capabilities + 0-5 scale + states). project_id NULL = seed template.';
COMMENT ON TABLE assessment_capability_scores IS 'capability x state -> 0..5 maturity. current back-fills from assessment_findings; source=llm|human.';

COMMIT;

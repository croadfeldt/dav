-- DAV per-tenant (client) base schema. GENERATED from the live DB by the tenancy
-- schema split; do not hand-edit — regenerate via scripts/gen_base_schema.sh.
-- Run-once per tenant schema under search_path=tenant_<slug>,public (tracked).
-- Unqualified names resolve to the tenant schema; public.* are cross-schema FKs to control.

CREATE TABLE review_events (
    id bigint NOT NULL,
    file_path text NOT NULL,
    reviewer text NOT NULL,
    action text NOT NULL,
    status text,
    notes text,
    file_sha256_at_review text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT review_events_action_check CHECK ((action = ANY (ARRAY['review'::text, 'update'::text, 'clear'::text]))),
    CONSTRAINT review_events_status_check CHECK ((status = ANY (ARRAY['unreviewed'::text, 'in-review'::text, 'needs-work'::text, 'approved'::text, 'stale'::text])))
);

CREATE TABLE files (
    path text NOT NULL,
    content text NOT NULL,
    content_sha256 text NOT NULL,
    size_bytes integer NOT NULL,
    folder text NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE analysis_output_cache (
    id bigint NOT NULL,
    run_id text NOT NULL,
    kind text NOT NULL,
    scope text NOT NULL,
    uc_uuid text DEFAULT ''::text NOT NULL,
    content text NOT NULL,
    model_label text DEFAULT ''::text NOT NULL,
    source_ingested_at timestamp with time zone,
    created_by text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    project_id bigint
);

CREATE SEQUENCE analysis_output_cache_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE analysis_output_cache_id_seq OWNED BY analysis_output_cache.id;

CREATE TABLE analysis_runs (
    run_id text NOT NULL,
    run_name text,
    mode text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    total_ucs integer,
    successful integer,
    failed integer,
    total_samples integer,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    project_id bigint
);

CREATE TABLE assessment_capability_scores (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assessment_id uuid NOT NULL,
    framework_capability_id uuid NOT NULL,
    state_key text NOT NULL,
    maturity smallint,
    rationale text,
    source text DEFAULT 'human'::text NOT NULL,
    updated_by text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_score_maturity CHECK (((maturity IS NULL) OR ((maturity >= 0) AND (maturity <= 5)))),
    CONSTRAINT chk_score_source CHECK ((source = ANY (ARRAY['llm'::text, 'human'::text])))
);

COMMENT ON TABLE assessment_capability_scores IS 'capability x state -> 0..5 maturity. current back-fills from assessment_findings; source=llm|human.';

CREATE TABLE assessment_findings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    assessment_id uuid NOT NULL,
    capability_handle text NOT NULL,
    state text DEFAULT 'absent'::text NOT NULL,
    maturity integer,
    evidence text,
    notes text,
    pillar text DEFAULT 'platform'::text NOT NULL,
    family text DEFAULT 'dcm'::text NOT NULL,
    domain_prefix text,
    normalized_to_term_id uuid,
    catalog_capability_id bigint,
    normalization_status text DEFAULT 'unmapped'::text NOT NULL,
    classification text DEFAULT 'client-confidential'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    category text,
    CONSTRAINT chk_finding_norm CHECK ((normalization_status = ANY (ARRAY['normalized'::text, 'proposed-taxonomy-gap'::text, 'unmapped'::text]))),
    CONSTRAINT chk_finding_state CHECK ((state = ANY (ARRAY['present'::text, 'partial'::text, 'absent'::text, 'n/a'::text])))
);

COMMENT ON TABLE assessment_findings IS 'UDLM Knowledge family · Finding. state=present|partial|absent; gaps drive the roadmap.';

CREATE TABLE assessment_framework_link (
    assessment_id uuid NOT NULL,
    framework_id uuid NOT NULL,
    linked_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE assessments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    handle text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    owned_by text,
    created_by text,
    created_via text DEFAULT 'manual'::text NOT NULL,
    lifecycle_state text DEFAULT 'OBSERVED'::text NOT NULL,
    family text DEFAULT 'dcm'::text NOT NULL,
    assessment_type text DEFAULT 'generic'::text NOT NULL,
    pillar text DEFAULT 'platform'::text NOT NULL,
    source text,
    summary text,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    classification text DEFAULT 'client-confidential'::text NOT NULL,
    scope_tier text DEFAULT 'project'::text NOT NULL,
    project_id bigint,
    scope_tags text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_assess_pillar CHECK ((pillar = ANY (ARRAY['platform'::text, 'people-process'::text, 'enablement'::text]))),
    CONSTRAINT chk_assess_tier CHECK ((scope_tier = ANY (ARRAY['global'::text, 'shared'::text, 'domain'::text, 'project'::text])))
);

COMMENT ON TABLE assessments IS 'UDLM Knowledge family · Assessment (OBSERVED). Generic mechanism; confidential data inside work env.';

CREATE TABLE audit_log (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    actor text,
    actor_source text,
    action text NOT NULL,
    method text,
    path text,
    object_type text,
    object_id text,
    project_id bigint,
    outcome text DEFAULT 'success'::text NOT NULL,
    status_code integer,
    ip text,
    user_agent text,
    summary text,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL
);

CREATE SEQUENCE audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE audit_log_id_seq OWNED BY audit_log.id;

CREATE TABLE capability_catalog (
    id bigint NOT NULL,
    project_id bigint,
    cap_key text NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    definition text DEFAULT ''::text NOT NULL,
    spec_refs text[] DEFAULT '{}'::text[] NOT NULL,
    depends_on text[] DEFAULT '{}'::text[] NOT NULL,
    status text DEFAULT 'confirmed'::text NOT NULL,
    created_by text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    domain text DEFAULT ''::text NOT NULL,
    family text DEFAULT 'dcm'::text NOT NULL,
    domain_prefix text,
    normalized_to_term_id uuid,
    normalization_status text DEFAULT 'unmapped'::text NOT NULL,
    created_via text DEFAULT 'curated'::text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    classification text DEFAULT 'public'::text NOT NULL,
    subdomain text,
    disposition text,
    strategic_fit text,
    tech_fitness text,
    bounded_context text,
    strategic_provider text
);

CREATE SEQUENCE capability_catalog_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE capability_catalog_id_seq OWNED BY capability_catalog.id;

CREATE TABLE experiments (
    id integer NOT NULL,
    proposal_id integer,
    title text,
    change_spec jsonb DEFAULT '{}'::jsonb NOT NULL,
    eval_set_id integer,
    eval_set_name text,
    sample_count integer DEFAULT 1 NOT NULL,
    baseline_run text,
    candidate_run text,
    baseline_score jsonb,
    candidate_score jsonb,
    verdict text,
    verdict_reason text,
    status text DEFAULT 'running'::text NOT NULL,
    auto_promote boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE experiments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE experiments_id_seq OWNED BY experiments.id;

CREATE TABLE goal_measures (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    goal_id uuid NOT NULL,
    statement text NOT NULL,
    metric text,
    baseline text,
    target text,
    ord integer DEFAULT 0 NOT NULL
);

CREATE TABLE goal_targets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    goal_id uuid NOT NULL,
    framework_capability_id uuid NOT NULL,
    target_maturity smallint,
    weight real DEFAULT 1.0 NOT NULL,
    rationale text,
    CONSTRAINT chk_target_maturity CHECK (((target_maturity IS NULL) OR ((target_maturity >= 0) AND (target_maturity <= 5))))
);

CREATE TABLE goals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id bigint,
    theme_id uuid,
    statement text NOT NULL,
    origin text DEFAULT 'human'::text NOT NULL,
    description text,
    owner text,
    priority integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    target_date date,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_goal_origin CHECK ((origin = ANY (ARRAY['human'::text, 'derived'::text, 'customer'::text]))),
    CONSTRAINT chk_goal_status CHECK ((status = ANY (ARRAY['open'::text, 'committed'::text, 'achieved'::text, 'dropped'::text])))
);

COMMENT ON TABLE goals IS 'Apex outcome (goal-driven backward chain). origin: human|derived|customer. See maturity-wall-design.md.';

CREATE TABLE improvement_proposals (
    id integer NOT NULL,
    batch_id text NOT NULL,
    run_id text NOT NULL,
    run_name text,
    signature_class text,
    kind text NOT NULL,
    target text,
    rationale text,
    proposed_change text,
    predicted_effect text,
    confidence text,
    source text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'proposed'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text,
    reviewed_at timestamp with time zone,
    reviewed_by text,
    review_note text,
    change_spec jsonb
);

CREATE SEQUENCE improvement_proposals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE improvement_proposals_id_seq OWNED BY improvement_proposals.id;

CREATE TABLE lifecycle_events (
    id bigint NOT NULL,
    uc_uuid text NOT NULL,
    from_state text,
    to_state text NOT NULL,
    actor text NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE lifecycle_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE lifecycle_events_id_seq OWNED BY lifecycle_events.id;

CREATE TABLE managed_repos (
    id integer NOT NULL,
    uuid uuid DEFAULT gen_random_uuid() NOT NULL,
    namespace text NOT NULL,
    display_name text,
    repo_url text NOT NULL,
    repo_branch text DEFAULT 'main'::text NOT NULL,
    root_path text DEFAULT ''::text NOT NULL,
    roles text[] DEFAULT '{}'::text[] NOT NULL,
    ingestion_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'system'::text NOT NULL,
    github_pat_encrypted text,
    github_webhook_secret_encrypted text,
    github_pat_credential_id integer,
    github_webhook_secret_credential_id integer,
    project_id bigint,
    use_category text,
    CONSTRAINT managed_repos_namespace_check CHECK ((namespace ~ '^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$'::text))
);

CREATE SEQUENCE managed_repos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE managed_repos_id_seq OWNED BY managed_repos.id;

CREATE TABLE managed_use_cases (
    uuid text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    yaml_content text NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    lifecycle_state text DEFAULT 'draft'::text NOT NULL,
    corpus_pr_url text,
    corpus_pr_state text,
    corpus_commit_sha text,
    corpus_synced_at timestamp with time zone,
    corpus_synced_by text,
    corpus_synced_path text,
    corpus_branch text,
    priority text,
    priority_score integer,
    readiness_score integer,
    project_id bigint,
    source_repo_uuid uuid,
    source_path text DEFAULT ''::text NOT NULL,
    source_ref text DEFAULT ''::text NOT NULL,
    customer_requests integer DEFAULT 0 NOT NULL
);

CREATE TABLE pr_comment_poll_state (
    repo_uuid uuid NOT NULL,
    last_poll_started_at timestamp with time zone,
    last_poll_finished_at timestamp with time zone,
    last_poll_ok boolean,
    last_poll_error text,
    comments_seen_total bigint DEFAULT 0 NOT NULL,
    newest_seen_updated_at timestamp with time zone
);

CREATE TABLE pr_comments (
    id integer NOT NULL,
    uuid uuid DEFAULT gen_random_uuid() NOT NULL,
    repo_uuid uuid NOT NULL,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    github_comment_id bigint NOT NULL,
    github_comment_type text NOT NULL,
    pr_number integer NOT NULL,
    pr_title text,
    pr_url text,
    author_login text NOT NULL,
    author_url text,
    body text NOT NULL,
    comment_url text,
    status text DEFAULT 'new'::text NOT NULL,
    status_changed_at timestamp with time zone,
    status_changed_by text,
    github_created_at timestamp with time zone NOT NULL,
    github_updated_at timestamp with time zone NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    ingestion_source text DEFAULT 'poller'::text NOT NULL
);

CREATE SEQUENCE pr_comments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE pr_comments_id_seq OWNED BY pr_comments.id;

CREATE TABLE project_stage_context (
    project_id bigint NOT NULL,
    stage text NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    updated_by text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    section_overrides jsonb DEFAULT '{}'::jsonb NOT NULL,
    applied boolean DEFAULT false NOT NULL
);

CREATE TABLE recording_jobs (
    job_id text NOT NULL,
    project_id integer,
    submitted_by text,
    status text DEFAULT 'queued'::text NOT NULL,
    phase text,
    progress real DEFAULT 0,
    file_name text,
    content_type text,
    file_bytes bytea,
    file_size bigint,
    context text,
    model_config_id integer,
    transcript text,
    items jsonb,
    error text,
    duration_seconds integer,
    worker text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    expires_at timestamp with time zone
);

CREATE SEQUENCE review_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE review_events_id_seq OWNED BY review_events.id;

CREATE TABLE run_diagnoses (
    batch_id text NOT NULL,
    run_id text NOT NULL,
    run_name text,
    taxonomy jsonb DEFAULT '{}'::jsonb NOT NULL,
    used_llm boolean DEFAULT false NOT NULL,
    rule_count integer DEFAULT 0 NOT NULL,
    llm_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text
);

CREATE TABLE run_sessions (
    run_name text NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    category text DEFAULT 'ad-hoc'::text NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    mode text,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    phase text,
    wall_time_seconds double precision,
    gpu_energy_joules double precision,
    gpu_avg_power_watts double precision,
    gpu_peak_power_watts double precision,
    gpu_avg_gfx_activity double precision,
    total_prompt_tokens bigint,
    total_gen_tokens bigint,
    uc_total integer,
    uc_succeeded integer,
    uc_failed integer,
    finalized_at timestamp with time zone,
    baseline_gen_tokens double precision,
    baseline_prompt_tokens double precision,
    set_id integer,
    set_name text,
    selection_mode text,
    uc_state_snapshot jsonb,
    spec_namespaces text[],
    corpus_namespaces text[],
    archived boolean DEFAULT false NOT NULL,
    project_id bigint,
    trigger_payload jsonb,
    corpus_repo_branch text,
    spec_repo_branch text,
    corpus_repo_sha text,
    spec_repo_sha text
);

CREATE TABLE themes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id bigint,
    name text NOT NULL,
    color text,
    ord integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE uc_analyses (
    id bigint NOT NULL,
    run_id text NOT NULL,
    uc_uuid text NOT NULL,
    uc_handle text,
    status text,
    verdict text,
    overall_assessment text,
    wall_time_seconds double precision,
    sample_count integer,
    engine_version text,
    model text,
    endpoint_url text,
    analyzed_at timestamp with time zone,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    lifecycle_state_at_run text,
    source_kind text,
    infra_confidence_label text,
    infra_confidence_score integer,
    infra_confidence_signals jsonb,
    infra_confidence_explanation text,
    infra_confidence_recommendations jsonb,
    engine_commit text,
    consumer_version text,
    uc_content_sha text,
    source_repo_shas jsonb,
    eval_fingerprint text,
    error_reason text,
    error_phase text
);

CREATE SEQUENCE uc_analyses_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE uc_analyses_id_seq OWNED BY uc_analyses.id;

CREATE TABLE uc_capabilities (
    id bigint NOT NULL,
    analysis_id bigint NOT NULL,
    run_id text NOT NULL,
    uc_uuid text NOT NULL,
    capability_id text NOT NULL,
    usage text,
    confidence text,
    confidence_score integer,
    rationale text,
    namespace text,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE uc_capabilities_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE uc_capabilities_id_seq OWNED BY uc_capabilities.id;

CREATE TABLE uc_capability_deps (
    id bigint NOT NULL,
    analysis_id bigint NOT NULL,
    run_id text NOT NULL,
    uc_uuid text NOT NULL,
    capability_id text NOT NULL,
    depends_on_id text NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE uc_capability_deps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE uc_capability_deps_id_seq OWNED BY uc_capability_deps.id;

CREATE TABLE uc_customer_requests (
    id bigint NOT NULL,
    uc_uuid text NOT NULL,
    project_id bigint,
    customer text NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    created_by text DEFAULT ''::text NOT NULL,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    customer_id bigint
);

CREATE SEQUENCE uc_customer_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE uc_customer_requests_id_seq OWNED BY uc_customer_requests.id;

CREATE TABLE uc_gaps (
    id bigint NOT NULL,
    analysis_id bigint NOT NULL,
    run_id text NOT NULL,
    uc_uuid text NOT NULL,
    gap_id text,
    title text,
    description text,
    severity text,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    recommendation text,
    rationale text,
    namespace text,
    -- Wave-1 gap identity (ADR-009): the catalog capability this gap concerns.
    -- catalog_capability_id links to capability_catalog(id) when the emitted
    -- capability_id matched; normalization_status mirrors assessment_findings.
    catalog_capability_id bigint,
    normalization_status text DEFAULT 'unmapped'::text NOT NULL,
    CONSTRAINT chk_uc_gaps_norm CHECK (normalization_status IN ('normalized','proposed-taxonomy-gap','unmapped'))
);

CREATE SEQUENCE uc_gaps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE uc_gaps_id_seq OWNED BY uc_gaps.id;

CREATE TABLE uc_pr_comment_links (
    id integer NOT NULL,
    uc_uuid uuid NOT NULL,
    pr_comment_uuid uuid NOT NULL,
    linked_at timestamp with time zone DEFAULT now() NOT NULL,
    linked_by text DEFAULT 'system'::text NOT NULL,
    notes text
);

CREATE SEQUENCE uc_pr_comment_links_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE uc_pr_comment_links_id_seq OWNED BY uc_pr_comment_links.id;

CREATE TABLE use_case_set_members (
    set_id bigint NOT NULL,
    uc_uuid text NOT NULL,
    uc_source text DEFAULT 'managed'::text NOT NULL,
    uc_handle text,
    uc_path text,
    added_by text NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE use_case_sets (
    id bigint NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    project_id bigint
);

CREATE SEQUENCE use_case_sets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE use_case_sets_id_seq OWNED BY use_case_sets.id;

ALTER TABLE ONLY analysis_output_cache ALTER COLUMN id SET DEFAULT nextval('analysis_output_cache_id_seq'::regclass);

ALTER TABLE ONLY audit_log ALTER COLUMN id SET DEFAULT nextval('audit_log_id_seq'::regclass);

ALTER TABLE ONLY capability_catalog ALTER COLUMN id SET DEFAULT nextval('capability_catalog_id_seq'::regclass);

ALTER TABLE ONLY experiments ALTER COLUMN id SET DEFAULT nextval('experiments_id_seq'::regclass);

ALTER TABLE ONLY improvement_proposals ALTER COLUMN id SET DEFAULT nextval('improvement_proposals_id_seq'::regclass);

ALTER TABLE ONLY lifecycle_events ALTER COLUMN id SET DEFAULT nextval('lifecycle_events_id_seq'::regclass);

ALTER TABLE ONLY managed_repos ALTER COLUMN id SET DEFAULT nextval('managed_repos_id_seq'::regclass);

ALTER TABLE ONLY pr_comments ALTER COLUMN id SET DEFAULT nextval('pr_comments_id_seq'::regclass);

ALTER TABLE ONLY review_events ALTER COLUMN id SET DEFAULT nextval('review_events_id_seq'::regclass);

ALTER TABLE ONLY uc_analyses ALTER COLUMN id SET DEFAULT nextval('uc_analyses_id_seq'::regclass);

ALTER TABLE ONLY uc_capabilities ALTER COLUMN id SET DEFAULT nextval('uc_capabilities_id_seq'::regclass);

ALTER TABLE ONLY uc_capability_deps ALTER COLUMN id SET DEFAULT nextval('uc_capability_deps_id_seq'::regclass);

ALTER TABLE ONLY uc_customer_requests ALTER COLUMN id SET DEFAULT nextval('uc_customer_requests_id_seq'::regclass);

ALTER TABLE ONLY uc_gaps ALTER COLUMN id SET DEFAULT nextval('uc_gaps_id_seq'::regclass);

ALTER TABLE ONLY uc_pr_comment_links ALTER COLUMN id SET DEFAULT nextval('uc_pr_comment_links_id_seq'::regclass);

ALTER TABLE ONLY use_case_sets ALTER COLUMN id SET DEFAULT nextval('use_case_sets_id_seq'::regclass);

ALTER TABLE ONLY analysis_output_cache
    ADD CONSTRAINT analysis_output_cache_pkey PRIMARY KEY (id);

ALTER TABLE ONLY analysis_output_cache
    ADD CONSTRAINT analysis_output_cache_run_id_kind_scope_uc_uuid_key UNIQUE (run_id, kind, scope, uc_uuid);

ALTER TABLE ONLY analysis_runs
    ADD CONSTRAINT analysis_runs_pkey PRIMARY KEY (run_id);

ALTER TABLE ONLY assessment_capability_scores
    ADD CONSTRAINT assessment_capability_scores_pkey PRIMARY KEY (id);

ALTER TABLE ONLY assessment_findings
    ADD CONSTRAINT assessment_findings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY assessment_framework_link
    ADD CONSTRAINT assessment_framework_link_pkey PRIMARY KEY (assessment_id);

ALTER TABLE ONLY assessments
    ADD CONSTRAINT assessments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY capability_catalog
    ADD CONSTRAINT capability_catalog_pkey PRIMARY KEY (id);

ALTER TABLE ONLY capability_catalog
    ADD CONSTRAINT capability_catalog_project_id_cap_key_key UNIQUE (project_id, cap_key);

ALTER TABLE ONLY experiments
    ADD CONSTRAINT experiments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY files
    ADD CONSTRAINT files_pkey PRIMARY KEY (path);

ALTER TABLE ONLY goal_measures
    ADD CONSTRAINT goal_measures_pkey PRIMARY KEY (id);

ALTER TABLE ONLY goal_targets
    ADD CONSTRAINT goal_targets_pkey PRIMARY KEY (id);

ALTER TABLE ONLY goals
    ADD CONSTRAINT goals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY improvement_proposals
    ADD CONSTRAINT improvement_proposals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY lifecycle_events
    ADD CONSTRAINT lifecycle_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY managed_repos
    ADD CONSTRAINT managed_repos_pkey PRIMARY KEY (id);

ALTER TABLE ONLY managed_repos
    ADD CONSTRAINT managed_repos_uuid_key UNIQUE (uuid);

ALTER TABLE ONLY managed_use_cases
    ADD CONSTRAINT managed_use_cases_pkey PRIMARY KEY (uuid);

ALTER TABLE ONLY pr_comment_poll_state
    ADD CONSTRAINT pr_comment_poll_state_pkey PRIMARY KEY (repo_uuid);

ALTER TABLE ONLY pr_comments
    ADD CONSTRAINT pr_comments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY pr_comments
    ADD CONSTRAINT pr_comments_repo_uuid_github_comment_id_github_comment_type_key UNIQUE (repo_uuid, github_comment_id, github_comment_type);

ALTER TABLE ONLY pr_comments
    ADD CONSTRAINT pr_comments_uuid_key UNIQUE (uuid);

ALTER TABLE ONLY project_stage_context
    ADD CONSTRAINT project_stage_context_pkey PRIMARY KEY (project_id, stage);

ALTER TABLE ONLY recording_jobs
    ADD CONSTRAINT recording_jobs_pkey PRIMARY KEY (job_id);

ALTER TABLE ONLY review_events
    ADD CONSTRAINT review_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY run_diagnoses
    ADD CONSTRAINT run_diagnoses_pkey PRIMARY KEY (batch_id);

ALTER TABLE ONLY run_sessions
    ADD CONSTRAINT run_sessions_pkey PRIMARY KEY (run_name);

ALTER TABLE ONLY themes
    ADD CONSTRAINT themes_pkey PRIMARY KEY (id);

ALTER TABLE ONLY uc_analyses
    ADD CONSTRAINT uc_analyses_pkey PRIMARY KEY (id);

ALTER TABLE ONLY uc_analyses
    ADD CONSTRAINT uc_analyses_run_id_uc_uuid_key UNIQUE (run_id, uc_uuid);

ALTER TABLE ONLY uc_capabilities
    ADD CONSTRAINT uc_capabilities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY uc_capability_deps
    ADD CONSTRAINT uc_capability_deps_pkey PRIMARY KEY (id);

ALTER TABLE ONLY uc_customer_requests
    ADD CONSTRAINT uc_customer_requests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY uc_gaps
    ADD CONSTRAINT uc_gaps_pkey PRIMARY KEY (id);

ALTER TABLE ONLY uc_pr_comment_links
    ADD CONSTRAINT uc_pr_comment_links_pkey PRIMARY KEY (id);

ALTER TABLE ONLY uc_pr_comment_links
    ADD CONSTRAINT uc_pr_comment_links_uc_uuid_pr_comment_uuid_key UNIQUE (uc_uuid, pr_comment_uuid);

ALTER TABLE ONLY goal_targets
    ADD CONSTRAINT uq_goal_target UNIQUE (goal_id, framework_capability_id);

ALTER TABLE ONLY assessment_capability_scores
    ADD CONSTRAINT uq_score UNIQUE (assessment_id, framework_capability_id, state_key);

ALTER TABLE ONLY use_case_set_members
    ADD CONSTRAINT use_case_set_members_pkey PRIMARY KEY (set_id, uc_uuid);

ALTER TABLE ONLY use_case_sets
    ADD CONSTRAINT use_case_sets_pkey PRIMARY KEY (id);

CREATE INDEX idx_analysis_output_cache_run ON analysis_output_cache USING btree (run_id);

CREATE INDEX idx_analysis_runs_started ON analysis_runs USING btree (started_at DESC);

CREATE INDEX idx_aruns_project ON analysis_runs USING btree (project_id);

CREATE INDEX idx_assess_project ON assessments USING btree (project_id);

CREATE INDEX idx_assess_type ON assessments USING btree (assessment_type);

CREATE INDEX idx_audit_action ON audit_log USING btree (action);

CREATE INDEX idx_audit_actor ON audit_log USING btree (lower(actor));

CREATE INDEX idx_audit_outcome ON audit_log USING btree (outcome);

CREATE INDEX idx_audit_project ON audit_log USING btree (project_id);

CREATE INDEX idx_audit_ts ON audit_log USING btree (ts DESC);

CREATE INDEX idx_capability_catalog_project ON capability_catalog USING btree (project_id);

CREATE INDEX idx_capcat_family ON capability_catalog USING btree (family);

CREATE INDEX idx_capcat_status ON capability_catalog USING btree (status);

CREATE INDEX idx_capcat_term ON capability_catalog USING btree (normalized_to_term_id);

CREATE INDEX idx_experiments_created ON experiments USING btree (created_at DESC);

CREATE INDEX idx_experiments_proposal ON experiments USING btree (proposal_id);

CREATE INDEX idx_experiments_status ON experiments USING btree (status);

CREATE INDEX idx_files_folder ON files USING btree (folder);

CREATE INDEX idx_finding_assessment ON assessment_findings USING btree (assessment_id);

CREATE INDEX idx_finding_capability ON assessment_findings USING btree (lower(capability_handle));

CREATE INDEX idx_finding_category ON assessment_findings USING btree (category);

CREATE INDEX idx_finding_state ON assessment_findings USING btree (state);

CREATE INDEX idx_finding_term ON assessment_findings USING btree (normalized_to_term_id);

CREATE INDEX idx_goal_measure_goal ON goal_measures USING btree (goal_id);

CREATE INDEX idx_goal_target_goal ON goal_targets USING btree (goal_id);

CREATE INDEX idx_goals_project ON goals USING btree (project_id);

CREATE INDEX idx_goals_theme ON goals USING btree (theme_id);

CREATE INDEX idx_improvement_proposals_batch ON improvement_proposals USING btree (batch_id);

CREATE INDEX idx_improvement_proposals_run ON improvement_proposals USING btree (run_id);

CREATE INDEX idx_improvement_proposals_status ON improvement_proposals USING btree (status);

CREATE INDEX idx_lifecycle_events_uc ON lifecycle_events USING btree (uc_uuid, created_at DESC);

CREATE INDEX idx_managed_repos_pat_credential ON managed_repos USING btree (github_pat_credential_id);

CREATE INDEX idx_managed_repos_project ON managed_repos USING btree (project_id);

CREATE INDEX idx_managed_repos_roles ON managed_repos USING gin (roles);

CREATE INDEX idx_managed_repos_tenant ON managed_repos USING btree (tenant_id);

CREATE INDEX idx_managed_repos_updated_at ON managed_repos USING btree (updated_at DESC);

CREATE INDEX idx_managed_repos_webhook_credential ON managed_repos USING btree (github_webhook_secret_credential_id);

CREATE INDEX idx_managed_uc_demand ON managed_use_cases USING btree (customer_requests DESC);

CREATE INDEX idx_managed_uc_priority ON managed_use_cases USING btree (priority_score DESC NULLS LAST);

CREATE INDEX idx_managed_uc_state ON managed_use_cases USING btree (lifecycle_state);

CREATE INDEX idx_managed_uc_updated ON managed_use_cases USING btree (updated_at DESC);

CREATE INDEX idx_managed_ucs_pr_state ON managed_use_cases USING btree (corpus_pr_state);

CREATE INDEX idx_muc_project ON managed_use_cases USING btree (project_id);

CREATE INDEX idx_muc_source_repo ON managed_use_cases USING btree (source_repo_uuid);

CREATE INDEX idx_pr_comments_github_upd ON pr_comments USING btree (github_updated_at DESC);

CREATE INDEX idx_pr_comments_pr ON pr_comments USING btree (repo_uuid, pr_number);

CREATE INDEX idx_pr_comments_repo ON pr_comments USING btree (repo_uuid);

CREATE INDEX idx_pr_comments_status ON pr_comments USING btree (status, fetched_at DESC);

CREATE INDEX idx_pr_comments_tenant ON pr_comments USING btree (tenant_id);

CREATE INDEX idx_review_events_created ON review_events USING btree (created_at DESC);

CREATE INDEX idx_review_events_file_created ON review_events USING btree (file_path, created_at DESC);

CREATE INDEX idx_review_events_reviewer_created ON review_events USING btree (reviewer, created_at DESC);

CREATE INDEX idx_rsess_project ON run_sessions USING btree (project_id);

CREATE INDEX idx_run_diagnoses_run ON run_diagnoses USING btree (run_id, created_at DESC);

CREATE INDEX idx_run_sessions_category ON run_sessions USING btree (category);

CREATE INDEX idx_run_sessions_created ON run_sessions USING btree (created_at DESC);

CREATE INDEX idx_run_sessions_phase ON run_sessions USING btree (phase);

CREATE INDEX idx_run_sessions_set ON run_sessions USING btree (set_id);

CREATE INDEX idx_score_assessment ON assessment_capability_scores USING btree (assessment_id);

CREATE INDEX idx_score_capability ON assessment_capability_scores USING btree (framework_capability_id);

CREATE INDEX idx_set_members_uc ON use_case_set_members USING btree (uc_uuid);

CREATE INDEX idx_themes_project ON themes USING btree (project_id);

CREATE INDEX idx_uc_analyses_fingerprint ON uc_analyses USING btree (eval_fingerprint);

CREATE INDEX idx_uc_analyses_infra_label ON uc_analyses USING btree (infra_confidence_label);

CREATE INDEX idx_uc_analyses_run ON uc_analyses USING btree (run_id);

CREATE INDEX idx_uc_analyses_state_at ON uc_analyses USING btree (lifecycle_state_at_run);

CREATE INDEX idx_uc_analyses_uuid ON uc_analyses USING btree (uc_uuid, ingested_at DESC);

CREATE INDEX idx_uc_analyses_verdict ON uc_analyses USING btree (verdict);

CREATE INDEX idx_uc_capdeps_cap ON uc_capability_deps USING btree (capability_id);

CREATE INDEX idx_uc_capdeps_run ON uc_capability_deps USING btree (run_id);

CREATE INDEX idx_uc_caps_cap ON uc_capabilities USING btree (capability_id);

CREATE INDEX idx_uc_caps_run ON uc_capabilities USING btree (run_id);

CREATE INDEX idx_uc_caps_uuid ON uc_capabilities USING btree (uc_uuid);

CREATE INDEX idx_uc_cust_req_customer ON uc_customer_requests USING btree (customer);

CREATE INDEX idx_uc_cust_req_customer_id ON uc_customer_requests USING btree (customer_id);

CREATE INDEX idx_uc_cust_req_uc ON uc_customer_requests USING btree (uc_uuid);

CREATE INDEX idx_uc_gaps_gap_id ON uc_gaps USING btree (gap_id);

CREATE INDEX idx_uc_gaps_namespace ON uc_gaps USING btree (namespace);

CREATE INDEX idx_uc_gaps_run ON uc_gaps USING btree (run_id);

CREATE INDEX idx_uc_gaps_uuid ON uc_gaps USING btree (uc_uuid);

CREATE INDEX idx_uc_pr_links_comment ON uc_pr_comment_links USING btree (pr_comment_uuid);

CREATE INDEX idx_uc_pr_links_uc ON uc_pr_comment_links USING btree (uc_uuid);

CREATE UNIQUE INDEX idx_uc_sets_name ON use_case_sets USING btree (lower(name));

CREATE UNIQUE INDEX idx_uc_sets_one_default ON use_case_sets USING btree (is_default) WHERE is_default;

CREATE INDEX idx_ucsets_project ON use_case_sets USING btree (project_id);

CREATE UNIQUE INDEX managed_repos_project_namespace_key ON managed_repos USING btree (COALESCE(project_id, (0)::bigint), namespace);

CREATE INDEX recording_jobs_project_idx ON recording_jobs USING btree (project_id);

CREATE INDEX recording_jobs_queued_idx ON recording_jobs USING btree (created_at) WHERE (status = 'queued'::text);

CREATE TRIGGER trg_managed_repos_updated_at BEFORE UPDATE ON managed_repos FOR EACH ROW EXECUTE FUNCTION public._touch_managed_repos_updated_at();

ALTER TABLE ONLY analysis_output_cache
    ADD CONSTRAINT analysis_output_cache_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY analysis_runs
    ADD CONSTRAINT analysis_runs_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY assessment_capability_scores
    ADD CONSTRAINT assessment_capability_scores_assessment_id_fkey FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE;

ALTER TABLE ONLY assessment_capability_scores
    ADD CONSTRAINT assessment_capability_scores_framework_capability_id_fkey FOREIGN KEY (framework_capability_id) REFERENCES public.framework_capabilities(id) ON DELETE CASCADE;

ALTER TABLE ONLY assessment_findings
    ADD CONSTRAINT assessment_findings_assessment_id_fkey FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE;

ALTER TABLE ONLY assessment_findings
    ADD CONSTRAINT assessment_findings_normalized_to_term_id_fkey FOREIGN KEY (normalized_to_term_id) REFERENCES public.capability_taxonomy_terms(id) ON DELETE SET NULL;

ALTER TABLE ONLY assessment_framework_link
    ADD CONSTRAINT assessment_framework_link_assessment_id_fkey FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE;

ALTER TABLE ONLY assessment_framework_link
    ADD CONSTRAINT assessment_framework_link_framework_id_fkey FOREIGN KEY (framework_id) REFERENCES public.assessment_frameworks(id) ON DELETE CASCADE;

ALTER TABLE ONLY assessments
    ADD CONSTRAINT assessments_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY capability_catalog
    ADD CONSTRAINT capability_catalog_normalized_to_term_id_fkey FOREIGN KEY (normalized_to_term_id) REFERENCES public.capability_taxonomy_terms(id) ON DELETE SET NULL;

ALTER TABLE ONLY capability_catalog
    ADD CONSTRAINT capability_catalog_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY experiments
    ADD CONSTRAINT experiments_proposal_id_fkey FOREIGN KEY (proposal_id) REFERENCES improvement_proposals(id) ON DELETE SET NULL;

ALTER TABLE ONLY goal_measures
    ADD CONSTRAINT goal_measures_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE;

ALTER TABLE ONLY goal_targets
    ADD CONSTRAINT goal_targets_framework_capability_id_fkey FOREIGN KEY (framework_capability_id) REFERENCES public.framework_capabilities(id) ON DELETE CASCADE;

ALTER TABLE ONLY goal_targets
    ADD CONSTRAINT goal_targets_goal_id_fkey FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE;

ALTER TABLE ONLY goals
    ADD CONSTRAINT goals_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY goals
    ADD CONSTRAINT goals_theme_id_fkey FOREIGN KEY (theme_id) REFERENCES themes(id) ON DELETE SET NULL;

ALTER TABLE ONLY improvement_proposals
    ADD CONSTRAINT improvement_proposals_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES run_diagnoses(batch_id) ON DELETE CASCADE;

ALTER TABLE ONLY lifecycle_events
    ADD CONSTRAINT lifecycle_events_uc_uuid_fkey FOREIGN KEY (uc_uuid) REFERENCES managed_use_cases(uuid) ON DELETE CASCADE;

ALTER TABLE ONLY managed_repos
    ADD CONSTRAINT managed_repos_github_pat_credential_id_fkey FOREIGN KEY (github_pat_credential_id) REFERENCES public.credentials(id) ON DELETE SET NULL;

ALTER TABLE ONLY managed_repos
    ADD CONSTRAINT managed_repos_github_webhook_secret_credential_id_fkey FOREIGN KEY (github_webhook_secret_credential_id) REFERENCES public.credentials(id) ON DELETE SET NULL;

ALTER TABLE ONLY managed_repos
    ADD CONSTRAINT managed_repos_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY managed_use_cases
    ADD CONSTRAINT managed_use_cases_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY pr_comment_poll_state
    ADD CONSTRAINT pr_comment_poll_state_repo_uuid_fkey FOREIGN KEY (repo_uuid) REFERENCES managed_repos(uuid) ON DELETE CASCADE;

ALTER TABLE ONLY pr_comments
    ADD CONSTRAINT pr_comments_repo_uuid_fkey FOREIGN KEY (repo_uuid) REFERENCES managed_repos(uuid) ON DELETE CASCADE;

ALTER TABLE ONLY project_stage_context
    ADD CONSTRAINT project_stage_context_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY review_events
    ADD CONSTRAINT review_events_file_path_fkey FOREIGN KEY (file_path) REFERENCES files(path) ON DELETE CASCADE;

ALTER TABLE ONLY run_sessions
    ADD CONSTRAINT run_sessions_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY run_sessions
    ADD CONSTRAINT run_sessions_set_id_fkey FOREIGN KEY (set_id) REFERENCES use_case_sets(id) ON DELETE SET NULL;

ALTER TABLE ONLY themes
    ADD CONSTRAINT themes_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY uc_analyses
    ADD CONSTRAINT uc_analyses_run_id_fkey FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE;

ALTER TABLE ONLY uc_capabilities
    ADD CONSTRAINT uc_capabilities_analysis_id_fkey FOREIGN KEY (analysis_id) REFERENCES uc_analyses(id) ON DELETE CASCADE;

ALTER TABLE ONLY uc_capability_deps
    ADD CONSTRAINT uc_capability_deps_analysis_id_fkey FOREIGN KEY (analysis_id) REFERENCES uc_analyses(id) ON DELETE CASCADE;

ALTER TABLE ONLY uc_customer_requests
    ADD CONSTRAINT uc_customer_requests_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id);

ALTER TABLE ONLY uc_customer_requests
    ADD CONSTRAINT uc_customer_requests_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY uc_customer_requests
    ADD CONSTRAINT uc_customer_requests_uc_uuid_fkey FOREIGN KEY (uc_uuid) REFERENCES managed_use_cases(uuid) ON DELETE CASCADE;

ALTER TABLE ONLY uc_gaps
    ADD CONSTRAINT uc_gaps_analysis_id_fkey FOREIGN KEY (analysis_id) REFERENCES uc_analyses(id) ON DELETE CASCADE;

ALTER TABLE ONLY uc_pr_comment_links
    ADD CONSTRAINT uc_pr_comment_links_pr_comment_uuid_fkey FOREIGN KEY (pr_comment_uuid) REFERENCES pr_comments(uuid) ON DELETE CASCADE;

ALTER TABLE ONLY use_case_set_members
    ADD CONSTRAINT use_case_set_members_set_id_fkey FOREIGN KEY (set_id) REFERENCES use_case_sets(id) ON DELETE CASCADE;

ALTER TABLE ONLY use_case_sets
    ADD CONSTRAINT use_case_sets_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

CREATE VIEW review_current AS
 WITH latest AS (
         SELECT DISTINCT ON (review_events.file_path, review_events.reviewer) review_events.file_path,
            review_events.reviewer,
            review_events.action,
            review_events.status,
            review_events.notes,
            review_events.file_sha256_at_review,
            review_events.created_at AS reviewed_at
           FROM review_events
          ORDER BY review_events.file_path, review_events.reviewer, review_events.created_at DESC
        )
 SELECT file_path,
    reviewer,
    status,
    notes,
    file_sha256_at_review,
    reviewed_at
   FROM latest
  WHERE (action <> 'clear'::text);

CREATE VIEW review_drift AS
 SELECT rc.file_path,
    rc.reviewer,
    rc.status,
    rc.reviewed_at,
    rc.file_sha256_at_review,
    f.content_sha256 AS current_sha256,
    (rc.file_sha256_at_review IS DISTINCT FROM f.content_sha256) AS is_drifted
   FROM (review_current rc
     JOIN files f ON ((f.path = rc.file_path)));

CREATE VIEW file_current_status AS
 SELECT DISTINCT ON (file_path) file_path,
    status,
    reviewer,
    reviewed_at,
    file_sha256_at_review
   FROM review_current
  ORDER BY file_path, reviewed_at DESC;

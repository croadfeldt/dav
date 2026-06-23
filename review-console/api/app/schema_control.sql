-- DAV control-plane base schema (public). GENERATED from the live DB by the
-- tenancy schema split; do not hand-edit — regenerate via scripts/gen_base_schema.sh.
-- Run-once per install under search_path=public (tracked in public.schema_migrations).

COMMENT ON SCHEMA public IS 'standard public schema';

CREATE FUNCTION public._touch_credentials_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE FUNCTION public._touch_managed_repos_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE public.account_identities (
    alias text NOT NULL,
    reviewer text NOT NULL,
    source text DEFAULT 'manual'::text NOT NULL,
    created_by text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.api_tokens (
    id bigint NOT NULL,
    email text NOT NULL,
    token_hash text NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    created_by text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    expires_at timestamp with time zone,
    revoked_at timestamp with time zone
);

CREATE SEQUENCE public.api_tokens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.api_tokens_id_seq OWNED BY public.api_tokens.id;

CREATE TABLE public.app_settings (
    key text NOT NULL,
    value jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_by text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.assessment_frameworks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id bigint,
    key text NOT NULL,
    name text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    is_seed boolean DEFAULT false NOT NULL,
    scale jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.assessment_frameworks IS 'Configurable maturity framework (categories->capabilities + 0-5 scale + states). project_id NULL = seed template.';

CREATE TABLE public.bundle_attachments (
    id bigint NOT NULL,
    bundle_id bigint NOT NULL,
    bundle_version_id bigint NOT NULL,
    project_id bigint,
    use_category text,
    attached_by text,
    attached_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.bundle_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.bundle_attachments_id_seq OWNED BY public.bundle_attachments.id;

CREATE TABLE public.bundle_items (
    id bigint NOT NULL,
    bundle_version_id bigint NOT NULL,
    item_type text NOT NULL,
    item_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_id bigint,
    "position" integer DEFAULT 0 NOT NULL
);

CREATE SEQUENCE public.bundle_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.bundle_items_id_seq OWNED BY public.bundle_items.id;

CREATE TABLE public.bundle_versions (
    id bigint NOT NULL,
    bundle_id bigint NOT NULL,
    version_no integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone
);

CREATE SEQUENCE public.bundle_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.bundle_versions_id_seq OWNED BY public.bundle_versions.id;

CREATE TABLE public.bundles (
    id bigint NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    kind text DEFAULT 'mixed'::text NOT NULL,
    current_version_id bigint,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.bundles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.bundles_id_seq OWNED BY public.bundles.id;

CREATE TABLE public.capability_aliases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    handle text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    owned_by text,
    created_by text,
    created_via text DEFAULT 'manual'::text NOT NULL,
    lifecycle_state text DEFAULT 'CANONICAL'::text NOT NULL,
    family text DEFAULT 'dcm'::text NOT NULL,
    use_instead text DEFAULT ''::text NOT NULL,
    resolves_to_term_id uuid,
    reason text DEFAULT ''::text NOT NULL,
    pillar text DEFAULT 'platform'::text NOT NULL,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    classification text DEFAULT 'public'::text NOT NULL,
    scope_tier text DEFAULT 'project'::text NOT NULL,
    project_id bigint,
    scope_tags text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_alias_state CHECK ((lifecycle_state = ANY (ARRAY['OBSERVED'::text, 'PROPOSED'::text, 'UNDER_REVIEW'::text, 'CANONICAL'::text, 'DEPRECATED'::text]))),
    CONSTRAINT chk_alias_tier CHECK ((scope_tier = ANY (ARRAY['global'::text, 'shared'::text, 'domain'::text, 'project'::text])))
);

COMMENT ON TABLE public.capability_aliases IS 'UDLM Knowledge family · Alias. avoid(handle) → use_instead, resolves_to a TaxonomyTerm.';

CREATE TABLE public.capability_antipatterns (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    handle text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    owned_by text,
    created_by text,
    created_via text DEFAULT 'manual'::text NOT NULL,
    lifecycle_state text DEFAULT 'PROPOSED'::text NOT NULL,
    family text DEFAULT 'dcm'::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    why text DEFAULT ''::text NOT NULL,
    instead text DEFAULT ''::text NOT NULL,
    related_term_id uuid,
    pillar text DEFAULT 'platform'::text NOT NULL,
    domain_prefix text,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    classification text DEFAULT 'public'::text NOT NULL,
    scope_tier text DEFAULT 'project'::text NOT NULL,
    project_id bigint,
    scope_tags text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_antipattern_state CHECK ((lifecycle_state = ANY (ARRAY['OBSERVED'::text, 'PROPOSED'::text, 'UNDER_REVIEW'::text, 'CANONICAL'::text, 'DEPRECATED'::text]))),
    CONSTRAINT chk_antipattern_tier CHECK ((scope_tier = ANY (ARRAY['global'::text, 'shared'::text, 'domain'::text, 'project'::text])))
);

COMMENT ON TABLE public.capability_antipatterns IS 'UDLM Knowledge family · Antipattern. Patterns to avoid relative to the taxonomy/architecture.';

CREATE TABLE public.capability_taxonomy_terms (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    handle text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    owned_by text,
    created_by text,
    created_via text DEFAULT 'manual'::text NOT NULL,
    lifecycle_state text DEFAULT 'PROPOSED'::text NOT NULL,
    family text DEFAULT 'dcm'::text NOT NULL,
    definition text DEFAULT ''::text NOT NULL,
    pillar text DEFAULT 'platform'::text NOT NULL,
    domain_prefix text,
    domain text,
    category text,
    parent_id uuid,
    normalization_rules text DEFAULT ''::text NOT NULL,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    classification text DEFAULT 'public'::text NOT NULL,
    field_classification jsonb DEFAULT '{}'::jsonb NOT NULL,
    scope_tier text DEFAULT 'project'::text NOT NULL,
    project_id bigint,
    scope_tags text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_taxterm_class CHECK ((classification = ANY (ARRAY['public'::text, 'internal'::text, 'confidential'::text, 'client-confidential'::text, 'classified'::text]))),
    CONSTRAINT chk_taxterm_pillar CHECK ((pillar = ANY (ARRAY['platform'::text, 'people-process'::text, 'enablement'::text]))),
    CONSTRAINT chk_taxterm_state CHECK ((lifecycle_state = ANY (ARRAY['OBSERVED'::text, 'PROPOSED'::text, 'UNDER_REVIEW'::text, 'CANONICAL'::text, 'DEPRECATED'::text]))),
    CONSTRAINT chk_taxterm_tier CHECK ((scope_tier = ANY (ARRAY['global'::text, 'shared'::text, 'domain'::text, 'project'::text])))
);

COMMENT ON TABLE public.capability_taxonomy_terms IS 'UDLM Knowledge family · TaxonomyTerm. Per-row family = vocabulary disambiguation namespace.';

CREATE TABLE public.code_repo_configs (
    id bigint NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    repo_url text NOT NULL,
    default_branch text DEFAULT 'main'::text NOT NULL,
    token text DEFAULT ''::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT code_repo_configs_provider_check CHECK ((provider = ANY (ARRAY['github'::text, 'gitlab'::text])))
);

CREATE SEQUENCE public.code_repo_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.code_repo_configs_id_seq OWNED BY public.code_repo_configs.id;

CREATE TABLE public.credentials (
    id integer NOT NULL,
    uuid uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    credential_type text NOT NULL,
    value_encrypted text NOT NULL,
    description text,
    tenant_id text DEFAULT 'default'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text DEFAULT 'system'::text NOT NULL,
    CONSTRAINT credentials_name_check CHECK ((name ~ '^[a-z0-9][a-z0-9-]{0,62}$'::text))
);

CREATE SEQUENCE public.credentials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.credentials_id_seq OWNED BY public.credentials.id;

CREATE TABLE public.customer_projects (
    customer_id bigint NOT NULL,
    project_id bigint NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.customers (
    id bigint NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    is_exclusive boolean DEFAULT false NOT NULL,
    is_universal boolean DEFAULT false NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.customers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.customers_id_seq OWNED BY public.customers.id;

CREATE TABLE public.framework_capabilities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    category_id uuid NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    ord integer DEFAULT 0 NOT NULL,
    catalog_capability_id bigint
);

CREATE TABLE public.framework_categories (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    framework_id uuid NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    band text,
    ord integer DEFAULT 0 NOT NULL,
    inflection_side text DEFAULT 'pre'::text NOT NULL,
    CONSTRAINT chk_cat_inflection CHECK ((inflection_side = ANY (ARRAY['pre'::text, 'post'::text])))
);

CREATE TABLE public.framework_states (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    framework_id uuid NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    ord integer DEFAULT 0 NOT NULL,
    kind text DEFAULT 'target'::text NOT NULL,
    CONSTRAINT chk_state_kind CHECK ((kind = ANY (ARRAY['current'::text, 'target'::text, 'desired'::text])))
);

CREATE TABLE public.mcp_server_configs (
    id bigint NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    sse_url text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    use_uc_assist boolean DEFAULT false NOT NULL,
    project_id bigint,
    auth_token_encrypted text DEFAULT ''::text NOT NULL,
    use_category text,
    bundle_attachment_id bigint
);

CREATE SEQUENCE public.mcp_server_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.mcp_server_configs_id_seq OWNED BY public.mcp_server_configs.id;

CREATE TABLE public.model_configs (
    id bigint NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    endpoint_url text NOT NULL,
    model_id text NOT NULL,
    api_key text DEFAULT ''::text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    is_local boolean DEFAULT false NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    use_arch_review boolean DEFAULT true NOT NULL,
    use_uc_assist boolean DEFAULT false NOT NULL,
    capabilities jsonb DEFAULT '{}'::jsonb NOT NULL,
    project_id bigint,
    use_category text,
    bundle_attachment_id bigint,
    CONSTRAINT review_model_configs_provider_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text])))
);

CREATE TABLE public.model_defaults (
    key character varying(64) NOT NULL,
    model_config_id integer,
    updated_by character varying(256) DEFAULT 'system'::character varying NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    project_id bigint NOT NULL
);

CREATE TABLE public.model_use_profiles (
    id integer NOT NULL,
    model_config_id integer NOT NULL,
    use_key text NOT NULL,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    updated_by text DEFAULT 'migration'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT model_use_profiles_use_key_chk CHECK ((use_key = ANY (ARRAY['evaluation_verification'::text, 'evaluation_explore'::text, 'evaluation_reproduce'::text, 'arch_review'::text, 'uc_assist'::text, 'enhancement'::text])))
);

CREATE SEQUENCE public.model_use_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.model_use_profiles_id_seq OWNED BY public.model_use_profiles.id;

CREATE TABLE public.output_templates (
    id bigint NOT NULL,
    name text NOT NULL,
    kind text DEFAULT 'report'::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    project_id bigint,
    use_category text,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.output_templates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.output_templates_id_seq OWNED BY public.output_templates.id;

CREATE TABLE public.project_members (
    project_id bigint NOT NULL,
    reviewer text NOT NULL,
    role text DEFAULT 'member'::text NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.projects (
    id bigint NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    uc_repo_uuid uuid,
    uc_path text DEFAULT ''::text NOT NULL,
    uc_branch text DEFAULT ''::text NOT NULL,
    is_exclusive boolean DEFAULT false NOT NULL,
    tenant_id bigint
);

CREATE SEQUENCE public.projects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;

CREATE TABLE public.rbac_account_roles (
    id bigint NOT NULL,
    reviewer text NOT NULL,
    role_id bigint NOT NULL,
    project_id bigint,
    granted_by text DEFAULT 'system'::text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    customer_id bigint,
    spans_all boolean DEFAULT false NOT NULL,
    tenant_id bigint
);

CREATE SEQUENCE public.rbac_account_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.rbac_account_roles_id_seq OWNED BY public.rbac_account_roles.id;

CREATE TABLE public.rbac_group_members (
    group_id bigint NOT NULL,
    reviewer text NOT NULL,
    added_by text DEFAULT 'system'::text NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.rbac_group_role_mappings (
    id bigint NOT NULL,
    source text DEFAULT 'ldap'::text NOT NULL,
    group_key text NOT NULL,
    role_id bigint NOT NULL,
    project_id bigint,
    created_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    customer_id bigint,
    spans_all boolean DEFAULT false NOT NULL,
    tenant_id bigint
);

CREATE SEQUENCE public.rbac_group_role_mappings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.rbac_group_role_mappings_id_seq OWNED BY public.rbac_group_role_mappings.id;

CREATE TABLE public.rbac_group_roles (
    id bigint NOT NULL,
    group_id bigint NOT NULL,
    role_id bigint NOT NULL,
    granted_by text DEFAULT 'system'::text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.rbac_group_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.rbac_group_roles_id_seq OWNED BY public.rbac_group_roles.id;

CREATE TABLE public.rbac_groups (
    id bigint NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    scope text NOT NULL,
    tenant_id bigint,
    project_id bigint,
    customer_id bigint,
    source text DEFAULT 'internal'::text NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.rbac_groups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.rbac_groups_id_seq OWNED BY public.rbac_groups.id;

CREATE TABLE public.rbac_privileges (
    key text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    scope text DEFAULT 'project'::text NOT NULL
);

CREATE TABLE public.rbac_role_privileges (
    role_id bigint NOT NULL,
    privilege_key text NOT NULL
);

CREATE TABLE public.rbac_roles (
    id bigint NOT NULL,
    key text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    scope text DEFAULT 'project'::text NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.rbac_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.rbac_roles_id_seq OWNED BY public.rbac_roles.id;

CREATE SEQUENCE public.review_model_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.review_model_configs_id_seq OWNED BY public.model_configs.id;

CREATE TABLE public.tenants (
    id bigint NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    isolation_level text DEFAULT 'hard'::text NOT NULL,
    declared_regime text DEFAULT 'none'::text NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.tenants_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.tenants_id_seq OWNED BY public.tenants.id;

CREATE TABLE public.use_categories (
    key text NOT NULL,
    label text NOT NULL,
    "position" integer DEFAULT 100 NOT NULL
);

CREATE TABLE public.user_invitations (
    token text NOT NULL,
    email text NOT NULL,
    display_name text DEFAULT ''::text NOT NULL,
    project_id bigint,
    project_role text DEFAULT 'editor'::text NOT NULL,
    global_role text DEFAULT 'editor'::text NOT NULL,
    invited_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone
);

CREATE TABLE public.user_settings (
    reviewer text NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.users (
    reviewer text NOT NULL,
    email text DEFAULT ''::text NOT NULL,
    display_name text DEFAULT ''::text NOT NULL,
    role text DEFAULT 'editor'::text NOT NULL,
    approved boolean DEFAULT false NOT NULL,
    source text DEFAULT 'ldap'::text NOT NULL,
    last_seen timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    password_hash text,
    must_change_password boolean DEFAULT false NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    default_project_id bigint,
    kind text DEFAULT 'person'::text NOT NULL
);

ALTER TABLE ONLY public.api_tokens ALTER COLUMN id SET DEFAULT nextval('public.api_tokens_id_seq'::regclass);

ALTER TABLE ONLY public.bundle_attachments ALTER COLUMN id SET DEFAULT nextval('public.bundle_attachments_id_seq'::regclass);

ALTER TABLE ONLY public.bundle_items ALTER COLUMN id SET DEFAULT nextval('public.bundle_items_id_seq'::regclass);

ALTER TABLE ONLY public.bundle_versions ALTER COLUMN id SET DEFAULT nextval('public.bundle_versions_id_seq'::regclass);

ALTER TABLE ONLY public.bundles ALTER COLUMN id SET DEFAULT nextval('public.bundles_id_seq'::regclass);

ALTER TABLE ONLY public.code_repo_configs ALTER COLUMN id SET DEFAULT nextval('public.code_repo_configs_id_seq'::regclass);

ALTER TABLE ONLY public.credentials ALTER COLUMN id SET DEFAULT nextval('public.credentials_id_seq'::regclass);

ALTER TABLE ONLY public.customers ALTER COLUMN id SET DEFAULT nextval('public.customers_id_seq'::regclass);

ALTER TABLE ONLY public.mcp_server_configs ALTER COLUMN id SET DEFAULT nextval('public.mcp_server_configs_id_seq'::regclass);

ALTER TABLE ONLY public.model_configs ALTER COLUMN id SET DEFAULT nextval('public.review_model_configs_id_seq'::regclass);

ALTER TABLE ONLY public.model_use_profiles ALTER COLUMN id SET DEFAULT nextval('public.model_use_profiles_id_seq'::regclass);

ALTER TABLE ONLY public.output_templates ALTER COLUMN id SET DEFAULT nextval('public.output_templates_id_seq'::regclass);

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);

ALTER TABLE ONLY public.rbac_account_roles ALTER COLUMN id SET DEFAULT nextval('public.rbac_account_roles_id_seq'::regclass);

ALTER TABLE ONLY public.rbac_group_role_mappings ALTER COLUMN id SET DEFAULT nextval('public.rbac_group_role_mappings_id_seq'::regclass);

ALTER TABLE ONLY public.rbac_group_roles ALTER COLUMN id SET DEFAULT nextval('public.rbac_group_roles_id_seq'::regclass);

ALTER TABLE ONLY public.rbac_groups ALTER COLUMN id SET DEFAULT nextval('public.rbac_groups_id_seq'::regclass);

ALTER TABLE ONLY public.rbac_roles ALTER COLUMN id SET DEFAULT nextval('public.rbac_roles_id_seq'::regclass);

ALTER TABLE ONLY public.tenants ALTER COLUMN id SET DEFAULT nextval('public.tenants_id_seq'::regclass);

ALTER TABLE ONLY public.account_identities
    ADD CONSTRAINT account_identities_pkey PRIMARY KEY (alias);

ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.api_tokens
    ADD CONSTRAINT api_tokens_token_hash_key UNIQUE (token_hash);

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (key);

ALTER TABLE ONLY public.assessment_frameworks
    ADD CONSTRAINT assessment_frameworks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bundle_attachments
    ADD CONSTRAINT bundle_attachments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bundle_items
    ADD CONSTRAINT bundle_items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bundle_versions
    ADD CONSTRAINT bundle_versions_bundle_id_version_no_key UNIQUE (bundle_id, version_no);

ALTER TABLE ONLY public.bundle_versions
    ADD CONSTRAINT bundle_versions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bundles
    ADD CONSTRAINT bundles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.bundles
    ADD CONSTRAINT bundles_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.capability_aliases
    ADD CONSTRAINT capability_aliases_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.capability_antipatterns
    ADD CONSTRAINT capability_antipatterns_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.capability_taxonomy_terms
    ADD CONSTRAINT capability_taxonomy_terms_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.code_repo_configs
    ADD CONSTRAINT code_repo_configs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.credentials
    ADD CONSTRAINT credentials_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.credentials
    ADD CONSTRAINT credentials_tenant_id_credential_type_name_key UNIQUE (tenant_id, credential_type, name);

ALTER TABLE ONLY public.credentials
    ADD CONSTRAINT credentials_uuid_key UNIQUE (uuid);

ALTER TABLE ONLY public.customer_projects
    ADD CONSTRAINT customer_projects_pkey PRIMARY KEY (customer_id, project_id);

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.framework_capabilities
    ADD CONSTRAINT framework_capabilities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.framework_categories
    ADD CONSTRAINT framework_categories_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.framework_states
    ADD CONSTRAINT framework_states_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.mcp_server_configs
    ADD CONSTRAINT mcp_server_configs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_defaults
    ADD CONSTRAINT model_defaults_pkey PRIMARY KEY (project_id, key);

ALTER TABLE ONLY public.model_use_profiles
    ADD CONSTRAINT model_use_profiles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_use_profiles
    ADD CONSTRAINT model_use_profiles_uniq UNIQUE (model_config_id, use_key);

ALTER TABLE ONLY public.output_templates
    ADD CONSTRAINT output_templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.project_members
    ADD CONSTRAINT project_members_pkey PRIMARY KEY (project_id, reviewer);

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.rbac_account_roles
    ADD CONSTRAINT rbac_account_roles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.rbac_group_members
    ADD CONSTRAINT rbac_group_members_pkey PRIMARY KEY (group_id, reviewer);

ALTER TABLE ONLY public.rbac_group_role_mappings
    ADD CONSTRAINT rbac_group_role_mappings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.rbac_group_roles
    ADD CONSTRAINT rbac_group_roles_group_id_role_id_key UNIQUE (group_id, role_id);

ALTER TABLE ONLY public.rbac_group_roles
    ADD CONSTRAINT rbac_group_roles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.rbac_groups
    ADD CONSTRAINT rbac_groups_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.rbac_privileges
    ADD CONSTRAINT rbac_privileges_pkey PRIMARY KEY (key);

ALTER TABLE ONLY public.rbac_role_privileges
    ADD CONSTRAINT rbac_role_privileges_pkey PRIMARY KEY (role_id, privilege_key);

ALTER TABLE ONLY public.rbac_roles
    ADD CONSTRAINT rbac_roles_key_key UNIQUE (key);

ALTER TABLE ONLY public.rbac_roles
    ADD CONSTRAINT rbac_roles_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_configs
    ADD CONSTRAINT review_model_configs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_slug_key UNIQUE (slug);

ALTER TABLE ONLY public.framework_capabilities
    ADD CONSTRAINT uq_cap_key UNIQUE (category_id, key);

ALTER TABLE ONLY public.framework_categories
    ADD CONSTRAINT uq_cat_key UNIQUE (framework_id, key);

ALTER TABLE ONLY public.framework_states
    ADD CONSTRAINT uq_state_key UNIQUE (framework_id, key);

ALTER TABLE ONLY public.use_categories
    ADD CONSTRAINT use_categories_pkey PRIMARY KEY (key);

ALTER TABLE ONLY public.user_invitations
    ADD CONSTRAINT user_invitations_pkey PRIMARY KEY (token);

ALTER TABLE ONLY public.user_settings
    ADD CONSTRAINT user_settings_pkey PRIMARY KEY (reviewer);

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (reviewer);

CREATE INDEX api_tokens_email_idx ON public.api_tokens USING btree (email);

CREATE INDEX api_tokens_hash_idx ON public.api_tokens USING btree (token_hash);

CREATE INDEX idx_account_identities_reviewer ON public.account_identities USING btree (lower(reviewer));

CREATE UNIQUE INDEX idx_alias_canonical ON public.capability_aliases USING btree (family, pillar, scope_tier, COALESCE(project_id, (0)::bigint), lower(handle)) WHERE ((lifecycle_state = 'CANONICAL'::text) AND is_current);

CREATE INDEX idx_alias_family ON public.capability_aliases USING btree (family);

CREATE INDEX idx_alias_tags ON public.capability_aliases USING gin (scope_tags);

CREATE INDEX idx_alias_term ON public.capability_aliases USING btree (resolves_to_term_id);

CREATE UNIQUE INDEX idx_antipattern_canonical ON public.capability_antipatterns USING btree (family, pillar, scope_tier, COALESCE(project_id, (0)::bigint), lower(handle)) WHERE ((lifecycle_state = 'CANONICAL'::text) AND is_current);

CREATE INDEX idx_antipattern_family ON public.capability_antipatterns USING btree (family);

CREATE INDEX idx_antipattern_tags ON public.capability_antipatterns USING gin (scope_tags);

CREATE INDEX idx_antipattern_term ON public.capability_antipatterns USING btree (related_term_id);

CREATE UNIQUE INDEX idx_bundle_attach_bundle_scope ON public.bundle_attachments USING btree (bundle_id, COALESCE(project_id, (0)::bigint), COALESCE(use_category, ''::text));

CREATE INDEX idx_bundle_attach_scope ON public.bundle_attachments USING btree (project_id, use_category);

CREATE INDEX idx_bundle_items_version ON public.bundle_items USING btree (bundle_version_id);

CREATE INDEX idx_bundle_versions_bundle ON public.bundle_versions USING btree (bundle_id);

CREATE INDEX idx_cap_category ON public.framework_capabilities USING btree (category_id);

CREATE INDEX idx_cat_framework ON public.framework_categories USING btree (framework_id);

CREATE UNIQUE INDEX idx_code_repos_name ON public.code_repo_configs USING btree (lower(name));

CREATE INDEX idx_credentials_tenant ON public.credentials USING btree (tenant_id);

CREATE INDEX idx_credentials_type ON public.credentials USING btree (credential_type);

CREATE INDEX idx_customer_projects_project ON public.customer_projects USING btree (project_id);

CREATE INDEX idx_framework_project ON public.assessment_frameworks USING btree (project_id);

CREATE INDEX idx_mcp_by_attachment ON public.mcp_server_configs USING btree (bundle_attachment_id) WHERE (bundle_attachment_id IS NOT NULL);

CREATE INDEX idx_mcp_servers_project ON public.mcp_server_configs USING btree (project_id);

CREATE UNIQUE INDEX idx_mcp_servers_scope_name ON public.mcp_server_configs USING btree (COALESCE(project_id, (0)::bigint), COALESCE(use_category, ''::text), lower(name));

CREATE INDEX idx_model_by_attachment ON public.model_configs USING btree (bundle_attachment_id) WHERE (bundle_attachment_id IS NOT NULL);

CREATE INDEX idx_model_configs_project ON public.model_configs USING btree (project_id);

CREATE UNIQUE INDEX idx_model_configs_scope_name ON public.model_configs USING btree (COALESCE(project_id, (0)::bigint), COALESCE(use_category, ''::text), lower(name));

CREATE INDEX idx_model_use_profiles_model ON public.model_use_profiles USING btree (model_config_id);

CREATE UNIQUE INDEX idx_output_templates_scope_name ON public.output_templates USING btree (COALESCE(project_id, (0)::bigint), COALESCE(use_category, ''::text), lower(name));

CREATE INDEX idx_projects_tenant ON public.projects USING btree (tenant_id);

CREATE INDEX idx_rbac_acct_role_acct ON public.rbac_account_roles USING btree (lower(reviewer));

CREATE UNIQUE INDEX idx_rbac_acct_role_uniq ON public.rbac_account_roles USING btree (lower(reviewer), role_id, COALESCE(project_id, (0)::bigint), COALESCE(customer_id, (0)::bigint), COALESCE(tenant_id, (0)::bigint));

CREATE INDEX idx_rbac_group_members_reviewer ON public.rbac_group_members USING btree (lower(reviewer));

CREATE UNIQUE INDEX idx_rbac_groups_scope_name ON public.rbac_groups USING btree (scope, COALESCE(tenant_id, (0)::bigint), COALESCE(project_id, (0)::bigint), COALESCE(customer_id, (0)::bigint), lower(name));

CREATE UNIQUE INDEX idx_rbac_grp_role_uniq ON public.rbac_group_role_mappings USING btree (source, lower(group_key), role_id, COALESCE(project_id, (0)::bigint));

CREATE INDEX idx_state_framework ON public.framework_states USING btree (framework_id);

CREATE UNIQUE INDEX idx_taxterm_canonical ON public.capability_taxonomy_terms USING btree (family, pillar, scope_tier, COALESCE(project_id, (0)::bigint), lower(handle)) WHERE ((lifecycle_state = 'CANONICAL'::text) AND is_current);

CREATE INDEX idx_taxterm_domain ON public.capability_taxonomy_terms USING btree (domain_prefix);

CREATE INDEX idx_taxterm_family ON public.capability_taxonomy_terms USING btree (family);

CREATE INDEX idx_taxterm_project ON public.capability_taxonomy_terms USING btree (project_id);

CREATE INDEX idx_taxterm_state ON public.capability_taxonomy_terms USING btree (lifecycle_state);

CREATE INDEX idx_taxterm_tags ON public.capability_taxonomy_terms USING gin (scope_tags);

CREATE INDEX idx_user_invitations_email ON public.user_invitations USING btree (lower(email));

CREATE INDEX idx_users_email ON public.users USING btree (lower(email));

CREATE UNIQUE INDEX uq_framework_seed_key ON public.assessment_frameworks USING btree (key) WHERE (project_id IS NULL);

CREATE TRIGGER trg_credentials_updated_at BEFORE UPDATE ON public.credentials FOR EACH ROW EXECUTE FUNCTION public._touch_credentials_updated_at();

ALTER TABLE ONLY public.assessment_frameworks
    ADD CONSTRAINT assessment_frameworks_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bundle_attachments
    ADD CONSTRAINT bundle_attachments_bundle_id_fkey FOREIGN KEY (bundle_id) REFERENCES public.bundles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bundle_attachments
    ADD CONSTRAINT bundle_attachments_bundle_version_id_fkey FOREIGN KEY (bundle_version_id) REFERENCES public.bundle_versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bundle_attachments
    ADD CONSTRAINT bundle_attachments_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bundle_items
    ADD CONSTRAINT bundle_items_bundle_version_id_fkey FOREIGN KEY (bundle_version_id) REFERENCES public.bundle_versions(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.bundle_versions
    ADD CONSTRAINT bundle_versions_bundle_id_fkey FOREIGN KEY (bundle_id) REFERENCES public.bundles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.capability_aliases
    ADD CONSTRAINT capability_aliases_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.capability_aliases
    ADD CONSTRAINT capability_aliases_resolves_to_term_id_fkey FOREIGN KEY (resolves_to_term_id) REFERENCES public.capability_taxonomy_terms(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.capability_antipatterns
    ADD CONSTRAINT capability_antipatterns_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.capability_antipatterns
    ADD CONSTRAINT capability_antipatterns_related_term_id_fkey FOREIGN KEY (related_term_id) REFERENCES public.capability_taxonomy_terms(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.capability_taxonomy_terms
    ADD CONSTRAINT capability_taxonomy_terms_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.capability_taxonomy_terms(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.capability_taxonomy_terms
    ADD CONSTRAINT capability_taxonomy_terms_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.customer_projects
    ADD CONSTRAINT customer_projects_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.customer_projects
    ADD CONSTRAINT customer_projects_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.framework_capabilities
    ADD CONSTRAINT framework_capabilities_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.framework_categories(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.framework_categories
    ADD CONSTRAINT framework_categories_framework_id_fkey FOREIGN KEY (framework_id) REFERENCES public.assessment_frameworks(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.framework_states
    ADD CONSTRAINT framework_states_framework_id_fkey FOREIGN KEY (framework_id) REFERENCES public.assessment_frameworks(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.mcp_server_configs
    ADD CONSTRAINT mcp_server_configs_bundle_attachment_id_fkey FOREIGN KEY (bundle_attachment_id) REFERENCES public.bundle_attachments(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.mcp_server_configs
    ADD CONSTRAINT mcp_server_configs_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY public.model_configs
    ADD CONSTRAINT model_configs_bundle_attachment_id_fkey FOREIGN KEY (bundle_attachment_id) REFERENCES public.bundle_attachments(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.model_configs
    ADD CONSTRAINT model_configs_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY public.model_defaults
    ADD CONSTRAINT model_defaults_model_config_id_fkey FOREIGN KEY (model_config_id) REFERENCES public.model_configs(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.model_defaults
    ADD CONSTRAINT model_defaults_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);

ALTER TABLE ONLY public.model_use_profiles
    ADD CONSTRAINT model_use_profiles_model_config_id_fkey FOREIGN KEY (model_config_id) REFERENCES public.model_configs(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.output_templates
    ADD CONSTRAINT output_templates_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.project_members
    ADD CONSTRAINT project_members_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);

ALTER TABLE ONLY public.rbac_account_roles
    ADD CONSTRAINT rbac_account_roles_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_account_roles
    ADD CONSTRAINT rbac_account_roles_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_account_roles
    ADD CONSTRAINT rbac_account_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.rbac_roles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_account_roles
    ADD CONSTRAINT rbac_account_roles_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_group_members
    ADD CONSTRAINT rbac_group_members_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.rbac_groups(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_group_role_mappings
    ADD CONSTRAINT rbac_group_role_mappings_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_group_role_mappings
    ADD CONSTRAINT rbac_group_role_mappings_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_group_role_mappings
    ADD CONSTRAINT rbac_group_role_mappings_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.rbac_roles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_group_role_mappings
    ADD CONSTRAINT rbac_group_role_mappings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_group_roles
    ADD CONSTRAINT rbac_group_roles_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.rbac_groups(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_group_roles
    ADD CONSTRAINT rbac_group_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.rbac_roles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_groups
    ADD CONSTRAINT rbac_groups_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_groups
    ADD CONSTRAINT rbac_groups_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_groups
    ADD CONSTRAINT rbac_groups_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_role_privileges
    ADD CONSTRAINT rbac_role_privileges_privilege_key_fkey FOREIGN KEY (privilege_key) REFERENCES public.rbac_privileges(key) ON DELETE CASCADE;

ALTER TABLE ONLY public.rbac_role_privileges
    ADD CONSTRAINT rbac_role_privileges_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.rbac_roles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.user_invitations
    ADD CONSTRAINT user_invitations_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;

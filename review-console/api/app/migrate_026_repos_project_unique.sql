-- migrate_026: project-scope managed_repos namespaces (tenancy Phase 0).
-- A repo namespace was GLOBALLY unique, so the same definition (e.g. the `dav`
-- repo) could be registered for only one project. Make it unique **per project**
-- so each project owns its own repo definitions (Kubernetes namespace model:
-- names unique within a namespace, not globally). COALESCE(project_id,0) keeps a
-- single platform-level (NULL) slot distinct, for later platform-default repos.
ALTER TABLE managed_repos DROP CONSTRAINT IF EXISTS managed_repos_namespace_key;
CREATE UNIQUE INDEX IF NOT EXISTS managed_repos_project_namespace_key
    ON managed_repos (COALESCE(project_id, 0), namespace);

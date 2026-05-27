# ADR-007 — Per-Role Path Overrides + Corpus Projection Parity

**Status:** Accepted
**Date:** 2026-05-27
**Author:** Chris Roadfeldt + Claude
**Extends:** [ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md),
[ADR-006](006-consolidate-code-repos-into-managed-repos.md)

---

## 1. Context

[ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md) made
`managed_repos` the registry, with `roles[]` distinguishing purpose. A
single `root_path` column has so far been the "where the served content
lives" pointer.

That model breaks when the **same repo carries multiple roles** —
which is the common case for a project that doubles as its own
substrate spec, test corpus, and enhancement target. DCM is exactly
this shape: `roles=[spec, corpus]` with content in different
subdirectories:

- `spec`  → `architecture/` (served by dav-docs-mcp)
- `corpus` → `dav/use-cases/` (cloned by the Tekton pipeline for runs)
- `enhancement-target` (when added) → repo root (where PRs land)

A single `root_path = "architecture"` works for spec but is wrong for
corpus.

Compounding: the corpus side was never given the same multi-source +
projector treatment as spec (ADR-003 only landed spec in the M2
projector). The `dav-source-corpus` ConfigMap is still single-source;
the Tekton pipeline clones one repo, walks one directory.

The questions this ADR settles:

1. **How do operators express per-role paths on a single managed_repos row?**
2. **What does multi-source corpus look like, end-to-end (registry →
   ConfigMap → pipeline → engine)?**
3. **Can operators pick which corpus repos to include per run?**

## 2. Decision

### 2.1 Per-role path overrides via metadata.role_paths

`managed_repos.root_path` stays as the row's **default** path. A new
optional `metadata.role_paths.{role}` overrides that default for a
specific role. New helper `repos.resolve_root_path(repo, role)`:

```python
def resolve_root_path(repo, role):
    overrides = (repo.get("metadata") or {}).get("role_paths") or {}
    return overrides.get(role) if role in overrides else (repo.get("root_path") or "")
```

No schema change. Backward compatible: repos with no overrides keep
working as today. Adopters set overrides only where they matter.

DCM's row becomes:
```yaml
root_path: "architecture"
metadata:
  role_paths:
    corpus: "dav/use-cases"
    # spec inherits root_path ("architecture")
    # enhancement-target inherits root_path; could override to "" if needed
```

### 2.2 Corpus multi-source + projector

`dav-source-corpus` ConfigMap gains the same shape as `dav-source-spec`
post-M2: either legacy `repo_url`+`repo_branch` keys, or new `sources`
YAML list. The projector grows a sibling `project_corpus_sources(conn)`
that regenerates the ConfigMap from `managed_repos` rows with
`role=corpus`, using `resolve_root_path(repo, 'corpus')` for each
source's per-role path.

CRUD hooks on managed_repos: if `role=corpus` is touched (added,
removed, or the row's URL/branch/path changed while carrying corpus),
the corpus projector runs. The spec projector (already in place) gets
the same `resolve_root_path` treatment — operators with per-role spec
overrides see them honored.

Unlike spec, the corpus ConfigMap does NOT trigger a rolling restart.
The corpus is consumed by Tekton PipelineRuns; each PipelineRun reads
the current ConfigMap at start, so a content change naturally takes
effect on the next run.

### 2.3 Per-run corpus selector

The New Run modal gains a "Corpus sources" multi-select that lists
every role=corpus repo. Default selection: all of them. The selection
flows through the API trigger as a `corpus_namespaces` list, which
becomes a Tekton PipelineRun param. The pipeline's git-sync step
filters the ConfigMap's `sources` list by that param so only selected
repos clone.

Each selected source clones into `/workspace/corpus/<namespace>/`. The
engine's `--corpus-path` points at `/workspace/corpus/` (parent); it
walks recursively to find UC files across all included sources.

Operator workflow:
- **Common case** (regression): default all-selected runs the full corpus
- **Ad-hoc / debug**: deselect everything except the one corpus you want

## 3. Alternatives Considered

### A. Drop root_path; require all paths via metadata.role_paths

Forces every operator to set per-role paths even when they're all the
same.

**Rejected because:** common-case noise. Single-purpose repos with one
content directory stay simple with the existing root_path.

### B. Separate `managed_repo_role_config(repo_id, role, root_path, ...)` table

Fully normalized per-role config in its own table.

**Deferred** to a future ADR if per-role config grows beyond paths
(e.g., per-role credential FKs, per-role webhook secrets, per-role
ingestion config). For v1 the JSONB approach gets us the same operator
ergonomics without schema churn or join overhead.

### C. Walk-all corpus (no per-run selector)

Pipeline always walks every role=corpus repo. Simpler.

**Rejected per user direction** — the per-run selector is wanted for
ad-hoc and debug workflows. Walk-all is still the default.

## 4. Consequences

### Positive

- One repo row covers multiple roles cleanly when each role wants a
  different content path.
- DCM-shaped projects (spec + corpus in one repo) get correct per-role
  paths without splitting the registry row.
- Corpus parity with spec: same projection model, same UI affordance,
  same operator mental model.
- Per-run selector enables focused regression runs (e.g., "run only
  the downstream corpus to triage a new bug") without touching the
  registry.

### Negative

- Two ConfigMap-projection write paths (spec + corpus) in the API;
  both have to stay consistent. Mitigated by reusing the projector
  module shape; the spec helper is the template.
- The Tekton pipeline's git-sync step gains complexity (multi-clone
  loop with namespace filter). Mitigated by isolating it in a single
  init-container script that parses the ConfigMap + namespace filter.
- New Run modal gains a multi-select. Reasonable for advanced ops;
  default = all keeps the common path unchanged.

## 5. Migration

No DB migration (metadata is already JSONB). For DCM specifically,
post-deploy the operator adds:

```
PUT /api/repos/dcm
{
  "metadata": {
    "provider": ...,
    "role_paths": {"corpus": "dav/use-cases"}
  }
}
```

(Or via the Repos UI's new Per-role paths section.)

For the corpus ConfigMap, the existing seed-from-existing-ConfigMap
helper handles the legacy single-source → multi-source projection on
first registry change touching role=corpus.

## 6. Implementation status

- `repos.resolve_root_path(repo, role)` helper
- `projector.project_corpus_sources(conn)` sibling
- `main.py` hooks corpus projector on role=corpus CRUD; seeds at startup
- `source-corpus-configmap.yaml.j2` supports `sources` YAML list shape
- Tekton `dav-git-sync` (or wrapper) extended for multi-clone
- `pipeline-stage2.yaml.j2` gains `corpus-namespaces` param
- API `RunTriggerIn` accepts `corpus_namespaces: list[str]`
- New Run modal: corpus source multi-select
- Repos UI: per-role paths editor on the repo form
- Sources → Evaluation corpus panel: read-only (M4 parity)

## 7. Related

- [ADR-003](003-multi-repo-registry-and-mcp-source-of-truth.md) — the
  registry this extends
- [ADR-004](004-per-repo-credentials-in-registry.md) — per-repo Fernet
  credentials (also a candidate for future per-role overrides via the
  same metadata.role_paths pattern, generalized)
- [ADR-005](005-shared-credentials-abstraction.md) — shared credentials
- [ADR-006](006-consolidate-code-repos-into-managed-repos.md) —
  enhancement-target role added; resolve_root_path will return ""
  (root) for it by default
- `docs/review-console-design.md` — Per-role paths section in the Repos
  panel; Evaluation corpus read-only block parallels the M4 spec section

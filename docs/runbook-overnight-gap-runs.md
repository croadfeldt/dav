# Runbook — overnight corpus gap-analysis runs (repeatable)

**Goal.** Run the full UC corpus (21-Sept baseline + all hammer sets + the whole corpus) through DAV's
gap engine for the DCM (and DAV) projects, so runs are **visible in the review-console UI**, then consolidate
the gaps into a report.

## Preconditions
- DAV reachable: `https://10.0.90.22:8843` (IPv4; homelab). Inference model up: `qwen3-32b.llm.ocp.roadfeldt.com`.
- PAT: `~/.claude-personal/.dav-token` (Bearer). Project is selected via the **`X-DAV-Project`** header (NOT a query param).
- Corpus is a registered corpus repo; the UCs live on `dcm` **main** under `dav/use-cases/`.

## Projects
- `20` = **DCM** (realization analysis) · `727` = **DAV** (self-analysis) · `1` = Default.

## Trigger a run (creates a `run_session`, visible in the UI)
`POST /api/runs` with header `X-DAV-Project: <pid>` and body (`RunTriggerIn`):
- `mode`: `verification` (default gap mode; 3 ensemble samples/UC).
- Whole corpus: `{"selection_mode":"corpus","set_id":"__all__"}`.
- One set: `{"selection_mode":"set","set_id":<id>,"set_name":"<your set>"}` (ids from GET /api/sets).
  The console resolves the set's members server-side and scopes the engine to exactly them.
  (Before the set-selection fix — repro run `dav-stage2-console-853521` — this shape silently
  ran the FULL corpus: set_id/selection_mode were stored as lineage only. On a fixed console an
  empty/unknown set is a 400/404, never a full-corpus fallback.)
- Always set `name` + `description` (they show in the console).

```bash
TOK=$(cat ~/.claude-personal/.dav-token); B=https://10.0.90.22:8843
curl -sk -4 -X POST -H "Authorization: Bearer $TOK" -H "X-DAV-Project: 20" -H "Content-Type: application/json" \
  -d '{"mode":"verification","selection_mode":"corpus","set_id":"__all__","name":"<name>","description":"<desc>"}' \
  "$B/api/runs"
```
Response: `{"ok":true,"run":{"name":"dav-stage2-console-NNNNNN",...},"resolved_params":{"inference_model":"qwen3-32b",...}}`.

## Read state (all GET, `X-DAV-Project` header)
- Sets: `/api/sets` · Runs: `/api/runs` · Gaps: `/api/analysis/gaps` · Roadmap: `/api/analysis/roadmap` · UCs: `/api/use-cases?limit=N`.

## Corpus registration — what a "full corpus" run actually contains

The console (and the engine, via the projected `dav-source-corpus` ConfigMap) reads corpus sources from
`managed_repos WHERE 'corpus' = ANY(roles)`. The **effective corpus root** per repo is
`metadata.role_paths.corpus` when present, else the `root_path` column (ADR-007 — one repo can serve
different subdirs to different roles; `resolve_root_path()` in `review-console/api/app/repos.py`).

**Live project-20 state (read 2026-07-24 via `GET /api/repos?role=corpus`):**

| namespace | branch | `root_path` col | `role_paths.corpus` | effective corpus root | verdict |
|---|---|---|---|---|---|
| `dcm`  | main | `architecture` (serves **spec**) | `dav` | `dav` | over-inclusive: pulls `dav/CHANGELOG.md`, `dav/schemas/**`, etc. alongside `dav/use-cases/**` |
| `udlm` | main | `''` (serves **spec** = whole repo) | — | `''` = whole repo | over-inclusive: 369 cached files, only 18 under `use-cases/` |
| `dav`  | main | `''` | — | whole repo | same pattern (out of scope here) |

Target state: `dcm` corpus root `dav/use-cases`, `udlm` corpus root `use-cases`.

> **Do NOT change the `root_path` column on either row.** It serves the `spec` role
> (`dcm`: `architecture`; `udlm`: whole repo). The corpus scoping lives in
> `metadata.role_paths.corpus`.

### Preferred method — the repos API (does projection + cache resync for you)

`PUT /api/repos/{namespace}` re-projects the corpus ConfigMap and resyncs the corpus-files cache
automatically on any corpus-role write. `metadata` is replaced wholesale, so send the full desired dict
(check current value first with `GET /api/repos/{namespace}`):

```bash
TOK=$(cat ~/.claude-personal/.dav-token); B=https://10.0.90.22:8843
# dcm: corpus root 'dav' -> 'dav/use-cases' (preserves nothing else — current metadata is only role_paths)
curl -sk -4 -X PUT -H "Authorization: Bearer $TOK" -H "X-DAV-Project: 20" -H "Content-Type: application/json" \
  -d '{"metadata":{"role_paths":{"corpus":"dav/use-cases"}}}' "$B/api/repos/dcm"
# udlm: scope the corpus role to use-cases/ (root_path '' keeps serving spec = whole repo)
curl -sk -4 -X PUT -H "Authorization: Bearer $TOK" -H "X-DAV-Project: 20" -H "Content-Type: application/json" \
  -d '{"metadata":{"role_paths":{"corpus":"use-cases"}}}' "$B/api/repos/udlm"
```

Verify: `GET /api/repos?role=corpus` shows the new `role_paths`, and `GET /api/corpus` no longer lists
`dcm/CHANGELOG.md` / `udlm/AGENTS.md`-style paths — corpus entries sit under `<ns>/<path relative to the
corpus root>` (e.g. `udlm/bare-metal/...`).

### Fallback — direct SQL (maintenance window only)

Raw SQL skips the ConfigMap projection and the corpus-files cache resync that the API performs —
after applying, force both by re-saving either repo via the API (a PUT re-sending the same
`metadata` — idempotent, but it triggers projection + resync) or at minimum `POST /api/corpus/resync`,
and confirm the projected `dav-source-corpus` ConfigMap changed.

```sql
-- 0. Inspect current state FIRST (also the after-verification query)
SELECT namespace, repo_url, repo_branch, root_path, metadata
FROM managed_repos WHERE 'corpus' = ANY(roles) AND project_id = 20 ORDER BY namespace;

-- 1. dcm: corpus root 'dav' -> 'dav/use-cases' (root_path column untouched — it serves spec)
UPDATE managed_repos
SET metadata = jsonb_set(
      COALESCE(metadata,'{}'::jsonb)
        || jsonb_build_object('role_paths', COALESCE(metadata->'role_paths','{}'::jsonb)),
      '{role_paths,corpus}', '"dav/use-cases"', true),
    updated_at = now()
WHERE namespace = 'dcm' AND project_id = 20 AND 'corpus' = ANY(roles);

-- 2. udlm: scope the corpus role to use-cases/ (row already EXISTS with role=corpus as of
--    2026-07-24; the INSERT below is only for an environment where it does not)
UPDATE managed_repos
SET metadata = jsonb_set(
      COALESCE(metadata,'{}'::jsonb)
        || jsonb_build_object('role_paths', COALESCE(metadata->'role_paths','{}'::jsonb)),
      '{role_paths,corpus}', '"use-cases"', true),
    updated_at = now()
WHERE namespace = 'udlm' AND project_id = 20 AND 'corpus' = ANY(roles);

-- 2b. Only if no udlm corpus row exists (0 rows updated above and namespace absent):
-- INSERT INTO managed_repos (namespace, repo_url, repo_branch, display_name, root_path,
--                            roles, tenant_id, project_id, metadata, created_by)
-- VALUES ('udlm', 'https://github.com/croadfeldt/udlm.git', 'main', 'udlm', '',
--         ARRAY['corpus'], 'default', 20,
--         '{"role_paths":{"corpus":"use-cases"}}'::jsonb, 'runbook');

-- 3. Verify: re-run the SELECT from step 0; expect
--    dcm  metadata -> role_paths -> corpus = 'dav/use-cases'
--    udlm metadata -> role_paths -> corpus = 'use-cases'
```

## Notes / gotchas
- The run **trigger** is on the review-console API (`POST /api/runs`), NOT the ops-mcp (which is read + set-management only).
- New/unpushed UCs can be included per-run via `managed_uc_uuids` / `uc_uuids`; pushed corpus UCs come from git (main).
- There is **no per-project `spec_repo`** configured — project 20 analyzes the corpus; a dedicated UDLM-data-model
  analysis project/spec is not set up (flagged as a gap).

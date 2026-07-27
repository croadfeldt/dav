# Run-source resolution from the registry — retiring the ConfigMap plane

_Design note, 2026-07-27, implementing Chris's ruling on the friction inventory
(`corpus-scope-friction.md` §RULED): "the ConfigMap is not the correct source of truth for the
operational details of DAV — that should come from the DB. Same goes for the MCP server." This
note settles the target shape and the deprecation order; build follows review, not this note._

## Current state (what makes it wrong)

Write path: repo registry (`managed_repos`, DB) → manual `POST /api/repos/project` →
**two ConfigMaps** (`dav-source-corpus`, `dav-source-spec`) + a rollout-restart of
`dav-docs-mcp`. Read path: the sync Tekton tasks mount `/config/sources`; the MCP serves
whatever it was restarted with.

Three structural consequences, each observed as an incident:
- only ONE project's repos can be projected (`DAV_MCP_SOURCE_PROJECT_SLUG`) — the fixture repo
  had to be DB-moved into project 20 to be visible at all;
- the projection is a hidden mutable step — correct registry, stale ConfigMap, silent empty runs;
- repo edits require restart cycles the registry knows nothing about.

## Target shape

**1. Runs: resolve at trigger, pin by SHA.**
`trigger_run` already resolves per-run parameters and already queries the registry (it fetches
`known_capability_ids` from the DB at trigger). It additionally resolves the active project's
repos for the requested roles + namespaces and passes the resolved source list into the
PipelineRun as a JSON param:

```json
sources: [{"namespace":"fixtures","repo_url":"...","ref":"<sha-resolved-at-trigger>",
           "root_path":"fixtures/corpus","role":"corpus"}, ...]
```

The sync task consumes the param when present and falls back to `/config/sources` when absent
(transition safety). Resolving the ref to a SHA at trigger gives every run pinned, recorded
provenance — `corpus_repo_sha` stops being filled in after the fact.

**2. MCP: read the registry, hot-refresh.**
`dav-docs-mcp` gains a refresh loop that pulls its source list from the API
(`GET /api/repos?role=spec`, authenticated the same way the engine already authenticates to the
API: projected SA token + TokenReview — the pattern exists, nothing new to invent). Interval
refresh plus a `POST /refresh` for immediacy. No rollout-restart on repo edits.

**3. Retire the projection.**
`POST /api/repos/project` becomes a deprecation warning; `DAV_MCP_SOURCE_PROJECT_SLUG` is
removed. With per-run and per-refresh resolution there is no shared projected plane, so:
- **project isolation becomes real** — a project's repos are its runs' sources, full stop;
- per-project MCP serving (tenancy Phase 3) loses its main blocker;
- the "0 cloned, N skipped by filter" class of failure becomes impossible, because the
  namespaces a run can name are exactly the namespaces its project registered (which is also
  what makes trigger-time namespace validation exact instead of best-effort).

## Deprecation order (each step independently shippable and reversible)

1. Trigger-time resolution param + sync-task fallback consumption (no behavior change until the
   param is sent).
2. API sends the param; ConfigMap read path becomes dead code in practice.
3. MCP refresh loop; projection stops restarting the MCP.
4. Projection endpoint → 410 with pointer; env var removed; ConfigMaps deleted after one release
   as rollback insurance.

## Risks, named

- **Tekton param size**: source lists are tens of entries at most; JSON param is well under
  limits. Not a real risk, checked anyway.
- **MCP auth**: solved pattern (engine→API TokenReview). The MCP's SA joins the trusted set.
- **Trigger latency**: one `git ls-remote` per repo to pin SHAs. Cacheable; acceptable.
- **The fallback lingering forever**: step 4 has a date the moment step 2 ships; the fallback's
  removal is part of the epic's definition of done, because a dual read path is exactly how the
  vocabulary fork happened.

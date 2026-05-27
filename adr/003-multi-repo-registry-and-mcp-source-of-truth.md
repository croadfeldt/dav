# ADR-003 — Multi-Repo Registry and MCP Source-of-Truth

**Status:** Accepted
**Date:** 2026-05-27
**Author:** Chris Roadfeldt + Claude

---

## 1. Context

DAV currently sources spec docs from one or more git repos via the
`dav-source-spec` ConfigMap, parsed at MCP init-container start. With
the UDLM/DCM split (May 2026) the MCP now serves two repos
concurrently, and the `consumer_spec_sources` var declares them as a
list inside the ConfigMap.

Two pressures push us past this design:

1. **Multi-tenancy.** A future multi-user DAV instance cannot share a
   single namespace-wide ConfigMap as authoritative repo state — there's
   no per-user RBAC at the field level, no way to scope repos per
   user/team, and operator changes affect all tenants at once.
2. **More repo roles.** PR-comment ingestion (#28 M5-M8) adds a third
   role (`issue-source`) beyond spec and corpus. A repo may carry
   multiple roles simultaneously (DCM is both spec source and corpus
   source today). Hardcoding per-role ConfigMaps doesn't scale.

The question this ADR settles: **what is the authoritative store for
"which repos does DAV know about, and for what purpose?"**

## 2. Decision

A new database table `managed_repos` is the **single source of truth**
for repos DAV operates on. Each row carries:

- `namespace` — URL-safe identifier used as the MCP doc-handle prefix
- `repo_url` + `repo_branch` — git clone target
- `root_path` — optional subdirectory served as the source root
- `roles[]` — open vocabulary; v1 is `{spec, corpus, issue-source}`
- `tenant_id` — defaults to `'default'`; ungated multi-tenant pathway
- `ingestion_config` — JSONB; per-role config (e.g., polling interval
  for issue-source)
- `metadata` + audit fields

The MCP server remains a **single deployment** for consistency and
security boundary clarity. It serves all sources across all tenants
via one in-cluster service. Per-tenant request filtering can be
layered on later (as a separate feature) when actual multi-tenancy
demands it — `tenant_id` in the table makes this additive, not a
breaking change.

The `dav-source-spec` ConfigMap continues to exist but is now
**projected** from the registry: review-console regenerates its
`sources` field whenever managed_repos rows with `role=spec` change.
The ConfigMap is the transport mechanism by which the MCP receives
its source list at pod start; the registry is the durable
source-of-truth that any UI, API, or operator interaction reads and
writes.

For first-run migration, on startup the review-console seeds the
registry from existing ConfigMap contents if the registry is empty.
This preserves operator configuration without manual reseeding.

## 3. Alternatives Considered

### A. Per-tenant MCP deployments

Review-console manages MCP pod lifecycle per tenant; each tenant gets
its own MCP pod, ConfigMap, and Route.

**Rejected because:** strong isolation isn't worth the N-pod overhead
and per-tenant DNS / pod lifecycle code. We don't have the workload
yet to justify the complexity, and the security gain over
request-scoped filtering on a single pod is modest given DAV's
threat model (internal users querying their own configured sources).

### B. MCP-as-API-client, no ConfigMaps

MCP queries review-console API for sources at startup; ConfigMaps
removed entirely.

**Rejected because:** couples MCP to the review-console API as a
hard dependency. MCP becomes unbootable if review-console is down.
The ConfigMap pattern is k8s-native, observable via `kubectl`, and
operationally well-understood — keeping it as the transport is
cheap. The registry is still the authoritative store; the ConfigMap
is downstream cache, not source-of-truth.

### C. Stay single-tenant, defer multi-tenant

Keep the current ConfigMap-only approach; add `tenant_id` later.

**Rejected because:** retrofitting tenant boundaries is more
expensive than designing for them. Adding the column now (with a
default value) costs almost nothing. The registry approach also
solves the immediate "more roles" problem regardless of tenancy.

## 4. Consequences

### Positive

- DB-backed registry is observable, RBAC-able, audit-trailed, and
  user-mutable through the API without `oc edit` round-trips.
- Single MCP keeps deployment topology stable; no pod-lifecycle code
  in the review-console.
- `tenant_id` from day one means multi-tenant filtering is additive
  later, not a migration.
- Roles vocabulary is extensible without schema changes.
- Adding/editing/deleting repos becomes a normal review-console UI
  flow; no operator edits to ConfigMaps required.

### Negative

- One write path now has two side effects: DB row + ConfigMap
  regeneration + (eventually) MCP rollout-restart. Failure modes are
  more interleaved than a single ConfigMap write was.
- The seed-from-existing-ConfigMap path adds a one-time complexity
  bump at the v1 deploy.
- The dav-source-spec ConfigMap shape (now projection output) needs
  to be treated as a derived artifact, not edited directly. Direct
  ConfigMap edits get clobbered on the next projection run.

### Operator transition

For operators with an existing single-source or multi-source
`dav-source-spec` ConfigMap:

1. Deploy the new review-console; first startup seeds the
   `managed_repos` table from the existing ConfigMap.
2. From that point forward, edit repos via the Repos UI or
   `POST /api/repos`. Direct ConfigMap edits get reverted on the
   next change.
3. To rollback (back to ConfigMap-only): drop the `managed_repos`
   table; restore manual ConfigMap management. (No code path forces
   the registry; the seed is one-way but reversible by table drop +
   stop using new API.)

## 5. Implementation status

- Migration 007 creates `managed_repos`.
- `review-console/api/app/repos.py` provides CRUD + seed helpers.
- `POST /api/repos`, `GET /api/repos`, `PUT /api/repos/{uuid|ns}`,
  `DELETE /api/repos/{uuid|ns}` expose CRUD to the UI.
- M2 wires the projection (registry → ConfigMap regeneration).
- M3-M4 add the Repos UI; Sources panel becomes a filtered view.
- M5-M8 extend `managed_repos.roles` with `issue-source` and build
  the PR-comment inbox on top.

## 6. Related

- ADR-001 — Consumer-agnostic framework (this is consistent with that
  decision: repos belong to consumer realms, not framework code)
- `docs/review-console-design.md` — Operational design doc updated to
  document the registry
- `specs/09-deployment-standards.md` — Deployment standards updated
  with the projection contract

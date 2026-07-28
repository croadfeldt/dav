## 🔬 DESIGN 2026-07-27 — run-source resolution from the registry (Chris-RULED epic)
**Why:** ruling on the friction inventory: the DB is the source of truth; ConfigMaps + the MCP's
ConfigMap feed are projections to retire. Design: **`docs/run-source-resolution-design.md`** —
trigger-time source resolution with SHA pinning (rides the PipelineRun as a JSON param;
sync-task fallback during transition), MCP hot-refresh from the API (existing TokenReview
pattern), 4-step deprecation ending with the projection endpoint at 410 and
`DAV_MCP_SOURCE_PROJECT_SLUG` removed. Makes project isolation real; unblocks tenancy Phase 3.
Build after review.


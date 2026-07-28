## F5 — Graphical UC ↔ capability map (bidirectional)
**Status: CONFIRMED (user agreed 2026-06-08); bidirectional.** Visualize use-cases ↔
capabilities both directions: pick a UC → its demanded capabilities; pick a capability
→ the UCs that demand it. Doubles as a **F4 consulting deliverable / "second
projection"** (gap + roadmap made legible at a glance).
- **Data already exists** (mostly a viz task): `uc_capabilities` (bipartite UC↔capability
  edges, "UC demands capability X"; schema.sql:234) and `uc_capability_deps`
  (capability→capability deps; schema.sql:256). Endpoints:
  `/api/analysis/capability-density` (main.py:6206, demand per capability) and
  `/api/analysis/foundational-capabilities` (main.py:6275, dependency ranking +
  leverage). Analysis libs: `capability_density.py`, `capability_graph.py`.
- **TODO:** likely one new endpoint returning the bipartite edge list (uc_uuid ↔
  capability_id) for a run/set, then a UI graph/matrix. Options: force-directed
  bipartite graph, or a UC×capability matrix/heatmap (demand count = cell weight),
  with click-through both ways. Size capability nodes by demand density; flag
  foundational ones (high leverage). Scope to a run/set (data is per-run).
- **Open Qs:** graph vs matrix as primary view? scope to current Set or cross-run
  aggregate? include capability→capability dep edges in the same view or a layer toggle?


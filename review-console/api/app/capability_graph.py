"""Foundational capability detection (DCM feature #3).

Cross-UC capability demand density (#2) answers "what do the most UCs ask for?".
But Kevin's point was that some capabilities are *foundational* — not heavily
demanded on their own, yet a blocking dependency for many others — and those
should float to the top. This module is the graph analysis that surfaces them.

Input is the set of dependency edges the engine emits on `capabilities_invoked`
(`depends_on`), aggregated across a run/set. Edges point dependant → dependency
(A depends_on B = A requires B). For each capability we compute how many other
capabilities *transitively* depend on it: the higher that count, the more
foundational. Combined with #2's per-capability demand, `leverage` highlights the
"boring but foundational" case — high transitive dependents, low direct demand.

Pure, cycle-safe, dependency-free for unit testing (see test_capability_graph.py).
"""

from __future__ import annotations


def foundational_ranking(edges, demand=None) -> list[dict]:
    """Rank capabilities by how foundational they are.

    `edges`: iterable of (dependant, dependency) pairs — A requires B. Self-loops
    and empty ids are ignored. A dependency that's never itself a dependant still
    counts as a node (the pure-foundation case).

    `demand`: optional {capability_id: uc_count} from #2's demand density, used to
    compute leverage. None → leverage omitted.

    Returns one row per capability node, sorted most-foundational first
    (transitive dependents desc, then direct dependents, then id):
        {capability_id, depends_on: [...], direct_dependents, transitive_dependents,
         demand_uc_count: int|None, leverage: float|None}
    where leverage = transitive_dependents / max(demand_uc_count, 1) — a high value
    means many capabilities rest on it but few UCs ask for it directly.
    """
    demand = demand or {}
    nodes: set = set()
    deps_of: dict[str, set] = {}        # dependant -> {dependency}  (forward / depends_on)
    dependants_of: dict[str, set] = {}  # dependency -> {dependant}  (reverse)
    for a, b in edges:
        if not a or not b or a == b:
            continue
        nodes.add(a)
        nodes.add(b)
        deps_of.setdefault(a, set()).add(b)
        dependants_of.setdefault(b, set()).add(a)

    def transitive_dependents(x: str) -> int:
        # Count every node that can reach x through depends_on edges (x's ancestors).
        # Iterative DFS over the reverse graph; `seen` makes it cycle-safe.
        seen: set = set()
        stack = list(dependants_of.get(x, ()))
        while stack:
            n = stack.pop()
            if n == x or n in seen:
                continue
            seen.add(n)
            stack.extend(dependants_of.get(n, ()))
        return len(seen)

    out = []
    for x in nodes:
        td = transitive_dependents(x)
        dd = len(dependants_of.get(x, ()))
        d = demand.get(x)
        out.append({
            "capability_id": x,
            "depends_on": sorted(deps_of.get(x, ())),
            "direct_dependents": dd,
            "transitive_dependents": td,
            "demand_uc_count": d,
            "leverage": round(td / max(d, 1), 2) if d is not None else None,
        })
    out.sort(key=lambda r: (-r["transitive_dependents"], -r["direct_dependents"], r["capability_id"]))
    return out

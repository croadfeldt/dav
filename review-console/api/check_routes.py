#!/usr/bin/env python3
"""Static guard against FastAPI route-shadowing bugs.

FastAPI matches routes in *definition order*. A static path declared AFTER a
same-shape parameterized sibling (e.g. `/api/results/uc-latest` after
`/api/results/{run_id}`) is unreachable — the param route swallows it and the
handler returns a misleading 404 ("run 'uc-latest' not found").

This scans app/main.py's @app.<verb>("/path") decorators and fails if any
static route is shadowed by an earlier parameterized sibling of the same method
and segment count. Run it pre-deploy (exit 1 on any shadow).

Two real bugs this would have caught: /api/results/uc-latest and
/api/runs/preflight-hint (both 2026-06-11).
"""
import re
import sys
from pathlib import Path

MAIN = Path(__file__).with_name("app") / "main.py"


def routes():
    out = []
    for i, line in enumerate(MAIN.read_text().splitlines(), 1):
        m = re.search(r'@app\.(get|post|put|delete|patch)\("([^"]+)"', line)
        if m:
            out.append((i, m.group(1), m.group(2)))
    return out


def segs(p):
    return p.strip("/").split("/")


def find_shadows(rs):
    shadows = []
    for ln, meth, path in rs:
        if "{" in path:
            continue  # only static paths can be shadowed
        for ln2, meth2, path2 in rs:
            if ln2 >= ln or meth2 != meth:
                continue
            s, s2 = segs(path), segs(path2)
            if len(s) != len(s2):
                continue
            same_shape = all(b.startswith("{") or a == b for a, b in zip(s, s2))
            if same_shape and any(b.startswith("{") for b in s2):
                shadows.append((ln, meth.upper(), path, ln2, path2))
    return shadows


def main():
    rs = routes()
    shadows = find_shadows(rs)
    if shadows:
        print(f"ROUTE-SHADOW CHECK: {len(shadows)} shadowed route(s) — these are UNREACHABLE:")
        for ln, meth, path, ln2, path2 in shadows:
            print(f"  L{ln} {meth} {path}  <-- shadowed by  L{ln2} {path2}  (declare the static path FIRST)")
        return 1
    print(f"ROUTE-SHADOW CHECK: OK ({len(rs)} routes, no shadowing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

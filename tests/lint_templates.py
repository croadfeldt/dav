#!/usr/bin/env python3
"""Jinja template syntax gate. yamllint can't parse *.j2 (they are Jinja, not YAML),
so instead we parse every template with Jinja to catch syntax breakage before deploy.
Exit non-zero if any template fails to parse. Wire into CI."""
import pathlib
import sys

try:
    from jinja2 import Environment
except ImportError:
    sys.exit("requires: pip install jinja2")

ROOT = pathlib.Path(__file__).resolve().parent.parent
env = Environment()
bad = 0
count = 0
for p in sorted(ROOT.rglob("*.j2")):
    if ".git" in p.parts:
        continue
    count += 1
    try:
        env.parse(p.read_text())
    except Exception as e:  # jinja2.TemplateSyntaxError et al.
        print(f"FAIL {p.relative_to(ROOT)}: {e}")
        bad += 1

print(f"{count} template(s) parsed, {bad} with syntax errors")
sys.exit(1 if bad else 0)

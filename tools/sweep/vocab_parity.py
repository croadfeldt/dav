#!/usr/bin/env python3
"""Q11 cross-repo vocabulary parity (repo-cleanliness-review.md): a rename ratified in the
owning repo must not survive in sibling repos.

The retired-term list lives HERE (one home) — extend it in the same PR as any future rename.
Each term carries the replacement and the rename's home so a hit explains itself. Terms are
matched with word-boundary or field-syntax precision to avoid false positives (e.g. the edge
field `kind:` is matched only with edge values; provenance `source.kind` is legitimate).

Usage: vocab_parity.py <repo-root> [<repo-root> ...]   # exit 1 on any hit
"""
import os
import re
import subprocess
import sys

# (pattern, replacement-hint, rename home)
RETIRED = [
    (re.compile(r"^\s*(?:-\s*)?kinds?\s*:\s*[\[<]?\s*(depends_on|contained_by|binds_to|references)", re.M),
     "edge field `kind` -> `edge_type`", "udlm ADR-026"),
    (re.compile(r"\bdependency_type\b"), "-> `strength` (hard|soft); conditional is a construction predicate", "udlm service-dependencies §4"),
    (re.compile(r"\brelationship_types?\b|\brelationship_nature\b"), "-> `edge_type` + derived nature", "udlm data-model-core §4"),
    (re.compile(r"^\s*entity_type\s*:\s*<?\s*(Atomic|Composite)\b", re.M), "shape is derived (`has_constituents`), not stored", "udlm ADR-027 addendum"),
    (re.compile(r"\bprovider_extensions\b"), "removed — Provider-Class SharedDataElements", "udlm ADR-038 / #202 executed"),
    (re.compile(r"\baudit\.chain_(integrity_alert|break)\b"), "-> audit.integrity_alert / audit.integrity_break", "udlm event-catalog (Merkle rename)"),
    (re.compile(r"^\s*profile\s*:\s*minimal\b|\bminimal\s+profile\b", re.M | re.I), "-> homelab (six-profile ladder)", "udlm docs/profiles.md"),
    (re.compile(r"\bsingle_gatekeeper\b|\bsingle_gating\b"), "-> single_validation", "dav engine consumer_profile"),
    (re.compile(r"\bai-assisted\b"), "-> llm-guided", "dav engine generated_by vocabulary"),
    (re.compile(r"\bcompound_service\b"), "-> composite_service", "dav engine resource_complexity"),
    (re.compile(r"\bmeta_provider_composed\b"), "retired from provider_landscape", "dav engine vocabulary"),
]
EXCLUDE_DIRS = {".git", "docs/adr", "docs/archive", "docs/internal", "node_modules", ".udlm-ci"}
EXCLUDE_BASENAMES = {"vocab_parity.py", "check_model_vocabulary.py", "check_terminology.py"}
SUFFIX = (".md", ".yaml", ".yml", ".json", ".py", ".go")


def tracked(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True,
                             check=True).stdout.splitlines()
    except Exception:
        out = []
    for rel in out:
        if any(rel.startswith(d + "/") or rel == d for d in EXCLUDE_DIRS):
            continue
        if os.path.basename(rel) in EXCLUDE_BASENAMES or not rel.endswith(SUFFIX):
            continue
        yield rel


def main():
    roots = sys.argv[1:] or ["."]
    hits = 0
    for root in roots:
        for rel in tracked(root):
            try:
                text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            # lines that explain a retirement are allowed to name the old term
            for rx, hint, home in RETIRED:
                for m in rx.finditer(text):
                    line_no = text.count("\n", 0, m.start()) + 1
                    line = text.splitlines()[line_no - 1]
                    if re.search(r"retired|removed|renamed|superseded|deprecated|was |formerly|->", line, re.I):
                        continue
                    print(f"FAIL {root}/{rel}:{line_no}  retired term ({home}): {hint}\n      > {line.strip()[:110]}")
                    hits += 1
    print(f"\n{hits} parity hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())

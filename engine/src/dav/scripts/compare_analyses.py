#!/usr/bin/env python3
"""compare_analyses: CLI for semantic comparison of Stage 2 Analysis YAMLs.

Usage:
  compare_analyses.py <analysis_a.yaml> <analysis_b.yaml>
  compare_analyses.py --quiet <analysis_a.yaml> <analysis_b.yaml>
  compare_analyses.py --json <analysis_a.yaml> <analysis_b.yaml>

Exit codes:
  0 — equivalent (no meaningful architectural change)
  1 — changed (reviewer should examine)
  2 — usage or parse error

The --quiet flag suppresses per-finding output when the verdict is
equivalent; useful in CI to avoid spam when most runs pass.

The --json flag emits a structured summary instead of the human-readable
diff. Shape:
  {
    "verdict": "equivalent" | "changed",
    "max_severity": "trivial" | "minor" | "major" | "critical" | "",
    "finding_count": int,
    "findings": [{"severity": "...", "field": "...", "description": "..."}, ...]
  }

Example usage:
  # Ad-hoc: did a prompt change affect analysis?
  compare_analyses.py analysis-prompt-v1.2.yaml analysis-prompt-v1.3.yaml

  # CI gate: fail if the post-PR analysis differs meaningfully from baseline
  compare_analyses.py --quiet baseline.yaml pr-run.yaml
  if [ $? -ne 0 ]; then echo "FAIL: spec change altered analysis"; fi
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "ERROR: PyYAML not installed. Run: pip install PyYAML\n"
    )
    sys.exit(2)

# Import the comparator module. Support both "installed alongside
# engine" layout and "run from scripts/ with engine/ on path" layout.
_HERE = Path(__file__).resolve().parent
_CANDIDATES = [
    _HERE.parent,              # scripts/ → engine/ parent
    _HERE.parent / "engine",   # repo-root/scripts → repo-root/engine
]
for candidate in _CANDIDATES:
    if (candidate / "evaluator" / "compare.py").exists():
        sys.path.insert(0, str(candidate))
        break

try:
    from evaluator.compare import compare, CompareResult
except ImportError as e:
    sys.stderr.write(
        f"ERROR: could not import evaluator.compare: {e}\n"
        f"Looked in: {[str(c) for c in _CANDIDATES]}\n"
    )
    sys.exit(2)

def load_analysis(path: Path) -> dict:
    """Load a YAML file, return the top-level dict.

    The file may be either:
      - A raw Analysis YAML (what Stage 2 writes to /tmp/analysis.yaml)
      - A log file with a `=== ANALYSIS ===` marker followed by the YAML,
        optionally followed by trailing `oc run` noise like
        `pod "stage2-..." deleted`.

    The second case is common for ad-hoc comparison of files captured
    via `tee` during manual runs. Auto-detect and strip both leading
    log chatter and trailing pod-deletion noise.
    """
    text = path.read_text()
    marker = "=== ANALYSIS ==="
    if marker in text:
        text = text.split(marker, 1)[1]

    # Strip trailing non-YAML noise. `oc run` emits `pod "..." deleted`
    # after the analysis content. Cut at the first line that starts
    # with `pod "` — nothing in a legitimate Analysis YAML starts with
    # that prefix.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith('pod "') and "deleted" in line:
            lines = lines[:i]
            break
    text = "\n".join(lines)

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        sys.stderr.write(f"ERROR: failed to parse {path}: {e}\n")
        sys.exit(2)
    if not isinstance(data, dict):
        sys.stderr.write(
            f"ERROR: {path} did not parse to a dict (got {type(data).__name__})\n"
        )
        sys.exit(2)
    return data

def render_json(result: CompareResult) -> str:
    return json.dumps({
        "verdict": result.verdict,
        "max_severity": result.max_severity,
        "finding_count": len(result.findings),
        "use_case_uuid_a": result.use_case_uuid_a,
        "use_case_uuid_b": result.use_case_uuid_b,
        "findings": [
            {"severity": f.severity, "field": f.field, "description": f.description}
            for f in result.findings
        ],
    }, indent=2)

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Semantically compare two Stage 2 Analysis YAMLs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("analysis_a", type=Path, help="First analysis YAML")
    parser.add_argument("analysis_b", type=Path, help="Second analysis YAML")
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress finding output when result is equivalent",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit JSON output instead of human-readable diff",
    )
    args = parser.parse_args()

    for p in (args.analysis_a, args.analysis_b):
        if not p.exists():
            sys.stderr.write(f"ERROR: file not found: {p}\n")
            return 2

    a = load_analysis(args.analysis_a)
    b = load_analysis(args.analysis_b)
    result = compare(a, b)

    if args.as_json:
        print(render_json(result))
    elif args.quiet and result.is_equivalent:
        # Silent pass for CI — exit code carries the signal
        pass
    else:
        print(result.render())

    return 0 if result.is_equivalent else 1

if __name__ == "__main__":
    sys.exit(main())

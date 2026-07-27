#!/usr/bin/env python3
"""Pre-flight the fixture corpus before spending GPU time on it.

Written because 7 of 8 fixture UCs were SILENTLY dropped on their first run: the
engine logged "corpus: 8 files" and then "running 1 UC(s)" with no warning naming
the rejected files or the reason. The cause was invented dimension values —
`single_resource` where the profile says `single_no_deps`, `none` where it says
`happy_path`.

That failure is invisible in exactly the wrong way: a scorer would have reported
1.00 recall on the single UC that survived, and the suite would have looked
healthy while measuring one eighth of itself.

Checks:
  * every UC parses as a UseCase
  * every dimension value and profile is in the consumer profile's vocabulary
  * every UC has a matching expected/ file, and vice versa
  * every expected capability_id is unique to one role (a gap cannot also be a
    control)

Exit non-zero on any problem, so it can gate a run.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "engine" / "src"))

try:
    from dav.core.consumer_profile import _GENERIC_REFERENCE_PROFILE as PROFILE
    from dav.core.use_case_schema import UseCase
except ImportError as e:  # engine not importable from this checkout
    sys.exit(f"cannot import engine ({e}); run from a checkout with engine/src present")

VOCAB = {
    "lifecycle_phase": PROFILE.lifecycle_phases,
    "resource_complexity": PROFILE.resource_complexities,
    "policy_complexity": PROFILE.policy_complexities,
    "provider_landscape": PROFILE.provider_landscapes,
    "governance_context": PROFILE.governance_contexts,
    "failure_mode": PROFILE.failure_modes,
}


def main() -> int:
    problems: list[str] = []
    handles: set[str] = set()

    uc_files = sorted((HERE / "corpus").rglob("*.yaml"))
    if not uc_files:
        return _fail(["no UC files under corpus/"])

    for f in uc_files:
        rel = f.relative_to(HERE)
        try:
            raw = yaml.safe_load(f.read_text())
            uc = UseCase.from_dict(raw)
        except Exception as e:
            problems.append(f"{rel}: does not parse — {str(e)[:120]}")
            continue
        handles.add(uc.handle)

        dims = raw["scenario"]["dimensions"]
        for key, allowed in VOCAB.items():
            val = dims.get(key)
            if val is not None and val not in allowed:
                problems.append(
                    f"{rel}: {key}={val!r} is not in the profile vocabulary "
                    f"(this is what silently quarantines a UC). Valid: {sorted(allowed)[:6]}…")
        prof = raw["scenario"].get("profile")
        if prof not in PROFILE.profiles:
            problems.append(f"{rel}: profile={prof!r} not in {PROFILE.profiles}")

    exp_files = sorted((HERE / "expected").glob("*.yaml"))
    expected_handles, seen_ids = set(), {}
    for f in exp_files:
        d = yaml.safe_load(f.read_text())
        expected_handles.add(d["uc"])
        for role, key in (("expected_gaps", "gap"), ("must_not_report", "control")):
            for item in (d.get(role) or []):
                cid = item["capability_id"]
                prior = seen_ids.get(cid)
                # A control in one UC and a seeded gap in another is a contradiction:
                # the scorer would count the same id as both a hit and a false positive.
                if prior and prior != key:
                    problems.append(
                        f"{cid} is a {prior} in one UC and a {key} in another — "
                        f"ground truth contradicts itself")
                seen_ids[cid] = key

    for h in sorted(handles - expected_handles):
        problems.append(f"UC {h} has no expected/ file — it would be scored as absent")
    for h in sorted(expected_handles - handles):
        problems.append(f"expected/ names {h} but no such UC exists")

    if problems:
        return _fail(problems)
    print(f"  OK — {len(uc_files)} UCs, {len(exp_files)} expectations, "
          f"{sum(1 for v in seen_ids.values() if v=='gap')} seeded gaps, "
          f"{sum(1 for v in seen_ids.values() if v=='control')} controls")
    return 0


def _fail(problems: list[str]) -> int:
    print(f"  {len(problems)} problem(s):")
    for p in problems:
        print(f"    - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

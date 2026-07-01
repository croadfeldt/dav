#!/usr/bin/env python3
"""Export the engine's generic reference profile vocabulary to a JSON artifact
the review-console API reads at runtime (operating-model DR §6, enum
single-source).

The API container does not import the engine, so the dimension/profile enums it
uses to validate UC YAML at save time used to be a hand-copied duplicate of the
engine's `get_generic_reference_profile()`. That drifted (the
`single_gatekeeper`→`single_gating`→`single_validation` rename had to be applied in two places).

This generator makes the engine the single source of truth: it writes
`review-console/api/app/dcm_vocab.json` from the reference profile. The API loads
that JSON; CI can run this with `--check` to fail the build if the committed JSON
has drifted from the engine.

Usage:
    python -m dav.scripts.export_dcm_vocab            # write the JSON
    python -m dav.scripts.export_dcm_vocab --check    # exit 1 if out of date
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dav.core.consumer_profile import get_generic_reference_profile

# scripts → dav → src → engine → repo_root
_REPO_ROOT = Path(__file__).resolve().parents[4]
_TARGET = _REPO_ROOT / "review-console" / "api" / "app" / "dcm_vocab.json"

# The enum fields the API mirrors for save-time UC validation. Keys are the
# JSON keys the API reads; values are the ConsumerProfile attribute names.
_FIELDS = {
    "lifecycle_phases": "lifecycle_phases",
    "resource_complexities": "resource_complexities",
    "policy_complexities": "policy_complexities",
    "provider_landscapes": "provider_landscapes",
    "governance_contexts": "governance_contexts",
    "failure_modes": "failure_modes",
    "profiles": "profiles",
}


def build_vocab() -> dict:
    profile = get_generic_reference_profile()
    vocab = {
        "_generated_by": "dav.scripts.export_dcm_vocab",
        "_source": "engine get_generic_reference_profile()",
        "consumer_id": profile.consumer_id,
        "schema_version": profile.schema_version,
    }
    for json_key, attr in _FIELDS.items():
        vocab[json_key] = list(getattr(profile, attr))
    return vocab


def _serialize(vocab: dict) -> str:
    return json.dumps(vocab, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="exit non-zero if the committed JSON differs from the engine "
             "(do not write); for CI drift detection",
    )
    args = parser.parse_args(argv)

    payload = _serialize(build_vocab())

    if args.check:
        if not _TARGET.exists():
            print(f"DRIFT: {_TARGET} does not exist; run without --check",
                  file=sys.stderr)
            return 1
        current = _TARGET.read_text()
        if current != payload:
            print(f"DRIFT: {_TARGET} is out of date with the engine reference "
                  f"profile; run `python -m dav.scripts.export_dcm_vocab`",
                  file=sys.stderr)
            return 1
        print(f"OK: {_TARGET} matches the engine reference profile")
        return 0

    _TARGET.parent.mkdir(parents=True, exist_ok=True)
    _TARGET.write_text(payload)
    print(f"wrote {_TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

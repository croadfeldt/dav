"""Export the DCM reference dimension vocabulary to a JSON artifact the review-console API consumes.

Single-source-of-truth bridge (operating-model DR §6): the engine's reference ConsumerProfile is the
ONE authoritative definition of the DCM dimension vocab. The API runs in a separate build context that
cannot import the engine, so instead of hand-duplicating the lists in `review-console/api/app/main.py`
(which drifted — the `single_gatekeeper`/`single_gating` incident), the API loads this GENERATED file.

Usage:
    python -m dav.scripts.export_dcm_vocab            # (re)write the committed artifact
    python -m dav.scripts.export_dcm_vocab --check    # exit 1 if the committed file is stale (CI/pre-commit)

The committed artifact is `review-console/api/app/dcm_vocab.json`. Regenerate it whenever the engine
reference profile's dimension lists change, and the `--check` mode catches drift.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dav.core.consumer_profile import get_generic_reference_profile

# repo root = .../engine/src/dav/scripts/export_dcm_vocab.py -> parents[4]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_TARGET = _REPO_ROOT / "review-console" / "api" / "app" / "dcm_vocab.json"

# The vocab keys the API validator needs (mirror ConsumerProfile dimension fields + profiles).
_FIELDS = (
    "lifecycle_phases",
    "resource_complexities",
    "policy_complexities",
    "provider_landscapes",
    "governance_contexts",
    "failure_modes",
    "profiles",
)


def build_vocab() -> dict:
    """Return the DCM vocab dict from the engine's authoritative reference profile."""
    p = get_generic_reference_profile()
    vocab = {f: list(getattr(p, f)) for f in _FIELDS}
    vocab["_generated_by"] = "dav.scripts.export_dcm_vocab"
    vocab["_source"] = "engine consumer_profile.get_generic_reference_profile()"
    vocab["_consumer_id"] = p.consumer_id
    return vocab


def _serialize(vocab: dict) -> str:
    return json.dumps(vocab, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    vocab = build_vocab()
    text = _serialize(vocab)
    check = "--check" in argv
    if check:
        if not _TARGET.exists():
            print(f"DRIFT: {_TARGET} does not exist; run export_dcm_vocab to create it", file=sys.stderr)
            return 1
        current = _TARGET.read_text()
        if current != text:
            print(f"DRIFT: {_TARGET} is stale vs the engine reference profile; "
                  f"run `python -m dav.scripts.export_dcm_vocab` to regenerate", file=sys.stderr)
            return 1
        print(f"OK: {_TARGET} matches the engine reference profile")
        return 0
    _TARGET.parent.mkdir(parents=True, exist_ok=True)
    _TARGET.write_text(text)
    print(f"wrote {_TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

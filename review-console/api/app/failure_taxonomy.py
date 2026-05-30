"""Failure taxonomy — classify a DAV run's failures into structured signatures.

Phase 0 of the self-improvement loop (docs/dav-self-improvement-vision.md):
turn grep-derived failure patterns into a queryable, typed taxonomy that the
diagnoser (diagnose.py) consumes. Operates on the *durable* workspace
artifacts — `run-summary.yaml` + `failures/<uuid>.error.txt` — not the
ephemeral Tekton pod logs, so it works on any run whose results dir survives.

The SIGNATURE_PATTERNS table encodes the OSAC 2026-05-29/30 failure chain
(docs/experiments/2026-05-29-osac-stabilization-and-r9700-model-eval.md): each
of those bugs was root-caused by hand; these regexes let the system re-detect
the same fingerprints automatically.

Pure module: `build_taxonomy()` takes a summary dict + failure tuples and
returns a plain dict — no DB, no LLM, no filesystem. The thin workspace loader
lives in the API layer.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Signature catalogue
#
# Each entry: (signature_class, default_severity, compiled_regex, human_label).
# Matched against the failure's error message, FIRST MATCH WINS — so order
# specific patterns before generic ones. An optional capture group 1 is
# recorded as `captured` evidence (e.g. the rejected vocabulary label).
# ---------------------------------------------------------------------------

SIGNATURE_PATTERNS: list[tuple[str, str, re.Pattern, str]] = [
    (
        "route_504", "high",
        re.compile(r"primary returned 504|504 Gateway Time-?out", re.I),
        "Inference gateway timeout — route timeout shorter than generation time",
    ),
    (
        "output_truncation", "high",
        re.compile(
            r"could not parse final analysis as JSON|unbalanced braces"
            r"|Expecting value|Extra data|Unterminated string",
            re.I,
        ),
        "Final-analysis JSON truncated or malformed — max_tokens too small or a parser bug",
    ),
    (
        "severity_reject", "high",
        re.compile(r"invalid severity label '([^']+)'", re.I),
        "Schema rejected a severity label the model emitted",
    ),
    (
        "confidence_reject", "high",
        re.compile(r"invalid confidence label '([^']+)'", re.I),
        "Schema rejected a confidence label the model emitted",
    ),
    (
        "score_out_of_band", "medium",
        re.compile(r"score \d+ (?:outside|out of) band", re.I),
        "A label's score fell outside its canonical band",
    ),
    (
        "context_overflow", "medium",
        re.compile(
            r"maximum context length|context.*overflow|reduce the length"
            r"|exceeds the model's max",
            re.I,
        ),
        "Prompt + requested output exceeded the model context window",
    ),
    (
        "budget_exhausted", "medium",
        re.compile(
            r"max[_-]?tool[_-]?calls|tool[- ]call budget|exhausted.*budget"
            r"|no final analysis emitted|never emitted a final",
            re.I,
        ),
        "Agent burned its tool-call budget without emitting a final analysis",
    ),
    (
        "tool_parse_error", "high",
        re.compile(
            r"tool call.*could not be parsed|invalid tool call"
            r"|json\.decoder|tool_call.*decode",
            re.I,
        ),
        "Model tool call could not be parsed (parser/model mismatch)",
    ),
    (
        "inference_error", "medium",
        re.compile(
            r"inference failed|InferenceError|connection (?:error|refused|reset)"
            r"|read timed out|status 50[0-3]",
            re.I,
        ),
        "Inference endpoint error (connection / non-504 5xx)",
    ),
]

UNKNOWN = ("unknown", "Unclassified failure — needs human or LLM diagnosis", "medium")

# Canonical vocabularies (mirrors engine `use_case_schema`) — carried here so
# the diagnoser can suggest the nearest canonical label for a reject without a
# round-trip into the engine. Ordered low→high.
SEVERITY_VOCAB = ["advisory", "minor", "moderate", "major", "critical"]
CONFIDENCE_VOCAB = ["low", "medium", "high"]


def classify_error(message: str) -> dict:
    """Classify one failure's error message into a typed signature.

    Returns {signature_class, label, severity, captured}. `captured` is the
    regex group-1 text when present (e.g. the offending vocabulary label).
    """
    text = message or ""
    for cls, severity, rx, label in SIGNATURE_PATTERNS:
        m = rx.search(text)
        if m:
            captured = m.group(1) if m.groups() else None
            return {
                "signature_class": cls,
                "label": label,
                "severity": severity,
                "captured": captured,
            }
    cls, label, severity = UNKNOWN
    return {"signature_class": cls, "label": label, "severity": severity, "captured": None}


def _error_excerpt(text: str, limit: int = 240) -> str:
    """The salient line of an error.txt — the `Error:`-block tail, trimmed."""
    if not text:
        return ""
    if "Error:" in text:
        text = text.split("Error:", 1)[1]
    # Drop any HTML (504 pages) and collapse whitespace.
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text[:limit]


def build_taxonomy(summary: Optional[dict], failures: list[dict]) -> dict:
    """Aggregate a run's failures into a structured taxonomy.

    Args:
        summary: the run-summary.yaml dict (for counts + config), or None.
        failures: list of {uc_uuid, uc_handle, error_text} for failed UCs.

    Returns a plain dict (JSON/jsonb-friendly) with run counts, the run config
    pulled from `effective_sampling`, and a per-class signature histogram with
    counts, captured values, and up to 3 exemplars each.
    """
    summary = summary or {}
    eff = (summary.get("effective_sampling") or {})
    sent = (eff.get("sent") or {})
    config = {
        "model": eff.get("model"),
        "endpoint_url": eff.get("endpoint_url"),
        "use_key": eff.get("use_key"),
        "mode": summary.get("mode"),
        "max_tokens": sent.get("max_tokens"),
        "temperature": sent.get("temperature"),
        "top_k": sent.get("top_k"),
        "top_p": sent.get("top_p"),
        "min_p": sent.get("min_p"),
    }

    # Aggregate by signature class.
    by_class: dict[str, dict] = {}
    for f in failures:
        c = classify_error(f.get("error_text", ""))
        cls = c["signature_class"]
        slot = by_class.setdefault(cls, {
            "signature_class": cls,
            "label": c["label"],
            "severity": c["severity"],
            "count": 0,
            "captured": [],          # distinct captured values (e.g. ['low','high'])
            "exemplars": [],         # up to 3
        })
        slot["count"] += 1
        if c["captured"] and c["captured"] not in slot["captured"]:
            slot["captured"].append(c["captured"])
        if len(slot["exemplars"]) < 3:
            slot["exemplars"].append({
                "uc_uuid": f.get("uc_uuid"),
                "uc_handle": f.get("uc_handle"),
                "excerpt": _error_excerpt(f.get("error_text", "")),
            })

    signatures = sorted(
        by_class.values(),
        key=lambda s: (-s["count"], {"high": 0, "medium": 1, "low": 2}.get(s["severity"], 3)),
    )

    total = summary.get("total_ucs")
    succeeded = summary.get("successful")
    failed = summary.get("failed")
    if failed is None:
        failed = len(failures)

    return {
        "run_id": summary.get("run_id"),
        "mode": summary.get("mode"),
        "total_ucs": total,
        "succeeded": succeeded,
        "failed": failed,
        "config": config,
        "signatures": signatures,
        "failure_count": len(failures),
        # A run can fail with zero `failures/` files if the pipeline died before
        # writing any (rare); callers should treat an empty signature list +
        # non-zero `failed` as "needs the Tekton log", a follow-up enrichment.
    }

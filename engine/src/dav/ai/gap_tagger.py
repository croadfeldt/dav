"""Post-analysis gap tagging — classify untagged gaps onto the catalog.

Why this exists (experiment E1, 2026-07-28): the analyzer finds gap SUBSTANCE
far more reliably than it tags it — the fixture battery measured id-recall 0.50
with the substance of missed ids present as untagged findings, and every
untagged finding also costs precision (battery: 0.154 vs the frontier
reference's 1.000). Generation and classification are different tasks:
asking "which of these 14 catalog entries is this finding?" is far easier than
asking the model to produce the right id while composing an analysis. So we
ask the easy question separately, once per untagged gap:

  - one small LLM call per untagged gap (no tools, temperature 0);
  - the answer is grammatically constrained to the catalog + "none"
    (response_format/json_schema — the wire shape ADR-007 verified);
  - "none" leaves the gap untagged — an unmapped gap is a taxonomy-gap
    candidate by design, and forcing a tag would manufacture identity.

Runs BEFORE the ensemble merge on purpose: merge keys gaps by capability_id
when present, so consistent tags across samples are what make cross-sample
dedup and quorum counting work.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

# The classification is one honest sentence of a question; the budget only
# needs to cover the enum answer (plus reasoning headroom the client adds
# for reasoning models).
_MAX_TOKENS = 512

NONE_SENTINEL = "none"


def _classify_schema(catalog_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "capability_id": {"type": "string",
                              "enum": [*catalog_ids, NONE_SENTINEL]},
        },
        "required": ["capability_id"],
        "additionalProperties": False,
    }


def _classify_prompt(gap, catalog: dict[str, str]) -> str:
    lines = [f"- {cid}: {name}" if name else f"- {cid}"
             for cid, name in catalog.items()]
    rationale = (getattr(gap, "rationale", "") or "")[:600]
    return (
        "You are classifying one architecture-gap finding onto a capability "
        "catalog.\n\nCatalog:\n" + "\n".join(lines) +
        "\n\nFinding:\n"
        f"  title: {gap.title}\n"
        f"  description: {(gap.description or '')[:600]}\n"
        + (f"  rationale: {rationale}\n" if rationale else "") +
        f"\nAnswer with the single catalog id this finding IS an instance of, "
        f"or \"{NONE_SENTINEL}\" if no catalog entry matches. Do not stretch a "
        "partial thematic overlap into a match — a wrong tag is worse than no "
        "tag."
    )


def tag_untagged_gaps(analysis, catalog: dict[str, str], client,
                      *, seed: int | None = None) -> dict:
    """Classify each untagged gap in `analysis` onto `catalog` (id → name),
    in place. Returns a report dict: {"attempted": n, "tagged": n,
    "none": n, "failed": n, "tags": {title: id}}.

    Never raises — a tagging failure leaves the gap untagged, which is the
    pre-E1 behavior. The analysis a consumer reads must not be lost to a
    classification hiccup.
    """
    from dav.ai.client import ChatMessage

    report = {"attempted": 0, "tagged": 0, "none": 0, "failed": 0, "tags": {}}
    if not catalog:
        return report
    ids = sorted(catalog)
    schema = _classify_schema(ids)
    for gap in getattr(analysis, "gaps_identified", []) or []:
        if (gap.capability_id or "").strip():
            continue
        report["attempted"] += 1
        try:
            resp = client.chat(
                [ChatMessage(role="user", content=_classify_prompt(gap, catalog))],
                temperature=0.0,
                max_tokens=_MAX_TOKENS,
                guided_json_schema=schema,
                seed=seed,
            )
            cid = (json.loads(resp.content or "{}").get("capability_id") or "").strip()
        except Exception as e:
            log.warning("gap-tagger: classify failed for %r (%s); left untagged",
                        gap.title, e)
            report["failed"] += 1
            continue
        if cid and cid != NONE_SENTINEL and cid in catalog:
            gap.capability_id = cid
            report["tagged"] += 1
            report["tags"][gap.title] = cid
        else:
            report["none"] += 1
    if report["attempted"]:
        log.info("gap-tagger: %d untagged gap(s) → %d tagged, %d none, %d failed",
                 report["attempted"], report["tagged"], report["none"],
                 report["failed"])
    return report

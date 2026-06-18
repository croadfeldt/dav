"""Maturity Wall slice 2 — backend write-side: framework CRUD + scoring.

Slice 1 shipped the schema (migration 021) + the platform-maturity seed
(maturity_seed.py); slice 3 shipped the heat-mapped wall UI. This module is the
write-side the UI consumes:

  • Framework CRUD persistence — create a project-scoped framework (optionally
    cloned from a seed/template), edit its name/scale/status, and CRUD its
    categories / capabilities / states. Seed templates (project_id IS NULL) are
    read-only — projects copy + edit. All gated by `assessment.edit` at the API.
  • LLM scoring (`propose_scores`) — read the assessment findings, ask DAV's
    EXISTING model call path (a `call_fn(system, user) -> text`, the same one
    arch-review/assessment-ingest use) to propose 0..5 current scores + per-phase
    targets per framework capability, and persist them as `source='llm'`. Never
    clobbers a `source='human'` cell (curated scores are the truth — design §2).
  • Manual override (`apply_overrides`) — persist a human-set cell with provenance
    (`source='human'`, `updated_by`, `updated_at`), so a human score is always
    distinguishable from an LLM one and survives the next LLM pass.

Persistence helpers take an asyncpg connection so callers own pooling/transactions.
The LLM orchestration takes the resolved `call_fn` (built by main._make_diagnosis_call_fn
from a model_configs row) so this module stays free of HTTP/provider detail and is
unit-testable with a fake call_fn. See docs/maturity-wall-design.md.
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger("dav.maturity")

CallFn = Callable[[str, str], Awaitable[str]]


def _slug(s: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in (s or "").lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def _coerce_maturity(v):
    """0..5 integer or None ('-' Not Assessed). Anything out of range / unparseable → None."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() in ("", "-", "n/a", "na", "none", "null"):
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if 0 <= i <= 5 else None


def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    return s.strip()


# ── Framework CRUD (persistence) ──────────────────────────────────────────────
async def create_framework(conn, *, project_id: int, name: str, key: Optional[str] = None,
                           scale=None, status: str = "active", created_by: str = "",
                           clone_from: Optional[str] = None) -> dict:
    """Create a PROJECT-scoped framework. If `clone_from` (a framework id, e.g. a seed
    template) is given, deep-copy its scale + states + categories + capabilities so the
    project gets an editable copy of the platform-maturity model. Returns the new framework row.
    A framework key is unique per project; auto-derive from name when absent."""
    fkey = (key or _slug(name) or "framework").strip()
    src = None
    if clone_from:
        src = await conn.fetchrow(
            "SELECT id, scale FROM assessment_frameworks WHERE id=$1::uuid", clone_from)
        if src is None:
            raise ValueError("clone_from framework not found")
    eff_scale = scale if scale is not None else (src["scale"] if src else [])
    if not isinstance(eff_scale, str):
        eff_scale = json.dumps(eff_scale)
    fid = await conn.fetchval(
        """INSERT INTO assessment_frameworks (project_id, key, name, version, status, is_seed, scale, created_by)
           VALUES ($1, $2, $3, 1, $4, false, $5::jsonb, $6) RETURNING id""",
        project_id, fkey, name, status, eff_scale, created_by or None)
    if src is not None:
        # Copy states.
        for s in await conn.fetch(
                "SELECT key, label, ord, kind FROM framework_states WHERE framework_id=$1::uuid ORDER BY ord",
                clone_from):
            await conn.execute(
                """INSERT INTO framework_states (framework_id, key, label, ord, kind)
                   VALUES ($1,$2,$3,$4,$5) ON CONFLICT (framework_id, key) DO NOTHING""",
                fid, s["key"], s["label"], s["ord"], s["kind"])
        # Copy categories + their capabilities.
        for c in await conn.fetch(
                """SELECT id, key, label, band, ord, inflection_side
                   FROM framework_categories WHERE framework_id=$1::uuid ORDER BY ord""",
                clone_from):
            cid = await conn.fetchval(
                """INSERT INTO framework_categories (framework_id, key, label, band, ord, inflection_side)
                   VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
                fid, c["key"], c["label"], c["band"], c["ord"], c["inflection_side"])
            for cap in await conn.fetch(
                    """SELECT key, label, ord, catalog_capability_id
                       FROM framework_capabilities WHERE category_id=$1 ORDER BY ord""", c["id"]):
                await conn.execute(
                    """INSERT INTO framework_capabilities (category_id, key, label, ord, catalog_capability_id)
                       VALUES ($1,$2,$3,$4,$5)""",
                    cid, cap["key"], cap["label"], cap["ord"], cap["catalog_capability_id"])
    row = await conn.fetchrow(
        "SELECT id, key, name, version, status, is_seed, project_id FROM assessment_frameworks WHERE id=$1", fid)
    return dict(row) | {"id": str(row["id"])}


async def update_framework(conn, fid, *, name=None, scale=None, status=None) -> None:
    sets, args = [], []
    if name is not None:
        args.append(name); sets.append(f"name=${len(args)}")
    if scale is not None:
        args.append(scale if isinstance(scale, str) else json.dumps(scale)); sets.append(f"scale=${len(args)}::jsonb")
    if status is not None:
        args.append(status); sets.append(f"status=${len(args)}")
    if not sets:
        return
    args.append(str(fid))
    await conn.execute(
        f"UPDATE assessment_frameworks SET {', '.join(sets)}, updated_at=now() WHERE id=${len(args)}::uuid", *args)


async def add_category(conn, fid, *, label: str, key=None, band=None, ord=0,
                       inflection_side="pre") -> dict:
    ckey = (key or _slug(label)).strip()
    cid = await conn.fetchval(
        """INSERT INTO framework_categories (framework_id, key, label, band, ord, inflection_side)
           VALUES ($1::uuid,$2,$3,$4,$5,$6) RETURNING id""",
        str(fid), ckey, label, band, ord, inflection_side)
    return {"id": str(cid), "key": ckey, "label": label, "band": band, "ord": ord,
            "inflection_side": inflection_side}


async def update_category(conn, cid, *, label=None, band=None, ord=None, inflection_side=None) -> None:
    sets, args = [], []
    for col, val in (("label", label), ("band", band), ("ord", ord), ("inflection_side", inflection_side)):
        if val is not None:
            args.append(val); sets.append(f"{col}=${len(args)}")
    if not sets:
        return
    args.append(str(cid))
    await conn.execute(f"UPDATE framework_categories SET {', '.join(sets)} WHERE id=${len(args)}::uuid", *args)


async def add_capability(conn, cid, *, label: str, key=None, ord=0,
                         catalog_capability_id=None) -> dict:
    ckey = (key or _slug(label)).strip()
    capid = await conn.fetchval(
        """INSERT INTO framework_capabilities (category_id, key, label, ord, catalog_capability_id)
           VALUES ($1::uuid,$2,$3,$4,$5) RETURNING id""",
        str(cid), ckey, label, ord, catalog_capability_id)
    return {"id": str(capid), "key": ckey, "label": label, "ord": ord,
            "catalog_capability_id": catalog_capability_id}


async def update_capability(conn, capid, *, label=None, ord=None, catalog_capability_id=None) -> None:
    sets, args = [], []
    for col, val in (("label", label), ("ord", ord), ("catalog_capability_id", catalog_capability_id)):
        if val is not None:
            args.append(val); sets.append(f"{col}=${len(args)}")
    if not sets:
        return
    args.append(str(capid))
    await conn.execute(f"UPDATE framework_capabilities SET {', '.join(sets)} WHERE id=${len(args)}::uuid", *args)


async def add_state(conn, fid, *, label: str, key=None, ord=0, kind="target") -> dict:
    skey = (key or _slug(label)).strip()
    await conn.execute(
        """INSERT INTO framework_states (framework_id, key, label, ord, kind)
           VALUES ($1::uuid,$2,$3,$4,$5) ON CONFLICT (framework_id, key) DO UPDATE
             SET label=EXCLUDED.label, ord=EXCLUDED.ord, kind=EXCLUDED.kind""",
        str(fid), skey, label, ord, kind)
    return {"key": skey, "label": label, "ord": ord, "kind": kind}


# ── Scoring ───────────────────────────────────────────────────────────────────
def build_scoring_prompt(framework: dict, findings: list, states: list) -> tuple[str, str]:
    """The system + user prompt for the LLM scoring pass. `framework` is the wall skeleton
    from _framework_structure (bands→categories→capabilities); `findings` are the assessment's
    raw findings (capability_handle/category/maturity/state/notes); `states` are the framework's
    target states (current excluded — current back-fills from findings). Returns (system, user)."""
    caps = []
    for band in framework.get("bands", []):
        for cat in band.get("categories", []):
            for cap in cat.get("capabilities", []):
                caps.append({"id": cap["id"], "category": cat["label"], "capability": cap["label"]})
    scale = framework.get("scale", [])
    scale_lines = "\n".join(
        f"  {s.get('value')} = {s.get('label')}" for s in scale) or "  0..5 (low→high)"
    target_states = [s for s in states if s.get("kind") in ("target", "desired")]
    state_keys = [s["key"] for s in target_states]
    finding_lines = "\n".join(
        f"- {f.get('category') or '—'} / {f.get('capability_handle')}: "
        f"maturity={f.get('maturity')} state={f.get('state')} "
        f"{(f.get('notes') or f.get('evidence') or '')[:160]}"
        for f in findings) or "(no findings)"
    system = (
        "You are a platform-maturity assessment scorer. Given an assessment's findings and a "
        "configurable maturity framework (capabilities scored 0..5 on a Function Appraisal scale), "
        "propose a 0..5 maturity score for each framework capability in each requested target "
        "state, foundational-first (earlier phases raise the weakest/foundational capabilities; "
        "later phases approach the desired state). Output ONLY a single JSON object — no prose, "
        "no markdown fences.")
    user = (
        "Maturity scale:\n" + scale_lines + "\n\n"
        "Target states to score (each capability gets a 0..5 per state; omit a capability/state "
        "when you cannot justify a score):\n  " + ", ".join(state_keys or ["(none)"]) + "\n\n"
        "Framework capabilities (use the exact id):\n"
        + "\n".join(f"- id={c['id']} · {c['category']} / {c['capability']}" for c in caps) + "\n\n"
        "Assessment findings (evidence to score from):\n" + finding_lines + "\n\n"
        "Return JSON of this exact shape:\n"
        '{ "scores": [ { "capability_id": "<id>", "state": "<state-key>", '
        '"maturity": 0-5, "rationale": "short why" } ] }\n'
        "Only include states from the target list above. Ground every score in the findings.")
    return system, user


def parse_scoring_response(raw: str, valid_cap_ids: set, valid_states: set) -> list[dict]:
    """Parse + validate the model's JSON: keep only well-formed rows referencing a known
    capability id + target state, with a 0..5 maturity. Defensive — a model can hallucinate."""
    try:
        obj = json.loads(_strip_code_fences(raw))
    except Exception as e:
        raise ValueError(f"scorer did not return valid JSON: {e}")
    rows = obj.get("scores") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        raise ValueError("scorer JSON missing a 'scores' array")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("capability_id") or "")
        state = str(r.get("state") or "")
        m = _coerce_maturity(r.get("maturity"))
        if cid in valid_cap_ids and state in valid_states and m is not None:
            out.append({"capability_id": cid, "state": state, "maturity": m,
                        "rationale": (r.get("rationale") or None)})
    return out


async def persist_llm_scores(conn, assessment_id, scored: list, *, updated_by: str) -> int:
    """Write LLM-proposed scores as source='llm'. NEVER overwrites a source='human' cell
    (curated scores are the truth). Returns how many cells were written."""
    n = 0
    for s in scored:
        # Upsert, but the WHERE on the DO UPDATE protects human cells.
        res = await conn.execute(
            """INSERT INTO assessment_capability_scores
                   (assessment_id, framework_capability_id, state_key, maturity, rationale, source, updated_by, updated_at)
               VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'llm', $6, now())
               ON CONFLICT (assessment_id, framework_capability_id, state_key) DO UPDATE
                 SET maturity=EXCLUDED.maturity, rationale=EXCLUDED.rationale,
                     source='llm', updated_by=EXCLUDED.updated_by, updated_at=now()
                 WHERE assessment_capability_scores.source <> 'human'""",
            str(assessment_id), s["capability_id"], s["state"], s["maturity"],
            s.get("rationale"), updated_by or None)
        # asyncpg returns 'INSERT 0 1' / 'UPDATE 1' / 'UPDATE 0' (human cell skipped).
        if res and res.rsplit(" ", 1)[-1] != "0":
            n += 1
    return n


async def apply_overrides(conn, assessment_id, overrides: list, *, updated_by: str) -> int:
    """Persist HUMAN cell overrides with provenance (source='human', updated_by, updated_at).
    A human score always wins and is distinguishable from an LLM one. maturity=None clears the
    cell to '-' Not Assessed (still source='human' — a deliberate human 'not assessed')."""
    n = 0
    for o in overrides:
        cid = str(o.get("capability_id") or o.get("framework_capability_id") or "")
        state = str(o.get("state") or o.get("state_key") or "")
        if not cid or not state:
            continue
        m = _coerce_maturity(o.get("maturity"))
        await conn.execute(
            """INSERT INTO assessment_capability_scores
                   (assessment_id, framework_capability_id, state_key, maturity, rationale, source, updated_by, updated_at)
               VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'human', $6, now())
               ON CONFLICT (assessment_id, framework_capability_id, state_key) DO UPDATE
                 SET maturity=EXCLUDED.maturity, rationale=EXCLUDED.rationale,
                     source='human', updated_by=EXCLUDED.updated_by, updated_at=now()""",
            str(assessment_id), cid, state, m, (o.get("rationale") or None), updated_by or None)
        n += 1
    return n

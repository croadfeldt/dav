"""Server-side run-selection resolution for /api/runs (set-scoped triggers).

The New Run UI resolves a Scoping Set's members into uc_handles / uc_uuids /
managed_uc_uuids client-side (ui/_filterFromSetMembers) before POSTing
/api/runs. The API stored set_id / selection_mode as run_sessions lineage
only — they never reached the pipeline params. So an API caller sending
{"selection_mode": "set", "set_id": N} with no UC lists got a SILENT
full-corpus run (repro: dav-stage2-console-853521 — mode=verification,
selection_mode='set', set_id=29, run-corpus log "Total: 420 UC(s)").

This module is the server-side twin of the UI mapping so /api/runs can
enforce the declared scope itself. Pure functions (no FastAPI/DB imports)
so test_run_selection.py runs standalone like the other api tests.
"""
from __future__ import annotations

from typing import Optional

# selection_mode values that declare a NARROWED scope: the run must carry a
# concrete UC list (explicit, or resolved from the set). 'corpus'/None = whole
# corpus is intended.
NARROWED_MODES = {"set", "selection", "individual"}

# selection_action() verdicts
PASS = "pass"                # payload scope is already correct as sent
RESOLVE_SET = "resolve-set"  # resolve set members server-side into UC lists
REJECT = "reject"            # declared narrowed scope with nothing to run — 400


def selection_action(selection_mode: Optional[str], set_id,
                     *, is_all_set: bool,
                     has_explicit_selection: bool) -> str:
    """Decide how /api/runs must treat the declared selection.

    Invariant enforced: a payload that DECLARES a narrowed scope
    (selection_mode in NARROWED_MODES, or a real set_id) must never fall
    through to a full-corpus run. Full corpus stays available via
    selection_mode='corpus', set_id='__all__', or simply no selection fields.
    """
    if has_explicit_selection:
        # UI path (and any caller that resolved its own lists): unchanged.
        return PASS
    if set_id is not None and not is_all_set:
        # A real set id with no explicit lists — resolve it server-side,
        # whether or not the caller also said selection_mode='set'.
        return RESOLVE_SET
    if selection_mode == "set":
        # set scope declared, but set_id is missing or '__all__'. '__all__'
        # genuinely means the whole corpus; missing set_id is unresolvable.
        return PASS if is_all_set else REJECT
    if selection_mode in NARROWED_MODES:
        # 'selection' / 'individual' with no UC lists: the same silent
        # full-corpus trap as the set bug — refuse instead.
        return REJECT
    return PASS


def member_filter(members: list[dict]) -> dict:
    """Map use_case_set_members rows → engine filter lists.

    Mirrors the UI's _filterFromSetMembers exactly:
      - uc_source == 'managed'          → managed_uc_uuids (engine fetches the
                                          YAML from the console API at run start)
      - corpus member with uc_handle    → uc_handles
      - corpus member without a handle  → uc_uuids
    """
    handles: list[str] = []
    uuids: list[str] = []
    managed: list[str] = []
    for m in members:
        if (m.get("uc_source") or "") == "managed":
            if m.get("uc_uuid"):
                managed.append(m["uc_uuid"])
        elif m.get("uc_handle"):
            handles.append(m["uc_handle"])
        elif m.get("uc_uuid"):
            uuids.append(m["uc_uuid"])
    return {"uc_handles": handles, "uc_uuids": uuids,
            "managed_uc_uuids": managed}

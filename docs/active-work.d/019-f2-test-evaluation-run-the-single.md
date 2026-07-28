## F2 — "Test evaluation" → run the single open UC directly + relabel
**Status: not started.**
- Today `testRunUC(uuid, ucPath, title, branchOverride)` (index.html:7051) builds a
  one-UC filter then calls `openNewRun(...)` — which opens the New Run config page
  (the "full use case run documentation" the user does NOT want).
- Runs are actually triggered by `submitNewRun()` (index.html:5601) → `POST /api/runs`
  with payload incl. `uc_handles`/`uc_uuids`/`managed_uc_uuids` from `_pendingRunFilter`,
  `selection_mode:'individual'`, model/endpoint via `_resolveEndpointModel`, defaults.
- **TODO:** make the button **submit the run immediately** for just the open UC
  (build the minimal /api/runs payload from the UC's filter + current project defaults,
  POST, then jump to the run detail) instead of opening the modal. Keep a path to the
  full config for power users (maybe shift-click or a small "configure…" affordance).
- **Relabel** the two buttons `▶ Test evaluation` (index.html:6806 corpus/managed,
  index.html:8362 managed-direct) — proposed new text **"Run this UC as well"**
  (⚠ CONFIRM wording with user — odd for a single-UC action; they asked for it
  literally). Update the `title=` tooltips at 7076-7077 too.
- Batch sibling: `#ucSelTestBtn "▶ Test selected"` (index.html:1190) — leave unless asked.


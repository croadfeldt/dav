## F1 — Additional-context text section for UC creation (esp. bulk)
**Status: backend DONE, UI only remaining.**
- Backend already supports it: `UCBulkExtractIn.context` (main.py:3568, max 4000) →
  endpoint `POST /api/use-cases/bulk-from-text` (main.py:3743) → `uc_assist.extract_bulk(context=…)`
  (uc_assist.py:299/318) injects "Additional context:\n…" into `_BULK_SYSTEM_PROMPT`.
  Single-UC assist path also has `context` (uc_assist.py:159/180).
- **TODO:** add an "Additional context" `<textarea>` to the BULK UC IMPORT MODAL
  (index.html ~2251, "M12a / ADR-008") and pass its value as `context` in the
  bulk-from-text POST body. Check whether single-UC create already shows a context
  field to mirror its styling/labeling. Keep it optional, ≤4000 chars.


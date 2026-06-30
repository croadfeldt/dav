# DAV Engine — Dimension Enum Alignment Handoff

> **For:** The DAV rearchitecture session
> **From:** Work session 2026-06-30
> **Context:** The Piotr-feedback UC corpus (`feat/piotr-uc-feedback` branch in `croadfeldt/dcm`) uses dimension values that the DAV engine rejects. The UCs are correct per the UDLM/DCM taxonomy; the engine enums are stale.

## What's broken

The engine's dimension enums in `engine/src/dav/core/consumer_profile.py` (line ~160) and `review-console/api/app/main.py` (line ~1853) reject 4 values the UCs use. 24 of 28 UCs in the Piotr-feedback scoping set (set ID 27) fail validation or fail to load.

## The 4 enum mismatches

| # | Dimension | UC value (correct) | Engine value (stale) | Affected UC count | Resolution |
|---|-----------|-------------------|---------------------|-------------------|------------|
| 1 | `policy_complexity` | `single_gating` | `single_gatekeeper` | 3 | **Rename.** GateKeeper → Gating Policy rename happened in UDLM. The schema doc (`dcm/dav/schemas/use_case.schema.json` line 110) already says `single_gating (formerly single_gatekeeper)`. Engine needs to match. |
| 2 | `lifecycle_phase` | `modification` | `day2_change` | 5 | **Add or rename.** The UCs describe a modification to an existing resource (policy override, audit verification, etc.). `day2_change` may be the same concept — confirm with Chris whether to rename `day2_change` → `modification` or add `modification` alongside it. |
| 3 | `lifecycle_phase` | `drift_detection` | `drift_remediation` | 2 | **Add.** Detection and remediation are distinct phases. The engine has `drift_remediation` but not `drift_detection`. Add `drift_detection` as a separate value — a UC that exercises finding drift is not the same as one that exercises fixing it. |
| 4 | `resource_complexity` | `compound_service` | `composite_service` | 6 | **Rename.** DCM taxonomy PR renamed to Composite Resource (not compound). The UCs use `compound_service` which may itself be wrong — should be `composite_service` per the taxonomy. **Check with Chris:** fix the UCs to `composite_service` (which the engine already accepts), or rename the engine value. |

## Files to update

1. `engine/src/dav/core/consumer_profile.py` ~line 160 — the enum lists
2. `review-console/api/app/main.py` ~line 1853 — same enum lists (duplicated)
3. `review-console/api/app/uc_assist.py` ~line 60 — prompt text references `single_gatekeeper`
4. `review-console/ui/index.html` ~line 5667 — UI references `single_gatekeeper`
5. `engine/src/dav/scripts/smoke_test_stage2.py` ~line 71 — smoke test uses `single_gatekeeper`

## Separate issue: `metadata` field loading

The run logs also show 22 UCs failing with `UseCaseMetadata.__init__() got an unexpected keyword argument 'edited'` and `'note'`. The Piotr-feedback UCs have `metadata.edited` and `metadata.note` fields that the `UseCaseMetadata` dataclass doesn't accept. Fix: either add those fields to the dataclass or use `**kwargs` / ignore unknown fields.

## Verification

After fixing, re-run the Piotr-feedback scoping set (set 27, 28 UCs). Expected: 0 validation failures, 28/28 load successfully.

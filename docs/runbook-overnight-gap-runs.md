# Runbook — overnight corpus gap-analysis runs (repeatable)

**Goal.** Run the full UC corpus (21-Sept baseline + all hammer sets + the whole corpus) through DAV's
gap engine for the DCM (and DAV) projects, so runs are **visible in the review-console UI**, then consolidate
the gaps into a report.

## Preconditions
- DAV reachable: `https://10.0.90.22:8843` (IPv4; homelab). Inference model up: `qwen3-32b.llm.ocp.roadfeldt.com`.
- PAT: `~/.claude-personal/.dav-token` (Bearer). Project is selected via the **`X-DAV-Project`** header (NOT a query param).
- Corpus is a registered corpus repo; the UCs live on `dcm` **main** under `dav/use-cases/`.

## Projects
- `20` = **DCM** (realization analysis) · `727` = **DAV** (self-analysis) · `1` = Default.

## Trigger a run (creates a `run_session`, visible in the UI)
`POST /api/runs` with header `X-DAV-Project: <pid>` and body (`RunTriggerIn`):
- `mode`: `verification` (default gap mode; 3 ensemble samples/UC).
- Whole corpus: `{"selection_mode":"corpus","set_id":"__all__"}`.
- One set (e.g. 21-Sept): `{"selection_mode":"set","set_id":29,"set_name":"FF Extended Target"}`.
- Always set `name` + `description` (they show in the console).

```bash
TOK=$(cat ~/.claude-personal/.dav-token); B=https://10.0.90.22:8843
curl -sk -4 -X POST -H "Authorization: Bearer $TOK" -H "X-DAV-Project: 20" -H "Content-Type: application/json" \
  -d '{"mode":"verification","selection_mode":"corpus","set_id":"__all__","name":"<name>","description":"<desc>"}' \
  "$B/api/runs"
```
Response: `{"ok":true,"run":{"name":"dav-stage2-console-NNNNNN",...},"resolved_params":{"inference_model":"qwen3-32b",...}}`.

## Read state (all GET, `X-DAV-Project` header)
- Sets: `/api/sets` · Runs: `/api/runs` · Gaps: `/api/analysis/gaps` · Roadmap: `/api/analysis/roadmap` · UCs: `/api/use-cases?limit=N`.

## Notes / gotchas
- The run **trigger** is on the review-console API (`POST /api/runs`), NOT the ops-mcp (which is read + set-management only).
- New/unpushed UCs can be included per-run via `managed_uc_uuids` / `uc_uuids`; pushed corpus UCs come from git (main).
- There is **no per-project `spec_repo`** configured — project 20 analyzes the corpus; a dedicated UDLM-data-model
  analysis project/spec is not set up (flagged as a gap).

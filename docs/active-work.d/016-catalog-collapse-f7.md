## ✅ SHIPPED 2026-06-09 — catalog collapse + F7
- **Catalog collapse (task #90):** the duplicate `capability_inventory` (keystone draft)
  is **dropped**; the Capability entity **is** the existing `capability_catalog`, extended
  additively into the UDLM Knowledge family (migration 020 + schema.sql). `cap_key`=handle,
  `status`=lifecycle (+`observed`), `project_id` nullable (NULL=global observed). Existing
  Catalog CRUD untouched. Shared write path `upsert_observed_capability()`. See
  `docs/capability-catalog-design.md` → "SHIPPED STATE (2026-06-09)".
- **F7 — assessment ingestion:** `assessment_ingest.py` (parser registry: generic +
  automation adapter; `synthetic_fixture()` — NO confidential data), migration 019
  (`assessments` + `assessment_findings`), endpoints `POST /api/assessments/ingest` (body
  `{use_fixture:true}` for the synthetic), `GET /api/assessments`, `GET /api/assessments/{id}`
  (+ gap summary). Assessments nav tab (platform-admin) → list + ingest + per-assessment
  findings/gap view. Validated on ephemeral Postgres (drop+extend+nullable+legacy-CRUD
  compat+seed+resolve+ingest). **WORK/PERSONAL BOUNDARY honored** — generic mechanism +
  synthetic data only; real per-format parsers + engagement data go inside the work env.


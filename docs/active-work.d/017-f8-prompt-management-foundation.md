## ✅ SHIPPED 2026-06-09 — F8 prompt management (foundation)
Per-project, per-stage prompt customization. Design: `docs/prompt-management-design.md`.
- **Schema:** `project_stage_context.section_overrides JSONB` (content = append context);
  new **`prompt.manage`** privilege seeded to project-admin/edit; **supersedes**
  `project.archreview.context` (rbac.py aliases old→new for back-compat).
- **Registry:** `prompts_registry.py` — stages + named base sections + `assemble()`.
  Stages: `stage2-analysis` (engine, **stored-held** — A/B before runtime enable),
  `arch_review` (console, **append-live**).
- **API:** GET `/api/prompts/stages`, GET `/api/prompts/project/{stage}` (customization +
  assembled preview); PUT `/api/stage-context/{stage}` extended (section_overrides, now
  gated on `prompt.manage`, **active-project** scoped — was a default-project bug).
- **UI:** Improve nav → **Prompts & Improvement** (tabs: Prompt management + existing
  diagnose/propose/experiments). Editor: stage picker → append box + per-section override
  + live assembled preview + Save.
- **HELD (needs Chris):** wiring section overrides to the **stage-2 engine** prompt
  (thread customization via Tekton param/env; section the base template). Byte-identical
  by default; any real stage-2 override is a prompt-quality change → A/B first.

Resume scratchpad for the current batch of asks. Survives chat-context loss.
Repo: `/Users/chris/git/dav`. Big single files: `review-console/api/app/main.py`
(~466KB), `review-console/ui/index.html` (~725KB), `review-console/api/app/schema.sql`.
Design doc: `docs/review-console-design.md` (keep in sync per house rule).


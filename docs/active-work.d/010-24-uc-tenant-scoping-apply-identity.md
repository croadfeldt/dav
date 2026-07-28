## ✅ SHIPPED 2026-06-23/24 — UC tenant-scoping, apply, identity, set-name, audited deletion (#199/#43)
**Why:** the masthead pill mis-reported a project's UC totals, bulk-extraction saves failed validation,
and a set rename didn't propagate to runs — all symptoms of scoping/identity/reference gaps. Full design
+ as-shipped detail in **`uc-tenant-scoping-project-application.md`** (and the Use Cases tab section of
`review-console-design.md`). Builds #346–#349 (API) / #355–#357 (UI). Branch `feat/tenant-aware-migrations`.
- **Pill = complete story.** Project total = managed + **corpus from the project's corpus-role repos**
  (`managed_repos WHERE 'corpus'=ANY(roles)`, namespace-matched), counted regardless of ingest status.
  `/api/use-cases` + `/api/freshness`. Verified dav=7, dcm=114.
- **Apply button (#43).** Managed UCs are tenant assets referenced into projects via M:N
  `use_case_projects` ("in this project" = home OR referenced); toolbar *project scope* selector +
  `?applied=0` "available to apply" pool; `POST /api/use-case-projects[/remove]`. Fork still pending.
- **Server owns UC identity.** UUID assigned on save; missing `handle` auto-derived before validation
  (fixes bulk-extraction save failures — the prompt enums already match the validator). Migration `t002`
  refreshed fabricated ids.
- **Reference-by-ID.** Scoping-set name resolved by joining `use_case_sets` on `set_id` everywhere it's
  displayed (runs list, analysis summary, rerun-config, experiments); stored snapshot is a provenance
  fallback only.
- **Audited deletion (right-to-erase).** `GET …/delete-impact` preview + UI propagation warning; deletes
  clean the no-FK join rows, **audit** (`use_case.delete` / `use_case_set.delete`), retain historical
  analyses by default, and offer `?purge_analyses=true` for full sovereignty erasure.


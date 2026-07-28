## ✅ SHIPPED 2026-06-17 — #173 Created + last-modified timestamps in Authoring views
**Why:** authors need to see when a UC / Scoping Set was created and last touched, unobtrusively,
to gauge freshness and ownership. The data was already captured (`managed_use_cases` and
`use_case_sets` both carry `created_at`/`updated_at`, all writes set `updated_at=now()`) and the
API already exposes both on `/api/use-cases[/{uuid}]` and `/api/sets[/{id}]`. The UC **detail**
Provenance block already rendered created/updated by + timestamps. The only gap was the
**Scoping Sets** Authoring accordion, which showed description + member count but no timestamps —
added an unobtrusive `created … · updated …` metadata line (`fmtTs`, `--text-faint`, full
timestamps on hover) matching the existing metadata styling. UI-only change; no migration needed.


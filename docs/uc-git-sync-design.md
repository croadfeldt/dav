# Use Case ↔ git repo sync — design

Status: **DRAFT for review** (design-only; no code) · Task: #257 · 2026-07-01
Related: #42 (UC git round-trip: git=truth, DB=projection), #41 (corpus mechanism), #240 (import
UCs from a remote branch), #181 (dedup), #182 (schema-constrained extraction), the UC fixer
(`uc-fix-design.md`).

> **This doc is for Chris to approve before any code.** It records the model, the authority
> decision, conflict handling, triggers, and the migration/engine impact. Nothing breaking is built
> until the authority model + trigger choice below are ratified.

## 1. Problem
Use cases live in **two places**: the **DB** (`managed_use_cases`, project-owned, edited in the
console) and **git repos** (corpus-role repos, one YAML per UC). Today they can **drift**: a UC edited
in the console isn't in git until someone clicks *Push to corpus*; a UC changed in git isn't in the DB
until the corpus file-cache re-reads it. The ask: **keep the two in sync** so DAV and the repos agree.

## 2. What already exists (don't rebuild)
DAV already has **both directions, partially and manually**:

| Direction | Mechanism today | Gap |
|-----------|-----------------|-----|
| **git → DB** | corpus-role repos are cached into the `files` table; their UC YAML is surfaced as `source=corpus` UCs (read-only projections) | no *managed*-UC update from git; no drift signal; refresh is manual/implicit |
| **DB → git** | `POST /api/use-cases/{uuid}/push-to-corpus` writes a managed UC out to a corpus repo, recording `corpus_synced_path`; `GET /api/corpus-push/status` reports unpushed UCs | manual (per-UC button); no auto-push; no pull-back; no conflict handling |

So "sync" = **formalize + automate the round-trip that already half-exists**, not green-field.

## 3. Authority model (the load-bearing decision)
Per **#42**, the established direction is **git = source of truth, DB = projection**. This design adopts
that as the **default**, with a pragmatic split because DAV *authors* UCs in the DB:

- **Corpus UCs** (`source=corpus`, live in a corpus repo): **git is authoritative.** The DB row is a
  cache/projection. On drift, git wins; the DB is refreshed from git.
- **Managed UCs** (`source=managed`, DB-authored, not yet pushed): **DB is authoritative until first
  push.** Authoring happens in the console; the UC has no git home yet. Once pushed
  (`corpus_synced_path` set), it becomes a corpus UC and **git becomes authoritative** for it.
- **The boundary is the push.** "Managed, unpushed" = DB owns it. "Pushed / corpus" = git owns it,
  DB projects it. This matches how authors actually work and keeps #42's git=truth for everything
  that has a git home.

Rejected alternatives: *DB-always-truth* (breaks git-native review/PR workflows, the whole point of
corpus repos); *per-repo policy flag* (more config surface; revisit only if a real case needs it).

## 4. Sync operations
Four operations, each a thin formalization of existing pieces:

1. **Pull (git → DB refresh).** For a corpus repo/branch, re-read its UC YAML and update the DB
   projection. Extends the existing corpus file-cache with an explicit, on-demand + triggerable
   refresh and a per-UC content hash so no-op re-reads are cheap.
2. **Push (DB → git).** Existing `push-to-corpus`, but callable in bulk and (optionally) automatically
   on managed-UC save when the project opts in. Writes YAML + records `corpus_synced_path` + the
   pushed content hash.
3. **Drift report (read-only, ship first).** For each UC with a git home, compare DB content hash vs
   git content hash → `in-sync | db-ahead | git-ahead | diverged`. **No writes.** This is the safe
   first slice and the observability spine for everything else.
4. **Reconcile.** Apply the authority model to a drifted UC: git-ahead → pull; db-ahead (managed,
   unpushed, or explicitly DB-owned) → push; **diverged → never auto-resolve** (§5).

## 5. Conflict handling
- **git-ahead / db-ahead** (one side changed): auto-resolvable per §3 authority — apply the winning
  side, record provenance.
- **diverged** (BOTH changed since last sync): **never silently overwrite.** Surface in the drift
  report; require an explicit human choice (keep-git / keep-db / open-a-diff). Track a
  `last_synced_hash` per UC so "both changed" is detectable (three-way: base = last synced).
- **Deletes**: a UC removed on one side is the sharpest edge — treat as diverged-class (never
  auto-delete the other side); surface for confirmation.

## 6. Triggers (pick one to start; all opt-in per project)
- **Manual** (default, safe): the drift report + explicit Pull/Push/Reconcile buttons. Ship this first.
- **On-event**: push on managed-UC save (opt-in); pull on the existing repo-listener webhook
  (`el-dav-repo-listener` already exists for corpus repos) when a UC file changes on the tracked branch.
- **Scheduled**: a periodic drift scan → surface (not auto-apply) in the masthead/Audit.

Recommendation: **manual + drift report first**, then wire the repo-listener for auto-**pull** of
corpus UCs (git=truth, low risk), and leave auto-push as an explicit per-project opt-in.

## 7. Data / schema impact
- Add to `managed_use_cases` (or a sidecar `uc_sync_state` table): `git_repo`, `git_branch`,
  `git_path`, `last_synced_hash`, `last_synced_at`, `sync_direction_owner`. (`corpus_synced_path`
  already exists — extend, don't duplicate.)
- **Migration**: additive columns only — non-breaking, back-fillable from `corpus_synced_path`.
- No table renames. No engine/pipeline change (the analyze pipeline reads UCs the same way).

## 8. Interaction with the UC fixer (new constraint)
The deterministic/LLM UC fixer (`uc-fix-design.md`) writes fixes to the **DB**. Under §3, if a fixed UC
has a git home, git is authoritative — so a DB-side fix must be **pushed back to git** to be durable
(else the next pull reverts it). Options: (a) after a fix, mark the UC `db-ahead` and let the drift
report prompt a push; (b) auto-push fixes when the project has auto-push on. **(a) is the default** —
the fixer already produces a reviewable diff; the push stays an explicit, authored step.

## 9. Rollout slices (for when this is approved)
- **A — Drift report (read-only).** `GET /api/uc-sync/drift` + a Use-Cases view surface. Zero writes,
  zero migration risk. Delivers the "are we in sync?" answer immediately.
- **B — Sync-state columns + Pull/Push formalized** (additive migration; bulk push; on-demand pull).
- **C — Auto-pull via repo-listener** (git=truth for corpus UCs; webhook already exists).
- **D — Conflict/diverged UI** (three-way, human-resolved) + optional auto-push opt-in.

## 10. Open decisions for Chris
1. **Authority split (§3)** — confirm "git=truth for pushed/corpus, DB=truth for managed-unpushed",
   or override (e.g. git=truth for *everything*, making managed UCs push-on-create).
2. **First automation** — auto-pull corpus UCs via the repo-listener (recommended) vs stay
   fully-manual until the drift report has proven itself.
3. **Auto-push** — never (always explicit), per-project opt-in (recommended), or on by default.
4. **Deletes** — confirm "never auto-propagate a delete" (recommended).
5. Whether sync state lives on `managed_use_cases` or a separate `uc_sync_state` table.

_No code will be written against this until §10 is answered._

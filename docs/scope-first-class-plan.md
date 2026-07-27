# Scope as a first-class artifact — the plan

_Plan, 2026-07-27, per Chris: "let's plan out the scope related changes first." Covers friction
items 4–7 (`corpus-scope-friction.md`); the source-plane epic (items 1–3) is planned separately
in `run-source-resolution-design.md` (#87) and only its seam is referenced here. This is a
sequencing + design-shape document: decision points are marked ⚖ and need Chris's ruling before
the affected PR starts._

## The centerpiece: a corpus index in the DB

Everything in items 4–7 reduces to one missing artifact: **DAV never knows what a run will
analyze until the engine has already started.** Scope is discovered at stage 2, quarantine is
discovered in a YAML on the PVC, and the UI's denominators fall back to ingested counts.

The fix is a **`corpus_index` table**: one row per UC file per registered namespace —
`(namespace, path, uuid, handle, family, success_semantics, dimensions, indexed_at,
repo_sha, valid, invalid_reason)`. Populated by the same sweep that already walks every repo
(the repo sync that today reports only `files_seen: 884`), including **dimension-vocabulary
validation at index time** — so quarantine is *predicted before launch*, not discovered after.

This is the DB-as-source-of-truth ruling applied to scope: the index is a queryable projection
of the corpus, refreshed on repo sync, SHA-stamped so staleness is visible.

## PR sequence (each ≤ the sizing rule, each independently shippable)

**P1 — the index.** Migration + sweep populates it + `GET /api/corpus/index?namespaces=…`
returning counts, families, and predicted-quarantine with reasons. No behavior change to runs.

**P2 — trigger-time scope resolution.** At trigger, corpus-mode runs resolve their scope from
the index (namespaces ∩ selection): store `uc_scope_total` (extending t007 to corpus mode) plus
a scope snapshot (the UC uuid list) on `run_sessions`. Folds in the parked
`feat/trigger-preflight` branch (namespace validation against registered repos; empty-catalog
warning) — same seam, one review. Response carries `warnings`.

**P3 — the preflight surface.** New Analysis modal shows, *before launch*: N UCs will be
analyzed (per namespace), M predicted-quarantined and why, catalog status, and the warnings from
P2. Run rows and headers switch to declared scope everywhere (kills the remaining "8/8 while 10
analyzed" fallback for corpus mode).

**P4 — quarantine as a run artifact.** Ingest `quarantine.yaml` into the DB per run; run detail
gets a quarantine panel (count + per-UC reason). Closes the loop with P1's prediction: predicted
vs actual quarantine on the same page — a mismatch means the index is stale or the engine
disagrees with the index's validation, both worth seeing.

**P5 — catalog seeding path (item 7).** `POST /api/capabilities/import` from a repo artifact
(the capability matrix already lives in git); #83-style sync rather than hand-fed SQL. Smallest
of the five; can run in parallel with P3/P4.

Bootstrap item 6a (agent-created project invisible to humans) is a one-liner policy question,
not a PR of its own: ⚖ below.

## ⚖ Decision points — ALL RULED (Chris, 2026-07-27): recommendations accepted as written

1. **RULED — sync-refresh + staleness marker.** Original question: refresh on repo-sync only, or also on-demand at trigger when the
   registered SHA differs from the indexed SHA? *Recommend: sync-refresh + a staleness marker in
   the preflight response ("index is N commits behind"), not a blocking re-index at trigger —
   trigger stays fast, staleness stays visible.*
2. **RULED — warn; block only at 0-of-N.** Original question: warn or block? *Recommend: warn in the modal, never block —
   the operator may be launching precisely to exercise the quarantine path (the fixture suite
   does). A "0 of N UCs would run" preflight result should block, since the run is certainly
   pointless.*
3. **RULED — snapshot at trigger + reconcile at sync.** Original question: store the resolved UC list at trigger (a snapshot that can
   drift from what the engine sees if the repo moves between trigger and clone), or re-resolve at
   sync and reconcile? *Recommend: snapshot at trigger + the engine's actual list recorded at
   sync; a diff between them is provenance signal (the corpus moved mid-launch), same spirit as
   the SHA pinning in #87.*
4. **RULED — auto-grant the PAT's owning human project-admin at creation.** Original question: when a PAT/agent creates a project, auto-grant the PAT's owning human
   project-admin? *Recommend: yes — one INSERT at creation; removes the invisible-project trap
   without a new surface.*
5. **RULED (by default, unobjected) — SHA-keyed independence.** Original question: P1's sweep currently hangs off the
   projection-triggered sync. *Recommend: build P1 against the current sync now, with the index
   keyed by namespace+SHA so it is indifferent to WHO triggers indexing — when #87 replaces the
   projection, the index's producer moves without the schema changing. The two epics stay
   unblocked from each other.*

## What this does not touch

Engine analysis behavior, verdicts, the ensemble, fixtures. Scope here means "what enters a
run and how the operator sees it" — nothing in this plan alters what happens to a UC once it is
analyzed.

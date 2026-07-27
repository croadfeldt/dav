# Corpus & scope: the friction inventory

_Problem statement, 2026-07-27. Chris: "we have a lot of room for improvement in the tool for
this type concern, among others." He is right, and the last 24 hours produced the evidence —
wiring one small synthetic corpus into DAV took a project, two repo rows, one manual projection
call, one DB-level workaround, and two catalog seedings, and still yielded three separate
"why can't I see it?" moments. Every item below is anchored to an incident from that session,
so this is measured friction, not speculation. Directions are sketches, not designs; each of
the big ones deserves its own note before build._

## 1. One shared source plane (the root cause of most of the rest)

**Friction:** there is ONE set of source ConfigMaps and ONE docs-MCP, so exactly one project's
repos are projected (`DAV_MCP_SOURCE_PROJECT_SLUG`, default `dcm`). A repo registered in any
other project is invisible to every run.

**Incident:** the fixtures repo, correctly registered in its own isolated project, produced
`0 source(s) cloned, 3 skipped by filter`. The fix was to move its rows INTO project 20 by SQL —
i.e., the isolation had to be broken to make the corpus reachable. Isolation now rests on
per-run namespace filters instead of the project boundary.

**Direction:** resolve sources per-run from the registry (runs already clone per-run; the
ConfigMap is a projection cache, not a necessity) — which is also tenancy Phase 3's
per-project MCP prerequisite.

## 2. Projection is a hidden mutable step

**Friction:** registry edits do nothing until someone calls `POST /api/repos/project`; until
then runs silently serve the stale ConfigMap. Nothing in the UI or the run says "your sources
are N edits behind the registry."

**Incident:** fixtures were registered and correct, and two consecutive runs saw nothing —
diagnosed only by reading the sync pod's log. Reprojection was needed twice more after role/path
edits.

**Direction:** project-on-write, or kill the projection entirely per §1. Failing that, stamp the
registry revision into the ConfigMap and show drift on the run page.

## 3. One repo row = one root_path + one role set

**Friction:** a repo that carries both corpus and spec at different paths needs two registry
rows with different namespaces (`fixtures` + `fixtures-spec`), which the operator must then name
correctly in two different per-run filter fields.

**Incident:** the fixture repo's first registration (one row, root `fixtures/`) fed
`expected/*.yaml` ground truth to the UC parser.

**Direction:** per-role path map on one row (`role_paths: {corpus: ..., spec: ...}`) — the model
session independently assumed this shape exists (`metadata.role_paths.corpus`), which is a hint
it is the natural model.

## 4. Corpus-mode runs have no declared scope

**Friction:** t007 records a declared scope only for explicit UC lists. A corpus-mode run's
denominator falls back to the ingested count, so run rows read "8/8" while 10 analyses exist,
and there is NO pre-run statement of what the run will analyze.

**Incidents:** the 4/4-vs-6 masthead bug (#75/#78 fixed the explicit-list half); the "8/8"
fixture rows this morning; and the sharpest one — `corpus: 8 files` followed by
`running 1 UC(s)` with seven UCs silently quarantined and nothing in the UI saying so.

**Direction:** resolve scope at trigger for corpus mode too (enumerate matched UCs after
namespace filtering and vocabulary validation, store the list as the scope); surface quarantine
per-run in the console, not only in `quarantine.yaml` on the PVC.

## 5. Namespace filters are stringly and unvalidated

**Friction:** `corpus_namespaces: ["fixtures"]` is matched against registered namespaces with no
validation — a typo or a stale name yields a silently empty corpus, which then fails as an
engine error ("no UC YAMLs") three stages later.

**Incident:** the first fixture smoke run, plus every moment of the two-row workaround where the
right filter pair was `["fixtures"]` + `["fixtures-spec"]` and nothing would have flagged
`["fixture"]`.

**Direction:** reject unknown namespaces at trigger with the registered list in the error.

## 6. Project bootstrap traps

**Friction (a):** a project created via an agent PAT is visible only to that agent — RBAC is
working as designed, but nothing surfaces the project's existence to any human, and the agent
gets no nudge to grant a human role.
**Friction (b):** a new project's capability catalog is empty, which makes gap id-tagging
SILENTLY structurally zero — the analysis looks like it found nothing identifiable when it was
never given a vocabulary.

**Incidents:** "i don't see the fixtures project in DAV" this morning (a); id-recall 0.00 on a
run whose substance detection was demonstrably working (b). Diagnosing (a) was itself misleading
because the legacy `project_members` table coexists with the live `rbac_account_roles`.

**Direction:** creator-binds-a-human at API creation (or a visible "agent-owned projects" admin
view); a trigger-time preflight warning "0 confirmed capability ids — gaps will be untagged";
drop or migrate the legacy membership table.

## 7. Per-project catalog has no seeding path

**Friction:** the capability catalog is per-project, but populating one means SQL (fixtures) or
a bespoke import endpoint (the DCM matrix import). There is no "seed from file/repo" path an
operator can use.

**Incident:** the fixture ids went in via hand-written INSERTs, twice (schema surprises on jsonb
columns included).

**Direction:** catalog import from a repo artifact (the capability matrix already lives in git —
the catalog could sync from it the way the vocabulary now syncs, #83-style, instead of being
hand-fed).

## The shape of the fix

Items 1–3 are one epic (source resolution belongs to the run, not to a global projection);
items 4–5 are a second (scope becomes a first-class, validated, pre-run artifact); items 6–7 are
bootstrap ergonomics. The first two epics are where the room-for-improvement mostly lives, and
both align with existing direction (tenancy Phase 3; the K4 run-lifecycle work from the
2026-07-25 sweep) rather than opening new fronts.

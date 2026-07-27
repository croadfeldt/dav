# Persona-perspective analysis — REQUIREMENT

_Ruled 2026-07-27. Chris, first: "we should add perspectives for the prompts — as a software
engineer building this architecture I need to ensure it …; as an SRE using the platform I need
to ensure I can …; as an application owner, my team can …; as an auditor, I can see and
validate …". Then, upgrading it: **"We are currently just looking at the architectural
validation from the architect's perspective. We must look at ALL the perspectives."**

That second sentence reframes the product: today's output is not "the analysis" — it is ONE
lens's analysis presented as if it were the whole. A verdict without a stated perspective is
mislabeled, and the miss below is what that mislabeling costs._

## Why this works (evidence, not theory)

The fixture's first measured pass produced exactly the failure this fixes: the analyzer read the
refusal contract's **empty "Auditable" section and returned `supported`** — the generic
platform-architecture lens simply does not weight auditability. Every seeded hole in the fixture
maps cleanly onto a persona whose framing would have made it salient:

| seeded hole | the persona who cannot miss it |
|---|---|
| `FIX-AUDIT-001` (no refusal record) | auditor — *missed by the generic pass* |
| `FIX-NONLEAK-001` (refusal may leak) | auditor / security |
| `FIX-PARTIAL-WARN-001` (outcome not surfaced) | application owner |
| `FIX-QUOTA-001` (no enforcement point) | SRE |
| `FIX-DEPS-001` (broken chain realized) | SRE / application owner |
| `FIX-ATOMIC-001` / `FIX-DEP-NATURE-001` | engineer building on the platform |

Persona framing is the same move as derived-verdicts (#79): decompose one broad judgment into
narrow questions the model is actually good at. There it was contract criteria; here it is
stakeholder objectives — and the two meet: ADR-003's refusal-contract elements are already
implicit persona demands (typed → integrator, actionable → application owner, non-leaking →
security, auditable → auditor, whole → consumer). The contract was multi-perspective from the
start; only the prompt is single-lens.

**Consequence for verdicts:** a spec area can be `supported` for the engineer and
`not_supported` for the auditor — the empty Auditable section IS that case. Under this
requirement a verdict is persona-qualified, and the roll-up ("supported for 4 of 5
perspectives; the auditor's gap is X") replaces today's single unqualified verdict. The
console's ruled persona-lens UX renders exactly this.

## The one design rule this forces (decide before build)

**Diverse lenses must not quorum-suppress each other.** #80's quorum merging exists to stop
one dissenting *sample* from moving a verdict. But a persona pass is not a repeated sample — it
is a deliberately different lens, and a gap only the auditor sees is *the point*, not noise.
If persona passes feed the same ensemble, an auditor-only finding is 1-of-N and gets muted by
the very mechanism that fixed the union bias.

Rule: **quorum applies within a lens (same persona, repeated samples); findings merge across
lenses by union, tagged with their source persona.** Cross-lens agreement is signal to display
("3 personas independently flagged this"), never a gate.

**Tier-aware union (model-side addition, adopted):** `PERSONAS.yaml` carries a `tier` on every
persona — operational | governance | oversight — and the union must preserve it. A gap in the
oversight tier is different in kind from an operational one (an exec-attestation gap is not an
SRE-cascade gap), so a report can say "clean operationally; the governance tier surfaces X"
instead of flattening tiers into one pile.

## The model-side structure this consumes (udlm #276 / dcm #97)

- **`PERSONAS.yaml`** — 20 canonical personas with `tier`, `framing`, `objectives` (the
  per-lens analysis targets) + `folded_aliases`. Byte-identical in both repos, PER-001/PER-002
  gated, with a coverage signal: a persona exercised by no UC is named, because a persona is
  only real if a UC views from it.
- **`scenario.perspectives`** on every UC — the lens set for a UC is
  **`{actor.persona} ∪ perspectives`**. Both corpora are backfilled (90 + 506 UCs); coverage
  gaps were closed with 8 NEW UCs (cloud-operator, solution-architect, integrator, and an
  oversight trio) rather than by force-attaching lenses to unrelated scenarios.
- **The derivation mapping** (signal → perspective) lives in the model-side handoff and is the
  shared contract for seeding `perspectives` on DAV's own fixtures — same mapping, then
  hand-tune, so a DAV fixture and a model UC get their lenses the same way and nothing forks.
  Known under-attachments to hand-add on fixtures: cloud-operator, the oversight trio,
  integrator.

## Where personas come from

Not invented in DAV. The UC schema already carries `actor.persona`; the model side's flow docs
maintain a persona index ("document usage from ALL personas"); and the console's ruled UX
paradigm is already *persona-scoped lenses over constant objectives*. This proposal is the
engine-side twin of that ruling: per-persona analysis is what makes the UI's persona lenses
render per-persona **data** instead of filtering one generic gap pool. Persona definitions and
their objectives ("as an SRE I need to …") belong with the model/corpus side; DAV consumes them
the way it now consumes the dimension vocabulary — published artifact, not private copy.

## First experiment (cheap, already possible)

One new prompt variant: the stage-2 pass framed as the auditor. Run the fixture battery, compare
against the generic pass:

- does `FIX-AUDIT-001` recall go 0 → 1? (the known miss)
- does `FIX-NONLEAK-001` recall improve?
- do the controls stay clean — or does the auditor lens *invent* audit requirements on complete
  sections? (`must_not_report` is the half that keeps a lens honest; an auditor prompt that
  flags everything is noise wearing a costume)
- does recall on the NON-auditor holes drop? (a lens that narrows is expected; measure it so
  multi-lens union is justified by data, not assumption)

Gate and instrument: the prompt-optimization loop (task #24) — persona prompts are prompt
changes and merge on battery numbers like any other.

## Acceptance test (the fixture already is one)

Every seeded hole has a responsible lens (table above). The requirement is met when the
full-lens battery recalls the holes the single lens demonstrably misses, with the controls
still clean per lens. The single-lens baseline is on record: FIX-AUDIT-001 missed, verdict
`supported` on an empty section.

## What DAV needs from the model side

The canonical persona set with per-persona objectives ("as an X I need to ensure …") as a
**published, machine-consumable artifact** — the flow docs already maintain a persona index, so
this is publication, not invention. Consumption mirrors the dimension vocabulary exactly:
published file, CI-gated, DAV reads it, no private copy (a private persona list is the same
fork with a different name). Request going to the model session.

## Sequencing

The in-flight queue holds (measurement programme → deploy → claim battery), then the auditor
lens is the FIRST experiment through the prompt loop (#24) — it has the known miss to beat.
Full multi-lens analysis (per-persona pass, union-across-lenses merge, persona-qualified
verdicts) is the epic that follows, designed against #79's criteria decomposition rather than
bolted beside it.

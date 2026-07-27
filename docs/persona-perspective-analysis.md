# Persona-perspective analysis — design seed

_Seed, 2026-07-27, Chris: "at some point we should add perspectives for the prompts — as a
software engineer building this architecture I need to ensure it …; as an SRE using the platform
I need to ensure I can …; as an application owner, my team can …; as an auditor, I can see and
validate …". Not scheduled; captured now because tonight's data already contains the proof case
and because thinking it through surfaced one design rule that must be decided before any build._

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
stakeholder objectives.

## The one design rule this forces (decide before build)

**Diverse lenses must not quorum-suppress each other.** #80's quorum merging exists to stop
one dissenting *sample* from moving a verdict. But a persona pass is not a repeated sample — it
is a deliberately different lens, and a gap only the auditor sees is *the point*, not noise.
If persona passes feed the same ensemble, an auditor-only finding is 1-of-N and gets muted by
the very mechanism that fixed the union bias.

Rule: **quorum applies within a lens (same persona, repeated samples); findings merge across
lenses by union, tagged with their source persona.** Cross-lens agreement is signal to display
("3 personas independently flagged this"), never a gate.

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

## Sequencing

After: the scope epic P2/P3, the claim battery, and the convergence-grounding prompt fix (the
current #24 candidate). This seed exists so the design rule and the proof case are not
rediscovered from scratch when it comes up.

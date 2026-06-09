# The Holistic Realization Model

_Foundational — the frame that ties DAV, DCM, and UDLM (and our consulting work)
together. Captured 2026-06-08. Mirrored in `dcm/docs/holistic-vision.md` and
`udlm/docs/holistic-vision.md`. Build to this._

## The model

**Use Cases are the unit of desired outcome.** Everything we build and do exists to
*realize* them. A Use Case is realized only when **three foundational pillars** each
support it:

| Pillar | Question it answers | Managed through | Maturity in our tooling |
|---|---|---|---|
| **Platform** | Can the architecture/plan support the UC? | capabilities, specification, rules, context, execution | **Built today** — DAV's **AD (Architectural Design) mode**; DCM is a reference platform realization |
| **People / Process** | Is the organization — its people, skills, and processes — structured and operating to support the UC? | operating model, org design, **value streams**, skills | Not yet built |
| **Enablement** | Is the consumer enabled to adopt, consume, and operate the UC? | adoption, change, skills transfer, operationalization | Emerging — the consulting/enablement lens |

> **Platform + People/Process + Enablement → realization of Use Cases → the holistic vision.**

Each pillar is a **different view of mostly the same data** (UCs ↔ gaps ↔
capabilities). The same gap-analysis engine evaluates every pillar; what changes per
pillar is the **evaluation target** (what the UC is evaluated against) and the
**ingestion method** (what data feeds it). The output shape is always the same:
**gaps → prioritized capabilities → strategy + roadmap.**

DAV originally evaluated UCs against the **DCM** spec; that was always just the first
instantiation. The general mode is **AD — validate that the underlying plan/architecture
can support a designated Use Case, and if not, say why and how to address it
holistically.** Any spec, not just DCM.

## How the work flows (consulting engagement)

1. **Agree outcomes.** Customer + us define the desired outcomes, execution, and
   operational details. These become / refine the Use Cases — the driver.
2. **Assess.** Our existing assessment process produces current-state data across the
   pillars: **Value Stream Mapping** (People/Process), and capability assessments for
   **automation strategy, platform, hybrid cloud, and AI**.
3. **Ingest** the assessment outputs into DAV → evaluate UCs per pillar → gaps.
4. **Gap analysis + prioritization** — foundational leverage (what unlocks the most) ×
   business value / effort / risk — across all three pillars.
5. **Strategy + roadmap** — the deliverable: a sequenced plan to close gaps and realize
   the holistic vision. Governed (audit, tenancy) and tied back to the agreed outcomes.

It is **both** assessment and roadmap: we already run the assessments; DAV's new job is
to **consume their outputs, do the cross-pillar gap analysis, and build the strategy +
roadmap** to close the gaps and enable the larger vision.

## DAV's role / what this means to build

DAV is the **engine** that operationalizes this model: it ingests UC drivers and pillar
data, runs the gap analysis, maps UCs↔capabilities, and produces the roadmaps. Today it
implements the **Platform** pillar (AD mode). The other two pillars are **views over
mostly the same data**, gated primarily on **new ingestion** and pillar-specific
evaluation targets — not a new engine:

- Generalize the evaluation target: "spec" → **assessment target** (current-state vs
  target/reference), selectable per pillar.
- New ingestion methods: **assessment-output import** and **Value Stream Mapping**
  (and others as the assessment catalog grows).
- Pillar-specific UC taxonomy/profile so engagement findings aren't forced into the
  DCM/architecture vocabulary.
- A **report/export projection** — the client-facing deliverable.
- The bidirectional **UC↔capability map** (F5) as a cross-pillar legibility view.

See `active-work.md` for the in-flight feature list and `uc-driven-roadmaps-design.md`
for the projections model this generalizes.

## Adjacent strategy note

**AI capability/strategy** is both an assessment lens (above) and a standalone strategy
we need to develop deliberately — captured as a work item in `active-work.md`.

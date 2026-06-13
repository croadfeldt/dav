# DAV UX paradigm — persona-scoped lenses over constant objectives

## Context
DAV grew feature-by-feature (use cases, runs, results, architecture review, engineering
roadmap, assessments, catalog, improve, config). The domain shell (the app-wide IA —
left domain rail + top sub-tab strip + detail bulk, see `review-console-design.md`
§Navigation) organized those features, but a structural IA is not a *usage paradigm*. The
question that surfaced the gap — "do we still need the run selector at the top?" — was
really "what is a user oriented around?"

**Decision (Chris, 2026-06-11): the objectives are constant; the *persona* changes.
Persona is the scoping principle.** "Bingo, that's the scoping we need."

## The principle
One system of objectives (the domains / the analysis pipeline). Multiple **personas**,
each a **lens** = a characteristic **question** and the **projection** that answers it, over
the *same* underlying analysis. DAV's dual pipeline already encoded this once — one
analysis, two projections: the architect reads it as a gap analysis, the engineer as a
roadmap (see `project_dav_product_goal`). Persona-as-lens generalizes that across the
whole tool: not different tools, the same evidence read in different directions.

### Corollary — what the chrome is for
- **Engagement (project)** = the one truly global context → it earns the masthead.
- **Persona** = the active lens → the (generalized) focus switcher; an persona sets which
  domains it foregrounds + their order, the landing point, the language, and the default
  posture (author vs consume).
- **Run / analysis** = working context *inside* the analysis-touching lenses, **not** global
  chrome → the masthead run **selector** is removed; run selection lives in the Execution
  domain; the masthead instead carries engagement + persona (+ live run **status**, #112).
- **Domains** = the constant objective substrate beneath every persona.

Existing seeds to generalize, not reinvent: the **focus switcher** (Architecture/Assessment,
#101) is a two-value proto-persona; **view-mode** (edit/consume) is the posture dimension;
**RBAC** is permission — it *constrains* persona but does not *equal* it (persona is the
role-of-the-moment / intent, which one person may switch between).

## The persona model — {persona × question × projection × data × gap}
**The objective spine is a translation chain** (confirmed Chris 2026-06-11), and personas own
its stages or consume its outputs:

```
Outcome / Initiative  →  Use Cases  →  [analysis: gaps + capabilities]
        │                                        │
        │                          Architect: UC gaps → architecture / approach
        │                          Engineer:  architecture / approach → code architecture / approach
        └─ Customer (feasibility) ─┬─ Stakeholder (value) ── consume the outputs
```

Architect and Engineer are **different personas with similar content** — two sequential
*translation* steps over the same analysis, not one. Each persona = a question + the
projection that answers it:

| Persona | Stage / role | Question | Projection | What's missing |
|---|---|---|---|---|
| **Architect** | translates **UC gaps → architecture/approach** | "How does this demand reshape the architecture, and where are the gaps?" | gap analysis → architecture changes | core/strong |
| **Engineer** | translates **architecture/approach → code architecture/approach** | "What's the complete picture and the build order?" | exhaustive **capability map** + **roadmap** | **coverage/completeness signals** so it's *trusted* complete, not just *presented* complete |
| **Customer** | consumes (feasibility) | "Can we do this? How does the architecture enable it? What's missing?" | **outcome-framed feasibility narrative** | the **enablement/affirmative** projection (see below) |
| **Stakeholder** | consumes (**value**) | "What's the value and priority of the output?" | value / priority view of outcomes | a **value/priority projection** over outcomes (elicited, not hallucinated — cf. #F11) |
| **Assessor / consultant** | translates **assessment → findings/gaps** | "What did the assessment reveal, mapped to capabilities/gaps?" | findings → capability/gap mapping | assessment branch in progress (#102/#104) |
| **Operator / methodology owner** | curates the platform + reusable system | "Is everything configured correctly?" | config + curation surfaces | Config domain (built) |

**Outcome / Initiative is a first-class object above the UC** (confirmed): customers and
stakeholders orient on outcomes ("can we do real-time fraud scoring?"), which roll up Use
Cases (and/or Sets). New object — see Decision 2.

### The keystone gap: DAV is gap-rich, enablement-poor
DAV was born a *gap*-analysis engine, so it computes the **negative space** (what's missing)
far better than the **affirmative** (how the architecture *does* support an outcome). Yet the
customer lens needs both — "how does the architecture enable it?" is the *positive of a gap*.
The enablement projection is the **same data inverted** (the capabilities/spec-sections a UC
invokes that the architecture *satisfies*); it simply isn't surfaced today. Plus customers
think in **outcomes/initiatives**, a level above the individual UC, which DAV has no
first-class object for.

This table is DAV running a gap analysis **on itself** — which is the strongest validation
that persona-as-lens is the right frame: it makes the tool's own gaps legible exactly the
way it makes a customer's.

## Implications to build from (derive, don't bolt on)
- **Masthead** = engagement + persona (+ live run status, #112); **remove the run selector**.
- **Persona switcher** generalizes the focus switcher; per-persona domain order / landing /
  posture.
- **New projection backlog:** the enablement/affirmative map; an outcome rollup (object TBD).
- **Per-persona deliverable/export** — the client-facing report (#F12 in `active-work.md`)
  falls out of the customer lens.

## Resolved decisions (Chris, 2026-06-11)
1. **Persona set** — Architect, Engineer, **Customer** (feasibility), **Stakeholder** (value),
   Assessor, Operator. Architect ≠ Engineer (sequential translation stages, similar content);
   Stakeholder is distinct from Customer — Customer asks *can we / how / what's missing*,
   Stakeholder asks *what's the value*.
2. **Outcome object** — **YES, a first-class Outcome/Initiative above the UC.** It rolls up
   UCs (and/or Sets); the customer/stakeholder lenses orient on it. New modeling work.
3. **Switch vs assigned** — **Switchable, default tied to the RBAC role.** Generalize the
   focus switcher: derive the default persona from the user's role, let them switch.
4. **Posture coupling** — **Orthogonal.** Persona is a *consumer* lens (which projection you
   read); the edit/consume posture (view-mode) stays independent.

## Build order (derived from the paradigm)
1. **Outcome/Initiative object** above the UC (schema + rollup of UCs/Sets) — foundational for
   the Customer + Stakeholder lenses.
2. ✅ **Persona switcher** — DONE. `PERSONAS` map (above `switchView`) generalizes the focus
   switcher; `_persona` (localStorage `davPersona`), `_defaultPersona()` (RBAC-derived:
   assessment-only → Assessor, else Architect), `_applyPersona()` renders the rail to the
   persona's ordered domains via `_personaDomains()` + `renderDomainRail()`; masthead
   `#personaSel` dropdown. Switchable, orthogonal to view-mode.
3. ✅ **Masthead run selector removed** — DONE. The run is working context, not chrome:
   `#globalRunSel` retired → read-only `#rccName` status label; selection lives in
   Execution → Runs (`selectRunResult`). (Live run *status* pull-down = #112, still pending.)
4. **Enablement / affirmative projection** — invert the gap data into "how the architecture
   enables it" (the Customer lens's missing third).
5. **Value/priority projection** over outcomes for the Stakeholder lens (cf. #F11).

## Related
**`uc-scoped-evaluation-design.md`** — the consumer-side mechanics: scope = UC/Set → per-UC
result cache (fingerprinted, rebuilt on change) → derived outcome requirements + roadmap; run =
rebuild job; the masthead **freshness chip** (#112). This is where the run selector goes to die.
`review-console-design.md` §Navigation (the domain shell this rides on) · `holistic-vision.md`
(the three pillars — personas may map onto pillar consumers) · `uc-driven-roadmaps-design.md`
· `scope-and-bundles-design.md` · #101 (focus), #112 (masthead freshness), #F12 (report export).

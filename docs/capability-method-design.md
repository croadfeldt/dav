# UseCase → Domain-Driven Capability Method — adoption in DAV (#132)

**Status:** design pass (2026-06-12). Living doc — build to it, update after changes.
Source: `~/UseCase_to_Capability_Method.pdf` ("Mapping Use Cases to Domain-Driven Capabilities").
Pairs with `uc-driven-roadmaps-design.md` (the dual-roadmap requirements) + `capability-catalog-design.md`
(the catalog/taxonomy) + `customer-demand-dedup-design.md` (demand + matrix UI).

## The method (one line)
**Capabilities are the stable nouns; use cases are the moving target.** Trace each use case → role → goal
→ **capability (the hinge)** → bounded context → single strategic provider → reusable delivery. Dedupe to a
capability map; classify (Core/Supporting/Generic); decide a **disposition (R4)**; fund and own at the
capability level. Functional *and* non-functional needs (security, observability, compliance) map the same way.

## What DAV already does (≈70% — the method validates DAV's direction)
- **UC → capability mapping + bidirectional Cap Map** = the method's mapping chain + "trace both ways."
- **Capability catalog** (observed + curated, normalized to the DCM taxonomy, deduped by `cap_key`) =
  "single source of truth, one capability, one source."
- **`depends_on` + Foundational detection** = the dependency structure (#126 makes emission reliable).
- **Customer demand / distinct-customer importance** = the "capability-level funding / ROI" signal.
- **Dual roadmaps** (Architecture & Capability / Engineering) = the problem-space→solution-space split.
- **Personas** (Architect · Engineer · Operator · …) ≈ the method's **4 operating functions**
  (Capability/Product Owner · Capability Office/PMO · Architecture · Platform Engineering).
- **NFRs-as-capabilities** — DAV already treats infra-confidence + capabilities uniformly.

## The gaps to adopt (the new value), mapped to DAV
Each adds a small `capability_catalog` field + a lens/view; all reuse existing components.

1. **R4 Disposition (highest leverage).** Per capability decide **Reuse / Refurbish / Replace / Retire** on a
   2×2: **business/strategic fit** (high→low) × **technology fitness** (aligned/viable → constrained/weak).
   - Reuse = strategic & tech-aligned → adopt as the reusable component.
   - Refurbish = strategic & viable/gap-recoverable (or market-leading) → modernize & keep.
   - Replace = strategic but tech too constrained → rebuild as a new reusable capability.
   - Retire = not strategic, no growth path → sunset & consolidate.
   - **DAV:** `capability_catalog.disposition TEXT` (+ `strategic_fit` / `tech_fitness` enums driving it) →
     a **Disposition matrix view** (reuse the `renderCapMap` / 2b-ii grid: rows = capabilities, the R4
     quadrant as the cell). Turns the Cap Map from "what's demanded" into "what we *do* about it" — the
     missing actionable-roadmap layer. Ties to the Engineering roadmap's enablement gap.
2. **Core / Supporting / Generic** (DDD strategic design) — aims investment (best engineers on Core; buy
   Generic). `capability_catalog.subdomain TEXT` (NOT `classification` — that's a taken column, see m-i
   note) + a filter/lens on the Cap Map + Engineering view.
3. **Bounded context owner + single strategic provider.** DAV has `domain`/`domain_prefix`/`namespace`
   (≈ the bounded context). Formalize: `bounded_context` (the owning DDD context) + `strategic_provider`
   (the one team/platform that delivers it reusably) → enforce "one capability, one source."
4. **Explicit Role → Goal layer.** DAV's UC `scenario.actor` *is* the Role; Goals are implicit. Make goals
   explicit so the trace is UC → role → **goal** → capability. Lands in the **engine analysis schema/prompt**
   (the model emits role→goal→capability, not just capabilities) — fold into #126 (capability emission tune).
5. **Capability-level funding / ROI.** Express demand/ROI/roadmap at the capability (not the UC/project), so
   "investment compounds instead of forking." DAV's distinct-customer demand already feeds this; surface a
   per-capability demand rollup (UCs → capability → customers).

## Governing principles (DAV already holds most; keep enforcing)
Single source of truth (one provider per capability) · capability-level funding · trace both directions
("Do No Harm" — every legacy function accounted for; DAV's coverage analysis) · deploy-agnostic delivery ·
contracts at the seams · strategic-only enhancement (new investment flows to strategic capabilities).

## Operating model → DAV personas
| Method function | DAV persona / surface |
|---|---|
| Capability / Product Owner (proposes capabilities, owns ROI) | Architect + customer demand |
| Capability Office / PMO (dedupes, targets single source) | the catalog dedup + disposition review |
| Architecture (locate in contexts, set disposition, contracts) | Architecture roadmap + this method's views |
| Platform Engineering (builds/runs reusable components) | Engineering roadmap |

## Sliced plan
- **m-i — R4 disposition + Core/Supporting/Generic** ✅ **SHIPPED 2026-06-12.** Catalog gained
  `disposition` + `strategic_fit` + `tech_fitness` + `subdomain` columns. Editor: a compact
  `subdomain` select (Core/Supporting/Generic) + `fit` × `tech` driver selects that **suggest** the
  disposition (the 2×2 below; only auto-fills an *empty* verdict so an explicit architect choice is never
  overwritten) + an overridable `disposition` select. Catalog rows show **dual-labelled badges** —
  disposition `Reuse ·Tolerate` etc. (color-coded: Reuse=green, Refurbish=blue, Replace=amber,
  Retire=red) + subdomain (Core=accent, Supporting=blue, Generic=faint). Signal-led per
  [[feedback_dav_signal_over_noise]]: the verdict reads first, drivers are secondary.
  - **fit × tech → suggested disposition:** high+aligned→Reuse · high+constrained→Refurbish ·
    low+aligned→Reuse (Tolerate — keep, don't invest) · low+constrained→Retire. (Replace is the manual
    override for high-fit-but-too-constrained; binary tech can't distinguish viable from unsalvageable.)
  - **NB — column is `subdomain`, not `classification`:** `capability_catalog.classification` already
    exists (data sensitivity, `NOT NULL DEFAULT 'public'`, written by `assessment_ingest`). The DDD
    strategic type is a distinct concern → its own `subdomain` column. Don't conflate them.
  - **Disposition Board** ✅ also shipped 2026-06-12: the Catalog has a **List ⇄ Board** toggle (mirrors
    the Customers List⇄Matrix pattern). The Board groups capabilities into the four R4 verdict columns —
    **Undecided** (the work queue, first) · Reuse·Tolerate · Refurbish·Invest · Replace·Migrate ·
    Retire·Eliminate — color-coded, each chip click-to-edit with subdomain + driver chips. This is the
    "what we *do* about it" decision surface (`_renderCatalogBoard` / `#catBoard`).
  - Remaining for the method's full vision: a Cap-Map/Engineering **subdomain lens** (m-ii) + optionally a
    true 2×2 fit×tech quadrant placement (the Board groups by the verdict, which also handles manually-set
    dispositions that have no drivers — the more robust default).
- **m-ii — Core/Supporting/Generic + disposition lens** ✅ **SHIPPED 2026-06-12.** The catalog's
  `subdomain` + `disposition` now ride the analysis views via `_catalog_meta_map` (cap_key → meta,
  catalog = single source): the **Engineering capability-density list** shows the same dual-labelled
  badges as the catalog; the **Cap Map matrix** gets a thin disposition-colored **underline** on each
  capability column header (+ a one-line legend, subdomain on hover) — the R4 verdict at a glance without
  crowding the dense grid. Both `/api/analysis/capability-density` + `/api/analysis/uc-capability-map`
  return `subdomain`/`disposition` per capability. Remaining (optional): a filter/toggle to *isolate* a
  subdomain or disposition; deferred until there's demand (signal-over-noise — the always-on lens is
  enough for now).
- **m-iii — Bounded context + strategic provider** ✅ **SHIPPED 2026-06-12.** Catalog gained
  `bounded_context` + `strategic_provider` columns (CatalogIn + INSERT/UPDATE); editor has a two-field
  ownership row; the catalog list shows `🏷 provider · ⬡ context` per capability — formalizing "one
  capability, one source." Remaining (optional, deferred): active **"single source" enforcement** (flag a
  bounded context whose capabilities name *different* providers, or capabilities missing a provider) — a
  catalog lint/lens; build when there's signal to act on, per signal-over-noise.
- **m-iv — Role → Goal** decomposition in the engine schema/prompt — **HELD**: couples to #126 (capability
  emission tune) + the held stage-2 prompt (#93), which Chris wants A/B-controlled. Do not wire without that.
- **m-v — Capability-level funding rollup** ✅ **SHIPPED 2026-06-12.** `/api/analysis/capability-density`
  now returns `distinct_customers` per capability — the union of distinct customers across the UCs that
  demand it (reuses the demand log's free-text `customer`, the same metric as the UC list, so it's
  scope-correct). The Engineering capability list shows a **👥 N** funding badge. Ties the method's "fund
  the capability, not the UC" to DAV's anti-poisoning distinct-customer demand. Optional next: roll the
  same number onto the Cap Map / disposition Board, and weight the suggested disposition by demand.

**#132 status (2026-06-12): m-i, m-ii, m-iii, m-v SHIPPED. m-iv HELD** (engine role→goal couples to #126 +
the A/B-gated stage-2 prompt #93 — do not wire without Chris's A/B).

Each slice ships behind lint/e2e + the validation-currency rule (#128), and updates this doc +
`capability-catalog-design.md`.

## Open decisions
- **R4 drivers automatic or manual?** Manual (architect sets disposition) first; later derive a *suggested*
  disposition from signals (demand, readiness, infra-confidence, staleness) for the architect to confirm —
  the hybrid LLM+human pattern DAV already uses.
- **Classification source** — human, taxonomy-derived, or LLM-suggested-then-confirmed.
- **Does the engine emit goals**, or are they derived in the console from the UC scenario? (Engine emission
  is richer but couples to #126 + the held stage-2 prompt.)
- Relationship to the **(customer × project) matrix** — disposition/classification are capability-level (not
  customer-scoped), so they're a *capability* lens, orthogonal to the customer axis. Confirm.

## Standards & best-practices alignment (2026-06-12)
The deck's method is a synthesis of established frameworks. Mapping DAV's concepts to their canonical names
buys credibility + interop. DAV is well-aligned; the gaps below name the standard to adopt.

| DAV / method concept | Industry standard it maps to | Adopt |
|---|---|---|
| Capability map (UC→capability, dedup, levels) | **BIZBOK** (Business Architecture Guild) capability map; **ArchiMate** Capability + Realization; **TOGAF** Capability-Based Planning | name it a *capability map*; make capabilities **MECE** (mutually-exclusive, collectively-exhaustive) + **leveled** (L1/L2/L3 hierarchy); optional ArchiMate export |
| R4 Disposition (Reuse/Refurbish/Replace/Retire) | **Gartner TIME** (Tolerate / Invest / Migrate / Eliminate) — *the* standard app-portfolio 2×2; AWS **6 R's** (migration) | map R4 ↔ TIME explicitly (Reuse≈Tolerate/keep, Refurbish≈Invest, Replace≈Migrate, Retire≈Eliminate); surface the TIME label for enterprise familiarity |
| Core / Supporting / Generic | **DDD subdomains** (Evans/Vernon) — Core/Supporting/Generic *is* DDD; **Gartner Pace-Layering** (Systems of Record/Differentiation/Innovation); **Wardley evolution** (genesis→custom→product→commodity) | keep the DDD terms; optionally add a **Wardley evolution** axis to inform build-vs-buy + the R4 quadrant |
| Bounded context owner + single strategic provider | **DDD bounded contexts + context mapping** (ACL/OHS/Customer-Supplier); **Team Topologies** (capability-as-a-product, stream-aligned/platform teams); **Conway's Law** | "single source" = one owning team/context per capability — a Team-Topologies platform/stream-aligned team |
| Capability-level funding (vs project) | **SAFe Lean Portfolio Management** ("fund value streams, not projects"); **Project to Product** (Kersten, Flow Framework); Beyond Budgeting | frame DAV's demand→funding as value-stream/capability funding |
| Role → Goal (the "what, not how") | **Cockburn** use-case goal levels; **UML** actors+goals; **Jobs-To-Be-Done**; User Story Mapping (Patton) | make goals explicit + outcome-oriented (JTBD) |
| NFRs are capabilities (security, observability, compliance) | **ISO/IEC 25010** quality model; well-architected frameworks | treat quality attributes as first-class capabilities (DAV already does via infra-confidence) |
| Trace both ways / Do-No-Harm | **ISO/IEC/IEEE 42010** (architecture description) + requirements traceability | keep the bidirectional UC↔capability↔delivery trace; it's an auditable traceability matrix |
| Operating model (4 functions) | **TOGAF ADM** roles; **IT4IT** value chain; capability-office/PMO patterns | DAV personas already approximate these |

**Net recommendation:** keep DAV's vocabulary but **dual-label** the two most enterprise-recognizable pieces —
**R4 ↔ Gartner TIME** and **Core/Supporting/Generic ↔ DDD subdomains** — and add **capability leveling +
heat-mapping** (BIZBOK: capabilities heat-mapped by demand × maturity, which DAV's demand + readiness scores
can drive directly). A **Wardley evolution** signal is an optional, high-insight addition to the disposition
decision. These are additive metadata, not a re-architecture.

## Related
`uc-driven-roadmaps-design.md` · `capability-catalog-design.md` · `customer-demand-dedup-design.md`
(matrix UI #130) · `#126` (engine capability emission). Tracked as **#132**.

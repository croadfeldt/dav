# DCM / UDLM — The Thesis

_The core argument the whitepaper defends, in one place. Everything in the paper is in service of this. (2026-06-16)_

---

## Thesis (one sentence)

**Infrastructure lifecycle is data — and it should be *open, versioned, machine-native* data, captured in a vendor-neutral model of a resource's four states (intent → request → realized → discovered) — because only then does the broken information loop close and the high-value reasoning built on top of it (capability coverage, maturity, gaps, roadmaps, sourcing) become reproducible, comparable, and shared, instead of trapped in bespoke integrations, slide decks, and proprietary tools.**

## The chain of reasoning (each link is a section of the paper)

1. **The problem is the data, not the automation.** Every stakeholder in a resource's life integrates bespoke; what was *intended* vs. *requested* vs. *realized* vs. *discovered* lives in different systems, different formats, or nowhere. There is no common language, so drift, provenance, and audit are all manual. (§1)

2. **A standard fixes this only as a *data-model contract*, not an application.** UDLM is wire-compatible, versioned, conformance-testable, and extensible without forking — the same move OSCAL made for compliance, applied to infrastructure lifecycle. Contracts, not code. The control plane stays domain-ignorant; any stakeholder plugs in through the contracts. (§2–3)

3. **Neutrality is load-bearing.** A contract becomes a *standard* — fundable, joinable, adoptable — only if it is vendor-neutral; a single vendor's tool is not. Hence open governance, the spec/implementation separation (DCM *realizes* UDLM; DAV *validates DCM* and *represents its data in UDLM*; adopters and integration partners are not owners), and the CNCF destination. (§6)

4. **Machine-native data makes machine-native reasoning possible — and keeps AI governed.** Once lifecycle data is in a common shape, capability / maturity / gap / roadmap reasoning becomes a reproducible query rather than a slide deck; and AI sits *on top of* a deterministic, policy-governed core rather than replacing it. (§3–5)

5. **It is already real and converging.** Independent enterprise demand for the same thing; a roadmap the implementation generated about the standard itself; a cross-architecture comparison only a shared model made possible; a running sovereign-workload rehydration sample; and active co-engineering toward one shared platform. The community exists in embryo. (§5–6)

## What it is NOT (the disciplined negatives)

- **Not a product.** The reference implementation proves the contract; it is one implementation among possible others.
- **Not an enterprise-architecture framework** (ArchiMate/TOGAF) — it is lighter and machine-native, modeling lifecycle and state, not the whole enterprise.
- **Not a portal or scorecard** — those are *surfaces* that can render UDLM data; UDLM is the lifecycle data beneath them.
- **Not "AI for infrastructure"** — AI is an enhancement on a deterministic, governed core, not the value proposition.

## The single sentence to remember

*Make infrastructure lifecycle — and the capability and maturity reasoning built on it — machine-native, open, and governed.*

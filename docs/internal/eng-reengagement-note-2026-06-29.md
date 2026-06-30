# DCM/UDLM — engineering re-engagement note (for the Monday meeting)

_Draft for Chris. Private (docs/internal). Purpose: re-sync the dcm-project engineering team and unblock the
merge queue. Pair with the roadmap (`dcm-udlm-6-week-demo-roadmap.md`)._

## Where we are
The spec work this cycle is **merged upstream** (`croadfeldt/udlm` + `croadfeldt/dcm` `main`) and **synced to
the downstream `dcm-project` PRs**, but **the downstream PRs are not merged** — `dcm-project/udlm main` is
still empty and `dcm-project/dcm` has ~30 open PRs. **The bottleneck is review/merge throughput, not authoring.**
"Built on UDLM" is currently a claim against open branches; landing them makes it true.

## The ask (one sentence)
Review and merge the downstream PR set in dependency order so the 6-week demo (and WS-I UDLM-conformance)
builds on *landed canon*, not moving branches.

## Suggested merge order (dependency-first)
1. **Foundations** — `u/u1-foundations`, `u/u2-foundations-layering`, `u/u3-foundations-lifecycle`
2. **Registry** — `u/u16-registry-framework` (#30), `u/u17-registry-resource-types` (#31) — the data model
3. **Contracts** — `u/u6-provider-policy` (#20), `u/u11-governance-authn` (#25), event-catalog/wire
4. **Entities** — `u/u9-entities-relationships` (#23), `u/u10-entities-services-knowledge` (#24)
5. **Governance / lifecycle / observability** — the remaining `u/u12…u15`
6. **DCM** — `pr/da13-adr` (ADRs 001–022, #78), `pr/da4-control-plane` (#69), `pr/da14-architecture-standards` (#98)

## Decisions to close at the meeting
- **#232 Service taxonomy** — `dcm-project/dcm#99` is posted (6 decisions): Service=act/Resource=thing;
  `service_provider → Resource Provider`; realize/Realized; **Composite/Atomic Resource**; we absorb
  Service-as-act + Infrastructure Platform. Get agreement → encode as ADRs/DecisionRecords.
- **Authz substrate** — Keycloak=authn confirmed; **authz open** (Alterino vs Kessel vs OPA-native). Pick.
- **Composite Resource offering** as a managed catalog-item class — `dcm-project/enhancements#66` (refs
  `croadfeldt/udlm#21`); intake for a KEP if there's interest.

## Two reconciliation flags (before merging those slices)
- The `kubernetes-compatibility` 4-line terminology port should land on `pr/dsp5-spec-integration`.
- **`pr/dx1-future-features` should be closed/trimmed** — it republishes the `docs/future-features/` folder
  that was just removed upstream (croadfeldt/dcm #21). Merging as-is re-introduces it.

## What's ready to show
- Roadmap §1–§15 (goals from the Jun 26 meeting; real-mechanics; bare-metal bootstrap; approved-architecture
  catalog; merge work WS-J; architecture-gaps §12).
- Public whitepaper updated (kinds-vs-capabilities, trust-broker, DecisionRecord, Composite Resource).
- Strongest proof points: a regulated FSI independently validated UDLM's layering = their base/user config
  model; the demo runs on **real mechanics + a real UDLM data model** (WS-I), not a Summit approximation.

_Note: posting anything to dcm-project / merging croadfeldt needs Chris's explicit go (standing rule)._

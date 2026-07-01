# DAV self-evaluation corpus — Findings & Resolutions

DAV dogfoods itself: these use cases describe **DAV's own** desired architecture so DAV can run
gap analysis against its own spec (`dav/docs/findings-resolution-design.md`,
`udlm/entities/knowledge-family.md` §4.5). They are the executable form of the operator goal —
**"submit changes to the architecture via ADRs"** — and the validation backstop for the
Findings & Resolutions capability (task #197, #184 dogfooding).

The architecture under test is DAV's own design: a finding → a proposed resolution (an ADR /
DecisionRecord) → **validated** against use cases → accepted → it **drives the change** (a
spec/enhancement PR). DAV is where ADRs are authored, validated, and submitted; DCM consumes them
for change-tracking + drift (`uc-fr-006`).

## Domain: `findings_resolution`

| UC | Loop step | Covers (requirements) |
|----|-----------|-----------------------|
| `uc-fr-001` | FIND — surface & anchor a finding | RF-1/2/4, RO-1 |
| `uc-fr-002` | ENABLE — propose the change as an ADR | RE-1/3 |
| `uc-fr-003` | ENABLE — validate the change before acceptance (the gate) | RE-6/7 |
| `uc-fr-004` | SUBMIT — accepted ADR drives a spec/enhancement change | RE-5 (primary goal) |
| `uc-fr-005` | ORGANIZE — one ADR resolves a class; auto-match & cite | RE-4/8, RF-3 |
| `uc-fr-006` | CONSUME — decision drift re-validation (DCM tracking) | OBSERVED re-validation |
| `uc-fr-007` | The WHY is queryable end-to-end (`depends_on` → ADR) | RE-3/8, answers the depends_on-WHY feedback |

All are `analytical` / `gate_class: advisory` — they evaluate DAV's architecture for gaps, they don't
gate CI. Grouped in the **"Findings & Resolutions (self-eval)"** scoping set.

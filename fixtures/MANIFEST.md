# Fixture manifest — what is seeded, and why

_This is the review artifact. Everything else in `fixtures/` is mechanical; the
claims below are judgement calls. Each seeded hole asserts **"a correct platform
spec would have to specify this"**. If a claim is wrong, DAV gets scored against my
mistake — permanently, in the fixture everything else is measured by. Review the
claims, not the YAML._

Design and rationale: `../docs/validation-fixture-suite-design.md`
(dav#82 — a frozen corpus with ground truth, so DAV can be measured for accuracy
rather than only for self-consistency).

The platform is **Halyard**, a deliberately small synthetic control plane. It is
not a model of UDLM or DCM and must never be read as one. `FIX-*` capability ids
are fixture-local and must never collide with the real catalog.

## Seeded holes — DAV MUST find these (recall)

| id | where | the claim |
|---|---|---|
| `FIX-AUDIT-001` | `10-refusal-contract.md` §4 | §4 *Auditable* is an empty section. Nothing requires a refusal record, names its fields, or says where it is written — a conforming implementation could refuse silently. **Claim: a refusal contract that cannot be audited is incomplete.** |
| `FIX-NONLEAK-001` | `10-refusal-contract.md` §3 | §3 *Non-leaking* is empty. No rule forbids echoing the protected resource's attributes, so a refusal could disclose exactly what a masked projection exists to hide. **Claim: a refusal that may leak the protected thing defeats the control.** |
| `FIX-PARTIAL-WARN-001` | `10-refusal-contract.md` §5 | The partial-fulfilment *behaviour* is specified and correct; the *surfacing* is not. `partial_failures` is a field, not a signal — a consumer can miss it and believe their intent was fully satisfied. **Claim: an intent that could not be fully satisfied must be surfaced as a warning naming what was refused, why, and how to resolve it.** |
| `FIX-DEPS-001` | `10-refusal-contract.md` §5 | Silent on dependent members: nothing stops realizing B when its hard dependency A was just refused, producing a resource pointing at nothing. **Claim: a broken dependency is a FAILURE, not a warning — hence `not_supported`, unlike the independent-member case.** |
| `FIX-ATOMIC-001` | `10-refusal-contract.md` §5 | No way for a consumer to declare all-or-nothing semantics. Best-effort is the correct *default*; the gap is the absence of an opt-in. **Claim: a consumer whose requirement is transactional must be able to say so.** |
| `FIX-NONPERSIST-001` | `30-credentials.md` | "Handling of a rejected body" is empty. The refusal is specified; non-retention of the transmitted secret is not. **Claim: refusing an inline secret while logging it is not a refusal in any useful sense.** |
| `FIX-QUOTA-001` | `50-quota.md` | "Enforcement point" is empty. Allocation and `QUOTA_EXCEEDED` exist, but not *where* consumption is checked and committed. **Claim: without a defined enforcement point, concurrent intents can both pass a check only one has headroom for.** |

## Controls — DAV MUST NOT report these (precision)

Deliberately **complete** areas. A gap reported against one of these is a false
positive, and false positives are the failure mode we currently cannot see: tonight's
ensemble bias produced *more* gaps as sample count rose, which without a precision
measure reads as thoroughness.

| id | where | why it is complete |
|---|---|---|
| `FIX-TYPED-001` | `10-refusal-contract.md` §1 | Closed `reason_code` set, stability rule, and `TENANCY_DENIED` explicitly distinguished from `NOT_FOUND`. |
| `FIX-ACTIONABLE-001` | `10-refusal-contract.md` §2 | Every refusal carries `remediation` naming the mechanism; the grant mechanism it points at is defined in `20-tenancy.md`. |
| `FIX-PROV-001` | `40-providers.md` | Eligibility is a superset test; tie-break is preference order then lexical, so selection is a total, reproducible function. |
| `FIX-BIND-001` | `60-bindings.md` | Declaration requirement, `BINDING_UNDECLARED`, remediation, and validation-before-realization are all stated. |

## The deliberate asymmetry (Chris's ruling, 2026-07-27)

`FIX-DEPS-001` expects **`not_supported`**; every other gap expects
`partially_supported`. The line is **failure vs warning**:

- partial fulfilment of **independent** members is *correct behaviour* — it needs a
  warning, not a refusal (`FIX-PARTIAL-WARN-001`)
- partial fulfilment that **breaks a dependency** is a *failure* — the platform has
  created state that cannot function (`FIX-DEPS-001`)

If DAV cannot tell those apart, the roadmap it produces will treat "surface this
better" and "stop producing broken state" as the same size of work.

**I had this wrong first.** I originally seeded best-effort partial application
itself as the defect, expecting `not_supported`. Chris ruled otherwise: realizing
the valid subset and refusing the violating member IS the system working as
expected — which matches Kubernetes and Terraform, where a declarative control
plane converges what it can. That correction is why `FIX-WHOLE-001` is now a
CONTROL. It also surfaced the more interesting distinction: this is the
**intent-requirement** side of the platform, where prior work has all been the
operational side.

## Coverage against ADR-003

One case per refusal-contract element, so the derived-verdicts proposal
(`docs/derived-verdicts-design.md`) is testable element by element rather than only
in aggregate.

| element | state | UC |
|---|---|---|
| typed | complete (control) | `fx-capability-unmet-refused` |
| actionable | complete (control) | `fx-tenancy-audit-missing` |
| non-leaking | **seeded hole** | `fx-projection-write-refused` |
| auditable | **seeded hole** | `fx-tenancy-audit-missing` |
| whole | **seeded contradiction** | `fx-partial-intent-wholeness` |

## What would make me wrong

Stated plainly so it can be checked rather than trusted:

- **If a real platform legitimately leaves audit records to an operator concern**,
  `FIX-AUDIT-001` is not a spec gap and DAV is being penalised for correctly not
  reporting it.
- **If best-effort partial application is a defensible product choice** for large
  intents, `FIX-WHOLE-001` is a design disagreement, not a hole, and expecting
  `not_supported` is too strong.
- **If `FIX-QUOTA-001` reads as an implementation detail** rather than a spec
  obligation, the recall target is unfair.

I believe all three are genuine spec obligations — a refusal you cannot audit, that
leaks the protected resource, or that half-applies an intent, is not a refusal that
a consumer can rely on. But these are the three most arguable claims here, and they
are where I would look first if the fixture starts producing results that feel wrong.

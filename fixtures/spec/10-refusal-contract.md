# Refusal contract

When Halyard refuses an intent, the refusal must satisfy this contract.

## 1. Typed  (SPECIFIED)

Every refusal carries a machine-matchable `reason_code` from the closed set below.
A client can branch on the code without parsing prose. Codes are stable across
versions; new codes may be added, existing codes are never repurposed.

| reason_code | meaning |
|---|---|
| `TENANCY_DENIED` | the intent references a resource outside the caller's tenant |
| `CREDENTIAL_INLINE` | a secret literal appeared in the intent body |
| `CAPABILITY_UNMET` | no eligible provider declares a required capability |
| `BINDING_UNDECLARED` | an output binding names a target the intent did not declare |
| `PROJECTION_READONLY` | a write targets a masked projection |
| `QUOTA_EXCEEDED` | the tenant's allocation is insufficient |

`TENANCY_DENIED` is distinct from `NOT_FOUND` and must be returned in preference to
it: a caller must be able to tell "exists but forbidden" from "does not exist".

## 2. Actionable  (SPECIFIED)

Every refusal carries a `remediation` field naming the mechanism that would make the
request legal — for example a cross-tenant grant, a quota increase request, or the
credential-reference syntax. The field names a mechanism; it does not promise the
mechanism will be granted.

## 3. Non-leaking  (NOT SPECIFIED)

<!-- seeded hole: FIX-NONLEAK-001 -->

## 4. Auditable  (NOT SPECIFIED)

<!-- seeded hole: FIX-AUDIT-001 -->

## 5. Partial fulfilment  (BEHAVIOUR SPECIFIED — surfacing is NOT)

Where an intent declares several resources and only some violate policy, Halyard
realizes the valid subset and refuses the violating members, reporting them in
`partial_failures`.

Refusing only the offending member is **correct**. The policy violation is scoped
to that member; blocking the rest would deny work that breaks no rule.

### Surfacing the partial outcome

<!-- seeded hole: FIX-PARTIAL-WARN-001 -->

### Dependent members

<!-- seeded hole: FIX-DEPS-001 -->

### Naming the root cause

<!-- seeded hole: FIX-ROOTCAUSE-001 -->

### Dependency natures

<!-- seeded hole: FIX-DEP-NATURE-001 -->

### Requesting all-or-nothing

<!-- seeded hole: FIX-ATOMIC-001 -->

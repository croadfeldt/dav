# Placement and residency

An intent may carry a `residency` constraint naming a jurisdiction. Providers declare
their `region` at registration.

## Provider region declaration  (SPECIFIED)

Every provider registration carries a `region` field from the closed jurisdiction
list. A registration without one is rejected. The region is immutable after
registration; relocation requires a new registration.

## Residency enforcement point

<!-- seeded hole: FIX-RESIDENCY-001 -->

The `residency` constraint field exists on the intent schema.

## Placement explanation

<!-- seeded hole: FIX-PLACE-EXPLAIN-001 -->

Selection among eligible providers follows the deterministic order defined in
40-providers.md.

# Quota and allocation

Each tenant holds an allocation per resource class. Realization consumes allocation;
release returns it.

## Enforcement point

<!-- seeded hole: FIX-QUOTA-001 -->

The `QUOTA_EXCEEDED` reason code exists in the refusal contract for this case.

# Operation audit

## Realization audit  (SPECIFIED)

Every realize, update, and teardown operation appends an audit record naming the
caller identity, the tenant, the resource, the operation, and the outcome. Records
are append-only and carry a monotonic sequence number per tenant.

## Read and projection access

<!-- seeded hole: FIX-AUDIT-READ-001 -->

Masked projections (20-tenancy.md) are the read surface for cross-tenant data.

## Audit integrity

<!-- seeded hole: FIX-AUDIT-INTEG-001 -->

Audit records are retained for the tenant's configured retention window.

# Tenancy

Every resource belongs to exactly one tenant. A tenant boundary is a hard isolation
boundary: an intent submitted by tenant A may reference only resources owned by
tenant A, unless a **cross-tenant grant** exists.

## Cross-tenant grants

A grant is declared by the owning tenant, names the grantee tenant and the specific
resource, and carries an expiry. Admission checks for a matching live grant before
allowing a dependency edge to cross a tenant boundary.

Absent a grant, admission refuses with `TENANCY_DENIED` (see the refusal contract)
and no dependency edge is created.

## Masked projections

A tenant may expose a **masked projection** of a resource to another tenant: a
read-only view with designated fields redacted. Projections are read paths only.
A write targeting a projection is refused with `PROJECTION_READONLY`.

## Grant lifecycle

<!-- seeded hole: FIX-GRANT-EXPIRE-001 -->

Cross-tenant grants are created by the granting tenant's administrator.

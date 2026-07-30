# Lifecycle: update and teardown

## Teardown ordering  (SPECIFIED)

Teardown of a multi-resource intent proceeds in reverse dependency order: no
resource is torn down before every resource depending on it is torn down. A
teardown interrupted mid-sequence resumes from its recorded position.

## Consumer drain

<!-- seeded hole: FIX-DRAIN-001 -->

Teardown of a resource with active consumers proceeds per the ordering above.

## Update convergence for bindings

<!-- seeded hole: FIX-UPDATE-CONV-001 -->

An in-place update may change a resource's published outputs (60-bindings.md).

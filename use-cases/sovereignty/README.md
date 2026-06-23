# DAV self-gating sovereignty use cases (#184)

DAV applies the same sovereignty model it espouses to **its own** data model. These mirror the DCM
sovereignty UCs (`dcm/dav/use-cases/sovereignty/`) but are DAV-namespaced (`uc-dav-sov-*`,
`dav-self/sovereignty/*`) so DAV can ingest both the DCM set (architecture-under-eval) and this set
(self-eval) without uuid collisions. Run them against DAV's own spec to gate future DAV changes.

Model: `docs/sovereignty-coequal-entity-model.md`. Customer↔tenant are co-equal; the relationship +
data live in the tenant; only a bare identity anchor is shared (RBAC binds it).

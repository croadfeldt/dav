# Seed data

Vendored snapshot of `dcm/taxonomy/DCM-Taxonomy.md`, used by `capability_catalog.seed_dcm_taxonomy()` to populate `capability_taxonomy_terms`/`capability_aliases` (family=dcm, global, CANONICAL) on startup. Idempotent + re-seedable; the catalog back-fill loop evolves it from here. Refresh by re-copying the source taxonomy.

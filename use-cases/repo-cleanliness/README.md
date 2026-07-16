# Repo Cleanliness scoping sets

Two DAV scoping sets that gate repository/model cleanliness — one for **UDLM** (`udlm/`) and one for **DCM** (`dcm/`) — same nine questions, scoped per repo so DAV ingests them as two distinct sets without uuid collision.

The nine dimensions: single-source · consistency · standards-documented · data-pipelines-complete · boundary-respected · provider-neutral · correctly-scoped-docs · referential-integrity · versioning-discipline. Grounded in ADR-008 (boundary), SPEC-DESIGN §33 (single-source) / §34 (scoping) / §35 (provider-neutral), the standards-adoption register + disposition, and the produce/consume rule.

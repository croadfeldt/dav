# Capability Catalog ↔ Taxonomy — design

_Foundational keystone (the dependency that's blocked a trustworthy roadmap: capabilities
are free-form strings today with no controlled vocabulary). Design agreed with Chris
2026-06-08. Build to this. See `holistic-vision.md`, `active-work.md` (F7),
`uc-driven-roadmaps-design.md`._

## The model: a mutually-reinforcing pair (not a one-way projection)

- **Taxonomy = normalization authority.** Defines *how* to name/classify capabilities
  per subject matter — the controlled vocabulary + normalization rules. Authoritative on
  **form**. Source: `dcm/taxonomy/DCM-Taxonomy.md` (DCM is the platform superset; domains
  = automation, hybrid-cloud, AI, …). **Known to be incomplete.**
- **Catalog = living inventory.** Independent; the source of truth for *what capabilities
  actually exist / are needed*, accreting from assessment ingestion, UC analysis, and
  engagements. Authoritative on **substance**.
- **Bind (catalog → taxonomy):** every catalog entry is **normalized to a taxonomy term**
  using that subject matter's rules.
- **Back-fill (catalog → taxonomy):** when the catalog encounters a real capability the
  taxonomy can't normalize, that is a **taxonomy gap** → the catalog **proposes** the
  missing term → curation accepts → the taxonomy becomes more complete. The catalog
  *drives* taxonomy completeness.

> **Recursive insight:** this is DAV's own gap-analysis loop applied to its vocabulary —
> taxonomy = "spec", catalog = "reality", unnormalizable entries = "gaps" that back-fill
> the spec. Same hybrid LLM-proposes / human-curates pattern, dogfooded on DAV itself.

Ensuring taxonomy completeness is therefore **both** an ongoing back-fill-driven process
**and** a candidate one-time audit of the current `DCM-Taxonomy.md` for obvious gaps.

## Pillar namespacing
Catalog entries carry a **pillar** (platform | people-process | enablement) so a platform
capability ("CI/CD pipeline") never conflates with a process capability ("release
approval") or an enablement capability ("operator runbook + training") in cross-UC
aggregation. The DCM taxonomy seeds the **platform** namespace; people-process and
enablement get their own (seeded later from their own references/VSM).

## Schema sketch (proposed — react before migration)

```
capability_taxonomy_terms          -- structured taxonomy (seeded from DCM-Taxonomy.md, extensible)
  id, term (canonical), definition,
  pillar, subject_matter/domain,    -- platform/automation, platform/hybrid-cloud, …
  parent_id,                        -- hierarchy: DCM superset → domain → term
  normalization_rules,              -- how to normalize items for this subject matter (text/json)
  source ('dcm-taxonomy'|'backfill'),
  status ('canonical'|'proposed'),  -- 'proposed' = a back-fill candidate awaiting curation
  provenance, ts

capability_catalog                  -- independent living inventory (the keystone)
  id, name, description,
  pillar, domain,
  taxonomy_term_id (nullable),      -- the normalized binding
  normalization_status ('normalized'|'proposed-taxonomy-gap'|'unmapped'),
  source ('assessment'|'uc-analysis'|'manual'),
  evidence/provenance,
  project_id,                       -- tenancy from birth
  canonical bool, ts
```

- `normalization_status = 'proposed-taxonomy-gap'` ⇒ create/point to a
  `capability_taxonomy_terms` row with `status='proposed'` ⇒ curation accepts ⇒ term
  becomes `canonical`, catalog entry becomes `normalized`.
- **Canonicalize existing data:** the free-form `uc_capabilities.capability_id` strings
  resolve to `capability_catalog` entries (the synonym-miscount fix). Likely a
  resolver/alias mechanism (string → catalog id), itself a hybrid propose/curate step.

## Taxonomy structure (verified 2026-06-08 via `capability_taxonomy.py`)
`dcm/taxonomy/DCM-Taxonomy.md` parses cleanly into the full normalization machinery:
- **42 domains** ← Part 4 "Capability Domain Prefixes" (IAM, CAT, REQ, PRV, LCM, POL,
  DRF, OBS, …). These are the catalog's **domain namespace**; DCM capability IDs already
  use them (`PRV-007`, `OBS-008`).
- **164 canonical terms** ← Part 1 "### … Terms" tables (the vocabulary to normalize onto).
- **11 anti-aliases** ← Part 2 Anti-Vocabulary (`avoid → use-instead` = the normalization
  rules).

**Layering clarified:** **automation / hybrid-cloud / AI are assessment *ingestion lenses***;
their findings **normalize onto the DCM taxonomy** (Part 4 domains + Part 1 terms) — which
is exactly why a *generalized DCM strategy is the superset*. **Strategic kicker:** back-fill
means real consulting engagements **feed and complete the DCM taxonomy/strategy itself** —
field work makes the product more complete.

## UDLM CONFORMANCE (decision 2026-06-08) — supersedes the bespoke schema below
**Chris: "We should be using a consistent data model and I'd like UDLM to be that data
model."** Migration 017 (`migrate_017_capability_catalog.sql`) is now a **DRAFT** — it is
DAV-bespoke (BIGSERIAL ids, ad-hoc status flags, no field provenance/classification) and
must be reworked to be **UDLM-conformant** before it ships.

**Effectiveness analysis / case study: `udlm/docs/case-study-dav-knowledge-realization.md`**
— the full write-up of how UDLM maps onto DAV (four-state ↔ curation, drift ↔ gap
analysis, where it stretches).

**DAV is a UDLM realization — a peer to DCM.** DCM realizes UDLM for *infrastructure*;
**DAV realizes UDLM for architecture/capability *knowledge*.** Both emit UDLM-conformant
Data. The capability catalog is the **pilot** for this pattern (prove it on one entity
family before re-modeling UCs/gaps/runs — don't boil the ocean).

**Why UDLM fits (not forced):** the back-fill loop maps onto UDLM's four states —
**Discovered** = capabilities an assessment says exist (ephemeral, per-run); **Intent** =
a proposed capability/term; **Realized** = the curated canonical catalog/taxonomy entry;
the **gap between Discovered and Realized/Intent = DAV's analysis.**

**UDLM Data properties to adopt** (foundations/foundations.md, four-states.md): UUID
(stable across lifecycle); Type; one Lifecycle state (per-type state machine = the
curation/normalization lifecycle); the standard **artifact-metadata block** (handle,
version, status, owned_by, created_by, created_via); **field-level provenance**; **data
classification** per field; contributor identity. Ownership/sharing → use UDLM's
ownership-sharing-allocation model (our tier/tags concept maps to it — don't reinvent).

**Prereq (DONE 2026-06-08):** the UDLM **Knowledge entity-type family** is drafted —
`udlm/foundations/entity-type-families.md` (defines "entity-type family" = a *logical
grouping*, NOT a usage boundary; definitions are universal/free-to-use, only *instances*
carry ownership/classification/scope) and `udlm/entities/knowledge-family.md`
(Capability, TaxonomyTerm, Alias, Antipattern as UDLM Data: UUID, metadata block, the
curation lifecycle PROPOSED→UNDER_REVIEW→CANONICAL + OBSERVED/DEPRECATED, four-state
mapping, provenance, classification). **Migration 017 rework** now realizes these
types: UUID PKs, the artifact-metadata block, a `lifecycle_state` per the curation
machine, field-level provenance, per-field classification, and a **`family` tag**
(disambiguation namespace — `Drive [Computing]` vs `Drive [Automotive]`; UDLM
entity-type-families §6); the tier/tags scope is an *instance* concern
(ownership-sharing-allocation), distinct from the universal definitions.

## Build order (fundamentals, OSS-safe, no confidential data)
1. ✅ **Parse `DCM-Taxonomy.md` → structured {domains, terms, anti_aliases}** —
   `review-console/api/app/capability_taxonomy.py` (pure, no DB). 42/164/11 verified.
   Next: project into the `capability_taxonomy_terms` seed (idempotent, re-seedable).
2. **`capability_catalog` + taxonomy tables** (migration; tenancy + pillar from birth).
3. **Normalization + back-fill mechanism** (resolve a capability → taxonomy term, or
   flag a gap + propose a term; curation endpoints; hybrid propose/curate UI later).
4. **Resolver** for existing `uc_capabilities` strings → catalog ids.
5. Then F7 assessment ingestion lands findings onto the catalog (synthetic fixture here;
   real confidential parsers inside the work env — see active-work.md WORK/PERSONAL
   BOUNDARY).

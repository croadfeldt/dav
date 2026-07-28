## F7 detail — assessment ingestion (decisions 2026-06-08)
- **Pilot = Automation assessment/strategy** (most data + usage). Data volume order:
  automation > hybrid-cloud > AI. **A generalized DCM strategy is the SUPERSET** across
  all of them.
- **Capability catalog ↔ DCM Taxonomy** — independent catalog, normalized TO the
  taxonomy, **back-fills the taxonomy where gaps exist** (catalog drives taxonomy
  completeness). Taxonomy = normalization authority (form); catalog = living inventory
  (substance). DCM superset → sub-domains {automation, hybrid-cloud, AI}, pillar-namespaced.
  Resolves the free-form-capability dependency. **Full design + schema sketch:
  `docs/capability-catalog-design.md`** (the keystone — build first).
- **WORK/PERSONAL BOUNDARY (critical):** real assessment output is **work-confidential**
  — it must be parsed **inside** the work env (Chris will move/run DAV inside for that).
  DAV stays OSS in personal. So **here we build the GENERIC mechanism only**: the
  assessment schema, a parser/mapper **interface** (dispatch by assessment type), the
  assessment-target abstraction, and a **synthetic/example** automation fixture. The
  real per-format parsers + confidential data are a drop-in **inside**. No confidential
  data in the OSS repo. See [[feedback_account_split]].
- **Fundamentals buildable here now:** (1) canonical capability catalog seeded from the
  DCM taxonomy (keystone); (2) `assessments` + `assessment_findings` schema
  (pillar/domain-aware, catalog-anchored); (3) generic import framework + type-dispatch
  parser interface + synthetic automation fixture; (4) map findings → UCs/capabilities/
  gaps so the existing engine consumes them; (5) F6 evaluation-target generalization.


# Repo-cleanliness review — the nine questions (udlm · dcm · dav)

**What this is.** The standing cleanliness bar for the three repos, split by enforcement mechanism:
**deterministic questions run in CI on every PR and on a schedule**; **semantic questions are the standing
brief for the review sweep** — LLM review agents (or a human reviewer) dispatched per repo, on cadence and
after any material change. This is the one home for the nine questions (originally authored as DAV scoping
sets in PR #45; re-homed here because the gap engine verifies that a *rule exists*, not that a repo
*complies* — CI gates and review agents do the actual detection).

**The process:** (1) CI runs the deterministic gates on every PR (each repo's `validate.yml`) and the full
suite on a monthly schedule (`cleanliness.yml`), opening an issue on failure; (2) the semantic sweep runs the
questions below as the agent brief — one agent per repo slice, findings as `file:line` + severity + owner —
after ratified-ADR batches, boundary changes, or the monthly issue's prompt; (3) findings feed a cleanup plan
the maintainer approves (the 2026-07-23 sweep is the reference run).

---

## The nine questions

### 1. Single-source *(deterministic + semantic)*
- Every normative concept/rule/vocabulary resolves to exactly one owning document; others reference by
  ID/link, never restate; no two documents carry a conflicting copy of the same rule/enum/table; a **prose
  restatement** counts, not only a duplicated rule-ID.
- **CI:** udlm `check_single_source.py` + `check_definition_single_source.py` (rule-IDs + definitions).
- **Semantic residue:** prose restatement — the checkers see IDs and fenced blocks, not paragraphs. Agent
  brief: hunt substantive re-explanations against `docs/file-index.md` ownership claims.

### 2. Consistency *(deterministic + semantic)*
- Shared shapes reference the canonical `$defs`, never reinvented inline; no per-type synonyms for a
  canonical enum/field; naming conventions uniform; divergent spellings/casing flagged.
- **CI:** udlm `check_model_vocabulary.py` (fenced blocks); dcm `check_terminology.py` (retired terms).
- **Semantic residue:** prose vocabulary drift (the guards scan code fences only — the `kind`→`edge_type`
  prose escape is the type specimen).

### 3. Standards-documented *(deterministic + semantic)*
- Every `adopts` entry has a register entry (adopt↔register holds) with disposition/tier; no standard cited
  in prose but absent from the register; where a credible external standard exists, adopt it — don't invent.
- **CI:** udlm `check_standards_registered.py` (ADOPT-001).
- **Semantic residue:** the "should have adopted a standard" judgment.

### 4. Data-pipelines-complete *(semantic; audit-driven)*
- Every field/output has an identified producer and consumer; no field asserted without a demonstrated
  produce/consume; orphan fields (no consumer) and phantom fields (no producer) flagged.
- **Mechanism:** the derive-candidacy + unused-data audits (udlm #194/#198 discipline); typed-output
  resolution in `validate_registry` covers the policy-output slice.

### 5. Boundary-respected — ADR-008 *(semantic)*
- Every passage passes the peer test (peer-could-differ → the realization layer); no engine mechanism in a
  data-model document; documents titled/scoped to their layer; cross-layer references are pointers.
- **Mechanism:** agent sweep, both directions (udlm restating engine, dcm restating model — RHY-001 was the
  type specimen).

### 6. Provider-neutral *(semantic; PVD-adjacent)*
- No definition names a mechanism/provider as *the* way to realize a fact; concrete providers are examples,
  never normative; no estate specifics in the portable spec; the origin/method of a fact is a field, not a
  prescribed mechanism.
- **Mechanism:** agent sweep + PVD (ADR-037) discipline; `check_estate_tokens.py` catches estate leakage.

### 7. Correctly-scoped-docs *(semantic)*
- Base models the portable definition; provider-specific config projected, not modeled field-by-field; field
  descriptions state what the field *is* — no change history/issue numbers/backstory; provenance/changelog
  lives in git/register, not normative text; opaque provider bodies are version-pinned passthroughs.

### 8. Referential-integrity *(deterministic)*
- Every reference target resolves to a registered, in-scope resource; no reference to removed/nonexistent
  types or documents; prose cross-references resolve to a real section.
- **CI:** `check_links.py` (markdown link resolution — all three repos) + udlm `validate_registry` (`$id`/ref
  resolution).

### 9. Versioning-discipline *(deterministic + semantic)*
- A change bumps the version to the required class; the id/uri version segment matches the version field;
  new/changed `adopts` carry version + register pairing + disposition; no content change without the
  version/metadata update.
- **CI:** udlm `validate_registry` + `compat-check` ($id↔version coherence).

---

## Per-repo gate inventory (keep this true)

| Gate | udlm | dcm | dav |
|---|---|---|---|
| single-source / definitions | ✅ CI | — (no rule-ID registry yet) | n/a |
| terminology / vocabulary | ✅ CI | ✅ CI | — |
| standards register | ✅ CI | — | n/a |
| estate tokens | ✅ CI | ✅ CI | — |
| markdown links (`check_links.py`) | ✅ CI | ✅ CI | ✅ CI |
| registry / contract validation | ✅ CI | ✅ CI | lint (`yamllint`/`ansible-lint`/Jinja parse) |
| scheduled full-suite + issue-on-failure (`cleanliness.yml`) | ✅ | ✅ | ✅ |
| semantic sweep (Q1–Q7 residues) | per cadence — this brief is the agent prompt |||

*A "—" is a known gap, not a pass. When a gate lands, flip the cell; when a new drift class appears, add the
question — in this file only.*

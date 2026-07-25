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

### 10. Pins-current *(deterministic + semantic)*
- Every pinned reference — SHA, branch name, tag, image ref, `$id`/`type_ref` URL, corpus `root_path` —
  resolves AND points at current, merged reality. A pin to a review branch, a never-merged commit, or a
  rewritten SHA is a HIGH finding even while the fetch still works (orphaned SHAs keep resolving on
  borrowed time).
- **Type specimens (one night, three instances):** an instance-data repo's CI pinned its spec
  dependency at a never-merged branch commit (red pipeline, 1000+ schema failures the moment the
  pin was bumped); a visualizer deployment pinned two different long-merged feature branches
  (silently frozen data).
- **CI:** `tools/sweep/check_pins.py` (this repo) — point it at any repo root; wire into each repo's
  `cleanliness.yml`.
- **Semantic residue:** "current" needs judgment — the pin may resolve to a live ref that is still the
  wrong one.

### 11. Cross-repo vocabulary parity *(deterministic)*
- A rename ratified in the owning repo must not survive in sibling repos. The owner's guard protects
  only the owner: `kind`→`edge_type` was fixed in udlm while still live in dcm prose and in a
  downstream instance-data repo's validator, tools, and schema pin — four repos, one drift.
- **CI:** `tools/sweep/vocab_parity.py` (this repo) — retired-term list swept across the sibling
  repos' surfaces; wire into each consumer repo's `cleanliness.yml`.
- Retired-term list lives in the script (one home); extend it in the same PR as any future rename.

### 12. Human-readable — the Jordi criteria *(semantic)*
- Every document a person will read passes the bar set in the Jordi review conversations: the
  **audience is known** and it's written to them (senior engineers — don't restate what they know);
  the **contract is up front** (what this is, what it settles, first paragraph); **references carry
  their gist** (never a bare number or filename — say what the cited thing decided); **less is more**
  (cut anything that doesn't move a decision); no internal/session shorthand an outsider can't decode.
- Flow-tier docs must be readable start-to-finish by an engineer who did not write them.
- **Mechanism:** agent sweep, ordered by the 21-UC surface first (below).

---

## Standing sweep parameters — audience and voice

- **Audience: human engineers.** Every judgment call in the sweep asks "does this cost an engineer
  time?" — not "is this formally perfect."
- **Voice: software and data-model architect.** Documents speak as the architect explaining a
  settled design to peers — declarative, grounded in the model, no marketing, no editorializing
  ("genuinely", "honestly", "the caveat is real" are defects), no hedging on decided things.
  Findings that flag voice drift cite the sentence, not the vibe.

## Sweep ordering — engineer-time first

Findings are reported **21-UC surface first**: the spec surface the September use cases require
(enumerated in udlm `registry/UDLM-0.1-SCOPE.md`) outranks peripheral docs, because that is what
engineering reads next. Severity within each tier: HIGH = an engineer will trip on it; MED = drift
that will grow; LOW = polish.

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
| pins-current (`tools/sweep/check_pins.py`, Q10) | — | — | — (script lands here; wire per repo) |
| vocab parity (`tools/sweep/vocab_parity.py`, Q11) | — | — | — (script lands here; wire per repo) |
| semantic sweep (Q1–Q7, Q10–Q12 residues) | per cadence — this brief + `docs/runbook-overnight-sweep.md` are the agent prompt |||

*A "—" is a known gap, not a pass. When a gate lands, flip the cell; when a new drift class appears, add the
question — in this file only.*

## Q13 — The cold reader (DOC-001, ruled core 2026-07-25)

Every document in scope is held to the cold-reader test: hand its opening to a competent
engineer who has never seen these repos — if they can repeat back what was decided and why it
matters, it passes; if they need a second document first, it fails. Sweep mechanics: sample
documents changed since the last sweep (ADRs and design notes always; READMEs and contexts on
rotation), read each as the persona it targets, and file every failure as a doc defect with
the sentence that lost the reader quoted. The standard is DOC-001 (udlm CONTRIBUTING.md rule
#1): narrative prose inside existing formats, industry-named concepts before internal
shorthand, references carrying their gist, jargon introduced not assumed. Two live precedents
prove the test finds what gates cannot: reader questions about pin/upstream-chain behavior
and about atomic-recompilation scope each exposed a doctrine ambiguity no deterministic gate
could see — both became same-day patches. The interpretability probe campaign (ADR-004) is
this question automated for type contexts; this sweep question covers the documents the
probes do not reach.

# DAV Framework Roadmap

**Status:** Living document, last updated 2026-04-27
**Current state:** Phase 4 baseline complete (run `2026-04-27T02-05-41Z-3b49872`); 3 architectural gaps surfaced and handed to the DCM session for response. Framework verified working end-to-end.

This roadmap captures the agreed sequence for evolving DAV beyond its current "produces baseline reports" state into "actively gates spec changes and proposes UCs the architect didn't think of." Each item is its own focused session; the order matters because earlier items provide foundation for later ones.

---

## Anchoring decisions

Two architectural commitments that shape every item below:

1. **Framework proposes; architect disposes.** Every UC that enters the corpus passes through explicit human admission. This holds for hand-authored UCs (already true), generated UCs (Mode C1+), coverage-suggested UCs (Mode C2), and adversarial UCs (Mode C3). No automated admission, no auto-promotion. The framework's role is to surface candidates worth considering; the architect's role is to choose which become canonical.

2. **Self-referential validation must be guarded against.** When the framework starts generating UCs, those UCs are tagged with their generation provenance (`generated_by.mode = exploration`, `generated_by.source`, `generated_by.model`, `generated_by.prompt_version`). Verdicts on generated UCs can always be sliced separately from verdicts on hand-authored UCs. This means at any moment we can ask "what does the framework say about UCs the architect wrote?" — preserving an independent ground-truth signal even as the corpus grows with generated content.

These commitments lock in protection against the failure mode where the framework grades its own homework.

---

## Session A — Negative UCs (calibration)

**Goal:** Prove the framework can produce `unsupported` verdicts when the spec genuinely doesn't address what a UC requires. This is calibration we need before trusting *any* automated `unsupported` verdict, including verdicts on generated UCs in later sessions.

**Estimated duration:** ~1 hour

**Scope:**
- Hand-author 1-2 UCs that require something the architecture explicitly does not provide
  - Candidate 1: a UC requiring a feature DCM has no concept of (e.g., automatic in-place capacity expansion of running VMs without restart — there is no such primitive in the spec)
  - Candidate 2: a UC requiring a provider type that does not exist (e.g., delegating audit verification to an external blockchain attestation service — there is no such provider type)
- Run them through DAV verification mode
- Confirm the verdict consensus produces `unsupported` (or, less ideally, `partially_supported` with gap factors clearly identifying the absence)
- If the verdicts come back `supported`, the framework has a calibration problem that needs fixing before we trust any `unsupported` reads

**Dependencies:** none. Can start immediately.

**Success criteria:**
- Both negative UCs produce non-`supported` verdicts
- The dissent factors clearly identify the architectural absence (not some unrelated reason)
- We have evidence that DAV's verdict space genuinely includes the negative case

**Watch for:**
- "False supported" verdicts where the model invents architectural support that doesn't exist (this would be a serious framework concern)
- Calibration drift: if a feature the spec genuinely lacks gets a `partially_supported` verdict instead of `unsupported`, the verdict thresholds may need adjustment

**Notes:**
- Tag these UCs with `generated_by.mode: authoring`, `tags: [negative, calibration]`
- Place in `dav/use-cases/calibration/` to keep them separable from architectural-coverage UCs
- These UCs should remain in the corpus permanently — they're the regression check that prevents the framework from drifting toward false-positive `supported` over time

---

## Session B — Baseline delta diagnostic

**Goal:** A small standalone tool that takes two run IDs and produces a verdict-shift report. Foundational for Mode B; useful immediately for any "did this spec change move the verdicts?" question.

**Estimated duration:** ~30-60 minutes

**Scope:**
- Read two run summaries from `/workspace/results/<run-id>/analyses/*.yaml`
- Produce a delta table:
  - Per-UC verdict pattern shift (e.g., "uc-008: 2-1 partial → 3-0 supported")
  - New gap factors that appeared
  - Gap factors that disappeared
  - Confidence level changes
- Output as markdown (for inclusion in PRs/issues) and as JSON (for programmatic consumption by Mode B)
- Lives in DAV repo under `engine/src/dav/tools/run_diff.py` or similar

**Dependencies:** Session A is not a hard dependency, but having calibrated negative UCs makes the diff tool more useful (you can see whether negative UCs stay `unsupported` or drift, which is a regression signal in itself).

**Success criteria:**
- Given two run IDs, produces a clear, readable delta
- Highlights regressions (verdicts moving away from `supported`) distinctly from improvements (verdicts moving toward `supported`)
- Unit-tested with synthetic before/after run pairs

---

## Session C / D — Mode B (PR-targeted gating)

**Goal:** Operationalize the framework as a process gate. Spec PRs trigger automatic DAV runs; verdict shifts are reported back to the PR. This turns DAV from "manually invoked analysis tool" into "active spec-change verifier" — the ADR-001 design intent.

**Estimated duration:** 1-2 sessions (~90 min each)

**Scope:**

Session C (backend):
- GitHub webhook receiver wiring (Tekton EventListener → existing Mode A pipeline)
- "Mode B" Tekton pipeline variant: runs DAV against the PR branch, then against the baseline tag, then invokes the baseline delta tool from Session B
- Status reporting: while the run is in flight, the PR shows a checkmark (pending). When complete, the PR shows green (no regressions) or red (verdicts moved away from `supported`)
- Webhook auth, rate limiting, run-cancellation (newer commits supersede older runs on the same PR)

Session D (frontend / commenter):
- GitHub PR commenter: posts the verdict-delta markdown table back to the PR as a comment
- Update mechanism: subsequent runs on the same PR update the comment in place rather than spamming new comments
- Handle the case where DAV itself errors (don't block the PR, but flag clearly)

**Dependencies:**
- Session B (baseline delta tool) is required — the comment body comes from that tool's markdown output

**Success criteria:**
- A test PR that does nothing produces a "no changes" verdict report
- A test PR that intentionally introduces a regression (e.g., reverts the Composite Service cleanup on one file) produces a verdict shift in the comment
- The Mode B run completes within reasonable wall-clock time (probably 60-120 min for the full corpus; faster modes for smaller PRs are deferrable optimization)

**Watch for:**
- Webhook auth complexity (GitHub's HMAC signature verification, Tekton's EventListener filter syntax)
- Run cost — every PR triggers a run; this could get expensive on cluster compute. Consider whether Mode B should default to a UC-subset (only UCs whose tags or domains intersect the changed files) for fast feedback, with full corpus runs only on merge or explicit request

---

## Session E — Mode C1 (architect-prompted generation)

**Goal:** First production-quality UC generation capability. The architect provides a prompt template ("generate 3 UCs exploring the failure modes of recovery policy"); the framework produces YAML candidates; each candidate goes through human review before admission. No automated admission; no claims about coverage; just "framework proposes, architect disposes."

**Estimated duration:** ~1 session

**Scope:**
- A new "generation" mode in the engine that takes a prompt template and produces N candidate UCs
- Generated UCs are valid against the existing UC schema
- Generated UCs are tagged with `generated_by.mode: exploration` and full provenance metadata
- Output candidate UCs go to a holding area (`/workspace/proposals/`), not directly into the corpus
- A CLI subcommand (`dav generate --prompt-template auth-failures.txt --count 5`) drives the generation
- Review workflow: architect reviews candidates, edits as needed, copies admitted ones into `dav/use-cases/<domain>/` and tags them as admitted

**Dependencies:**
- Session A — without negative UCs, we can't tell whether a generated UC is actually testing what it claims to test
- Session B — useful for evaluating the impact of admitting a new UC ("how does this change the corpus's verdict pattern?")
- Session C/D not strictly required, but Mode B makes admission cheaper because admitting a UC triggers a run that immediately tells you whether it's useful

**Success criteria:**
- Architect can request 5 UCs on a topic and the framework produces 5 valid YAMLs
- Architect can review and admit 1-2, reject the rest, with clear reasoning per UC
- Admitted UCs run cleanly through DAV verification mode and produce defensible verdicts
- Generated UCs are clearly distinguishable from hand-authored ones in run reports

**Watch for:**
- Generation prompt sensitivity — small prompt changes producing wildly different UC quality (suggests the framework needs prompt engineering refinement, not necessarily a flaw in the generation concept)
- Generated UCs that look superficially good but are actually duplicating existing UCs (need a similarity check during review)
- Generated UCs that fail schema validation (acceptable as long as the rate is low and the failures are clearly diagnosable)

---

## Future Sessions (rough order, no dates)

### Mode C2 — Coverage introspection

The framework reads the existing corpus + spec, identifies dimension combinations / capabilities / spec sections that aren't exercised by any UC, and proposes candidate UCs to fill those gaps.

This is more bookkeeping-flavored than C1. It requires:
- A formal definition of what "coverage" means (controlled-vocabulary cross-product? capability-citation count? gap-factor diversity?)
- An introspection mechanism that reads the corpus and the spec and produces a coverage report
- A generation step that proposes UCs targeting under-covered areas

Higher complexity than C1, lower judgment cost per UC (the proposals are mostly mechanical).

### Mode C3 — Adversarial / edge-case generation

The framework actively probes the spec looking for scenarios that should expose ambiguity or contradiction. "Given the spec's recovery policy, here's a case that crosses a boundary in an unexpected way."

Highest leverage, highest false-positive rate, hardest to evaluate. This is the capability that a human team can't replicate at scale — once it works, the framework augments architectural thinking rather than just verifying it.

Build only after C1 has been working reliably for a while.

### Review Console UI

Surface the runs, verdicts, gaps, and dissent trajectories in the existing Review Console webapp. The data is already produced; this is API surfacing + frontend rendering.

Per-section scoping in earlier session: probably 90 min backend + 90 min frontend for usable v1.

Build after Mode B is producing routine runs that warrant a UI to keep up.

### Stage 3 dissent triage automation

When ensembles dissent, hand the dissenting trajectory to a human (or future review-tier model) to classify as `real architectural finding` / `model misread` / `spec ambiguity`. Stored classifications calibrate future ensembles.

Likely emerges naturally from Review Console work — the UI is the natural place to do classification.

### Build-args image-provenance fix

OCP binary builds drop `--build-arg` from the CLI; the Containerfile's `ARG DAV_REPO_COMMIT=unknown` default takes over and the verify-image-provenance.sh script reports `[STALE]` on every fresh build. Probably needs `dockerStrategy.buildArgs` in the BuildConfig spec.

Forensic noise, not functional impact. Defer until a session where infrastructure-debt cleanup is the focus.

### Inference endpoint finalization

Currently in flux per ADR-001 — the dual-GPU Q8 layer-split Qwen3-32B is the working interim. Final pick TBD between several candidates. Not a framework concern (DAV is consumer-agnostic per ADR-001) — this is an inference operational concern that resolves separately.

---

## What this roadmap explicitly does not promise

A few things worth being explicit about so they don't drift into expectation:

- **No claim that Mode C will replace human UC authoring.** The architect remains the source of architectural intent. Generated UCs are proposals, not replacements for thought.
- **No claim of complete coverage.** Even with Mode C2 fully built, the corpus will only exercise what the framework can introspect. Implicit architectural assumptions, cross-document subtleties, and "we never thought of this" gaps remain harder to surface.
- **No claim that automated regression detection eliminates manual review.** Mode B catches verdict shifts but doesn't tell you whether a shift is good or bad — that's still architectural judgment. The framework reduces the set of things you have to look at, it doesn't replace looking at them.
- **No timeline commitments.** Sessions are ordered, not scheduled. Each session ships when its scope is done well, not when a date arrives.

---

## Pickup notes for next session

Whenever you next pick up DAV work:

- This roadmap is the source of truth for sequence
- Start with **Session A (Negative UCs)** unless explicitly redirected
- The Phase 4 baseline report (`dav-phase-4-baseline-report.md`) is the reference for current corpus verdict shape
- The DCM session is in flight on the three gaps from Phase 4 — when those land, they'll produce PRs that benefit from Mode B existing, which is incentive for sequencing C/D before C1
- Tags to be aware of: DCM `dav-baseline-v0.1.12` (after you push it), DAV `v0.1.12` (already pushed)

---

*Document maintained by the DAV project. Edit as the roadmap evolves.*

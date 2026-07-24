# Runbook — the overnight architecture sweep (repeatable)

**What this is:** the repeatable process for the big overnight consistency sweep of the UDLM data
model and DCM architecture. Goal: engineers never spend time cleaning the repos, the data model, or
the architecture — the sweep finds it first. Companion: the nine-question brief +
Q10–Q12 additions in [`repo-cleanliness-review.md`](repo-cleanliness-review.md) — that file owns
the questions; this file owns the procedure.

## Standing parameters
- **Audience:** human engineers. Every finding is judged by "does this cost an engineer time?"
- **Voice:** software and data-model architect — declarative, model-grounded, no editorializing.
- **Ordering:** 21-UC (0.1 scope) surface first — udlm `registry/UDLM-0.1-SCOPE.md` names it.
- **Read-only:** sweeps never commit/push/post; output is findings reports the maintainer reviews.

## The procedure (per sweep night)

1. **Sync** local clones of udlm, dcm, dav (and the operational repos for Part 3) to origin/main.
2. **Deterministic gates first** — run each repo's full gate suite (udlm `scripts/signoff.sh`; dcm
   `tests/*.py`; estate `tests/validate_estate.py`). Anything red is finding #1; the sweep does not
   argue with a green gate, it hunts what gates cannot see.
3. **Dispatch two parallel sweep agents** (one udlm, one dcm+cross-repo), each briefed with:
   the nine questions + Q10–Q12, the standing parameters above, the list of changes since the last
   sweep ("verify the changes left no seams" — half-updated passages, references to moved homes,
   orphaned content), and the deliverable format below.
4. **Cross-repo dimensions** (second agent): the Q10 pin audit across ALL repos incl. operational
   (estate CI refs, explorer refs, image tags, corpus paths); Q11 vocabulary parity from the
   retired-term list; consumer-tooling parity (does every generator/validator/tool consume the
   current registry surface — run them, don't just read them).
5. **Deliverables:** one report per agent at `~/SWEEP-<scope>-<date>.md` — verdict first (will
   engineers waste time: yes/no/where), findings as `file:line` + severity
   (HIGH = an engineer will trip / MED = drift will grow / LOW = polish), uc-core surface first,
   and a **"propose adding to CI"** list: every finding class a deterministic gate could catch
   forever, with the target repo named.
6. **Morning consolidation:** the maintainer reviews; accepted CI proposals become gates in the
   same week (the ratchet: every sweep should shrink the next sweep's semantic surface).

## Automation shipped with this runbook
- `tools/sweep/check_pins.py <repo-root>` — Q10 deterministic half: finds pinned SHAs/refs in CI
  and config files, verifies each resolves on its remote and (for SHAs) is reachable from the
  remote's default branch.
- `tools/sweep/vocab_parity.py <repo-root> [...]` — Q11: sweeps the retired-term list (owned in
  the script, one home) across sibling repos' text surfaces.
- Wire both into each repo's `cleanliness.yml` (monthly + on-demand); the gate-inventory table in
  the brief tracks which repos have them live.

## Cadence
After any ratified-ADR batch, boundary change, or bulk rename; monthly via `cleanliness.yml`
regardless; on demand the night before engineering-facing pushes (e.g. a dcm-project publishing
wave).

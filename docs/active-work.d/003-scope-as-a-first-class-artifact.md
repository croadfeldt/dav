## 🔬 PLAN 2026-07-27 — scope as a first-class artifact (friction items 4–7)
**Why:** Chris: plan the scope changes first. Centerpiece = a **corpus_index** table (one row
per UC per namespace, dimension-validated at index time, SHA-stamped) so scope and quarantine
are known BEFORE launch. Five PRs: P1 index · P2 trigger-time scope resolution (extends t007 to
corpus mode; folds parked feat/trigger-preflight) · P3 preflight surface in New Analysis +
declared-scope denominators everywhere · P4 quarantine as a run artifact (predicted vs actual) ·
P5 catalog import path. **Five ⚖ decision points need Chris** (index freshness, warn-vs-block,
snapshot semantics, 6a auto-grant, sequencing vs #87). Plan: **`docs/scope-first-class-plan.md`**.


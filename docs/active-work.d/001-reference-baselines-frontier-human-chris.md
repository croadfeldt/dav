## 🔬 DESIGN 2026-07-27 — reference baselines: frontier + human (Chris)
**Why:** the fixture scores local models against SEEDED truth; nothing measures the seeds or
the ceiling. F1: frontier on fixtures (whole spec fits one context — no tool loop needed, no
engine change; same scorer). F2: frontier judges a ~30-finding sample of REAL corpus output
(where no ground truth exists). H: human adjudicates contested seeds, F2 disagreements, and a
small agreement-audit (the judge gets judged). Proof the human tier is load-bearing: Chris
overruled a claim both the fixture author AND the analyzer shared. **Blocked on the standing
Anthropic-API-key ask** + a human-time dial. Doc: **`docs/reference-baselines-design.md`**.


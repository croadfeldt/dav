## ✅ SHIPPED 2026-06-13 — Roadmaps IA + Enhancement/PR Workbench (#140 · #138 · #145)
Deployed to ns `dav` (gate: compile · route-shadow 249 · UI e2e 60/0). Commits on
`feat/dcm-uc-prioritization`: `f2f5297` (IA + workbench + CI/CD design), `4051409` (#145 split),
`ebbd761` (scroll fix) — all pushed.
- **Roadmaps domain IA (#140):** four sub-tabs **Arch Review · Enhancement / PR · Cap Map · Roadmap**
  (relabeled `review`→Arch Review, `engineering`→Roadmap; added the `enhancement` view).
- **Enhancement / PR Workbench (#138):** new `#view-enhancement`, the "process enabler". Backend
  `POST /api/enhancements/preview` parses the plan (`enhancement_apply.parse_enhancement_blocks`) +
  routes each finding to its enhancement-target repo by `target:` namespace (read-only; returns
  `groups`/`unmatched`/`no_target`); `selected_ids` added to `POST /api/enhancements/apply` for
  selective submit. UI: per-repo PR groups, select **per finding / per PR / bulk**, expand to view
  patch+acceptance, **retarget** an unmatched namespace inline, **Submit selected → one PR per repo**
  (confirm + `project.enhance-pr`).
- **#145 — finished the split:** Enhancement Plan **generation moved out of Arch Review into the
  Enhancement/PR tab** (Step 1 · Enhancement Plan → Step 2 · Route → PRs); the superseded single-repo
  Create-PR form (`rpPrSection` + handlers) was removed from Arch Review. Arch Review = the review only.
- **Scroll fix:** `#view-enhancement` content sat in a plain centered div, but `.pf-view` is
  `overflow:hidden` (views need an inner `flex:1; overflow-y:auto` region like `.rp-output`) → content
  past the viewport was clipped. Wrapped it in a scroll region.
- **CI/CD design captured (#143):** `docs/cicd-design.md` (Tekton e2e, gate-as-merge-gate; needs
  Chris's webhook secret + registry creds + deploy branch).
- **NEXT (teed up, needs Chris's shape):** #141 proper roadmap creation tool (Roadmaps → Roadmap) +
  #142 SOW-from-roadmap (open-ended — proposed approach in the 2026-06-13 morning-review writeup, to
  design together before building). #139 push DCM/UDLM to RH in chunks.


# DAV Framework Roadmap

**Status:** Living document, last updated 2026-05-21
**Current state:** Phase 4 baseline complete (run `2026-04-27T02-05-41Z-3b49872`); Review Console v1 shipped (ops frontend, Runs/Results/Use Cases/Config tabs). Framework verified working end-to-end.

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

### ~~Review Console UI~~ — **SHIPPED (v1, 2026-05-21)**

The review console was redesigned from a corpus file-review tool into a full DAV operations frontend. Built ahead of the original sequencing (before Mode B) because the corpus was producing enough routine runs to justify a UI now.

**What shipped:**
- **Runs tab** — lists PipelineRuns, triggers new runs with full param control (mode, sample count, corpus subpath, repo overrides, inference overrides, halt-on-error)
- **Results tab** — browses `/workspace/results/` from the shared PVC; per-UC verdict/findings/gaps/recommendations; handles all three output modes (verification, reproduce, explore)
- **Use Cases tab** — full CRUD for managed UCs (Postgres-backed) + read-only view of corpus UCs from git; clone-to-managed for corpus UCs
- **Config tab** — spec/corpus repo switching

**Deferred from v1 (see items below):**
- Hard-linking PipelineRun names to result directory IDs
- Git push-back for managed use cases
- Multi-project / multi-consumer support from a single console instance

### Console v2 — run/result hard-linking

PipelineRun names (e.g. `dav-console-123456`) and result directory IDs (e.g. `2026-05-21T10-30-00Z-abc1234`) are currently correlated only by timestamp. Hard-linking would require:
- Adding a `--run-label` or `--run-id` param to the Tekton pipeline task
- Having `run_corpus.py` write that label into `run-summary.yaml`
- The console's Runs tab reading the label from completed PipelineRuns and linking out to the Results tab

Low urgency while run volume is small and timestamps suffice for manual correlation.

### Console v2 — managed UC git push-back

Managed UCs are currently stored only in Postgres. They should optionally be commitable back to the consumer's corpus repo as a PR. Design work needed:
- OAuth token scoping (the user's OCP identity needs to be mappable to a git credential)
- Target repo + branch selection in the UI
- PR authoring (title, body, commit message from the UC metadata)

Complexity is mostly in the auth bridging. The actual git operations are straightforward.

### Console v2 — multi-project / multi-repo support — **IN PROGRESS (M1-M8, 2026-05-27+)**

Originally scoped as a `dav-console-projects` ConfigMap. Superseded by the
managed_repos registry per [ADR-003](../adr/003-multi-repo-registry-and-mcp-source-of-truth.md).

Now: a first-class DB registry (`managed_repos` table) is the source-of-truth
for which repos DAV operates on. Repos carry roles (`spec`, `corpus`,
`issue-source`) and a `tenant_id` so multi-tenant filtering can layer on
later without a migration. The single dav-docs-mcp pod consumes a
projection of the registry (the existing dav-source-spec ConfigMap, now
regenerated by the API rather than operator-edited). Per-tenant request
filtering deferred to a future ADR-003-derivative when the workload arrives.

Milestone breakdown:

- **M1 — managed_repos table + CRUD API** ✅
- **M2 — Registry → ConfigMap projection** ✅ (idempotent, role=spec hook, rolls dav-docs-mcp on real change only)
- **M3 — Repos UI view** ✅ (Config → Managed repos: list with role chips, add/edit/delete dialog, manual "↻ Project" button, projection-status feedback in toasts)
- **M4 — Sources panel refactor** ✅ (Architecture spec panel is read-only; shows the list of projected sources; "↑ Manage in Repos" jump-to button; legacy single-source ConfigMap detected and called out for conversion)
- **M5 — `pr_comments` table + GitHub poller** ✅ (migration 008; async background poller hits role=issue-source repos every 5 min; upserts open-PR `issue_comment` + `pull_request_review_comment` into pr_comments; per-repo poll-state row tracks success/error + watermark; status lifecycle `new` → `dismissed` | `drafted_to_uc`; UC↔comment provenance via uc_pr_comment_links.)
- **M5b — Per-repo PAT redesign (ADR-004)** ✅ (replaces M5's cluster-wide GITHUB_TOKEN env-var with per-repo Fernet-encrypted credentials on managed_repos. Migration 009; crypto.py wraps Fernet; repos.py never returns secrets via HTTP, exposes `has_*` flags; github_client takes explicit token param; poller fetches per-repo PAT; UI Repos form adds PAT + webhook-secret write-only fields with Set/Rotate/Clear; forward path to HashiCorp Vault documented in ADR-004 §D.)
- **M9 — Shared credentials abstraction (ADR-005)** ✅ (migration 010 + credentials table + FK columns on managed_repos. `credentials.py` module with CRUD + get_credential_secret + resolve_credential_id helpers. `repos.get_repo_secrets()` resolution order: shared credential FK → inline encrypted column → None. API: GET/POST/PUT/DELETE /api/credentials, /api/credentials/types/vocabulary, POST /api/repos/{x}/convert-credential. UI: new Config → Shared credentials panel with list + add/edit/delete; Repos form gains per-field dropdowns to pick existing credential OR inline value; convert-to-shared button on each repo's inline credential; row chips indicate shared vs inline source. Vault forward path noted again — `credentials` rows map cleanly to Vault KV paths.)
- **M10 — Consolidate code_repo_configs into managed_repos (ADR-006)** ✅ (added `enhancement-target` role; migration 011 + Python data migration folds each code_repo_configs row into managed_repos, matching by repo_url and merging roles when the row already exists; tokens migrated to Fernet-encrypted github_pat_encrypted when key available. Enhancement PR endpoint rewritten — `PrCreateIn.repo_uuid: str` replaces `repo_config_id: int`, looks up via repos.get_repo + get_repo_secrets. /api/code-repos endpoints return 410 Gone pointing at /api/repos?role=enhancement-target. UI: Code repositories panel + nav link removed; PR creation dropdowns repointed at the new endpoint with provider inferred from URL when metadata.provider absent. code_repo_configs table left in place for one release cycle.)
- **M6 — Webhook receiver extension** ✅ (POST /api/webhooks/github/pr-comments validates per-repo HMAC against managed_repos.github_webhook_secret_encrypted; upserts via the same pr_comments path as the poller with ingestion_source='webhook'; oauth-proxy configured to skip auth on /api/webhooks/; accepts GitHub ping; ignores non-PR issue_comment events, deleted action, etc., with 200 so GitHub doesn't retry.)
- **M7 — Inbox API + LLM auto-draft endpoint** ✅ (`GET /api/inbox` with status/repo/tenant filters, `GET /api/inbox/{uuid}` enriched with uc_links, `POST /api/inbox/{uuid}/status` for dismiss/reopen/drafted_to_uc transitions with uc_pr_comment_links provenance, `POST /api/inbox/{uuid}/draft-uc` calls UC Assist with a tailored user message framing the PR comment as scenario source material)
- **M8 — Inbox UI tab** ✅ (new "📬 Inbox" top-level nav item; split layout — list left / detail right; status chips for new/drafted/dismissed/all + per-repo dropdown; click a row to see full body + GitHub deep-links + existing UC drafts; `✦ Draft UC (LLM)` button surfaces explanation + YAML; `⎘ Copy YAML` for fast paste into the UC editor; sessionStorage hand-off to Use Cases tab; "After saving the UC, mark the comment as Drafted" prompt closes the loop on uc↔comment provenance)
- **M11a — Per-role path overrides + corpus projection parity (ADR-007)** ✅ (`metadata.role_paths.{role}` lets a single managed_repos row serve multiple roles with different sub-paths — e.g. DCM uses `architecture/` for spec but `dav/use-cases/` for corpus. `repos.resolve_root_path(repo, role)` is the resolver; spec + corpus projectors both use it. New `dav-source-corpus` ConfigMap shape mirrors `dav-source-spec` post-M5b: multi-source YAML list with namespace/repo_url/repo_branch/root_path, legacy single-source fallback preserved. `projector.project_corpus_sources` regenerates on every role=corpus CRUD; no Deployment rollout (Tekton reads the ConfigMap fresh per run). UI: per-role inline path input on the Repos form; Corpus panel converted to read-only mirror of Spec panel; "↻ Project all" button covers spec + corpus.)
- **M11b — Multi-source Tekton corpus sync + per-run selector (ADR-007)** ✅ (new `dav-git-sync-multi-corpus` Tekton Task mounts the projected ConfigMap, parses `sources`, clones each into a temp dir, copies the per-source `root_path` subtree into `/workspace/<corpus-subdir>/<namespace>/`. Legacy single-source fallback honors `legacy-uc-subpath` so non-UC YAML (workflows etc.) stays out of the engine's recursive walk. Pipeline-stage2 gains `corpus-namespaces` param; `engine.gather_corpus` reads the parent corpus-subdir so multi-namespace UCs all flow through one run. API: `RunTriggerIn.corpus_namespaces` + `validations.trigger_run`/`_mk_pipelinerun` pass-through. UI: New Run modal gains a corpus-source multi-select checkbox grid auto-populated from `role=corpus` repos; all-selected ≡ no filter sent.)
- **M12a — Bulk UC creation from transcripts (ADR-008)** ✅ (new `📋 Bulk import` entry point on the Use Cases tab; multi-step modal: paste text → LLM extracts N distinct UC drafts → review/edit cards → batch `POST /api/use-cases` → post-save Set assignment step with four modes [skip / create new Set / add all to existing Set / per-UC pickers]. Backend: `uc_assist.extract_bulk` + `POST /api/use-cases/bulk-from-text`, never writes to DB — returns proposed `{yaml_content, rationale, source_excerpt}` items for the client to persist. Drafts go in at `lifecycle_state = draft` and run the same engine validation as single-UC creation, so malformed extractions fail at save time, not later in the engine run. Set assignment uses existing `/api/sets` + `/api/sets/{id}/members`, no new server contract.)
- **M12b — UC editor wizard (ADR-008)** ✅ (`+ New` on the Use Cases tab now opens `ucWizardModal`, a 5-step guided flow: 1) scenario textarea + model picker + optional context, 2) auto-generate draft YAML via `/api/uc-assist` + inline `✦ Refine` chat that round-trips the current YAML, 3) parsed-fields preview alongside an editable YAML textarea with `✓ Validate` against `/api/use-cases/validate`, 4) tags chip input pre-filled from YAML + skip / new Set / existing Set assignment, 5) summary + read-only YAML preview + final save → `POST /api/use-cases` + optional `/api/sets/{id}/members`. `↓ Advanced YAML editor` footer link hands the in-progress YAML off to the legacy ucModal for power users at any step. `editUC` and `cloneUC` continue to open the legacy ucModal directly — the wizard is only for new-from-scratch authoring.)
- **Post-M12 — Per-run spec source filter** ✅ (mirror of M11b's corpus-namespaces selector for the spec side. New Run modal renders a vertical checkbox list of `role=spec` namespaces auto-populated from `/api/sources` when multi-source mode is active; all-checked ≡ no filter sent. `RunTriggerIn.spec_namespaces` → `validations.trigger_run`/`_mk_pipelinerun` → PipelineRun param `spec-namespaces` → `dav-run-corpus` task exports `DAV_SPEC_NAMESPACES_FILTER` env var → `engine.ai.prompts.build_stage2_system_prompt` appends a "spec source focus for this run" paragraph asking the LLM to prefer documents from the listed namespaces and note any cross-namespace lookups. Soft enforcement only — the MCP itself still serves every registered spec namespace.)
- **Post-M12 — LLM-bound endpoint timeout chain** ✅ (every layer between client and API was hitting its own default short timeout on long LLM calls — route 30s, oauth-proxy 30s, nginx 60s — surfacing as 504/502 with the only useful signal in `oauth-proxy`'s "timeout awaiting response headers" log. Lifted all three to 600s to match `uc_assist.extract_bulk`'s own httpx timeout: `haproxy.router.openshift.io/timeout=600s` on the UI Route, `--upstream-timeout=600s` on the oauth-proxy sidecar, `proxy_read_timeout 600s` on the `/api/` location in the nginx ConfigMap.)
- **Post-M12 — API init container multi-source fix** ✅ (`git-clone-corpus` initContainer on `dav-review-api` referenced `dav-source-corpus.data.repo_url`/`repo_branch` with `optional: false`; once M11a converted the ConfigMap to multi-source shape those keys vanished and kubelet refused to create the container → `Init:CreateContainerConfigError` blocked rollouts. Made the env vars optional and added a shell branch that no-ops gracefully when they're absent — Tekton's `dav-git-sync-multi-corpus` task owns the real clone now and the API's directory-mode loader already tolerated an empty `/data/repo`.)
- **Post-M12 — Engine cross-turn tool-call dedup** ✅ (live multi-UC run surfaced a real failure mode: model called `search_docs(query="orchestration workflow")` three times across turns 7/9/11 — Qwen3 lost track of its own prior calls as context grew past ~20K tokens. Engine commit `bfb62ff` adds (1) `self._call_history` per-run state in `Stage2Agent` so cross-turn duplicates short-circuit with a `⛔ DUPLICATE-CROSS-TURN` marker carrying the original turn + tool_call_id + 400-char result preview, (2) a "scan your prior tool calls before re-issuing" paragraph appended to the stage-2 system prompt (`STAGE2_PROMPT_VERSION` 1.5 → 1.6), and (3) an end-of-run `kind=summary` turn record carrying `cross_turn_duplicates_blocked`, `distinct_calls`, `section_title_misses`, `too_large_handles`, `total_tokens` so the UI's prompts panel renders the per-UC dedup count inline. Verified live: the 2026-05-28 Barclays A/B run blocked 1 cross-turn duplicate on UC #3 and surfaced it via the summary record.)
- **Post-M12 — Qwen3 context-window A/B** ✅ (paired the dedup commit with a vLLM context bump from native 32K → 86K via YaRN rope-scaling on the llm-serving Qwen3-32B runtime. A/B tested an aggressive 128K + fp8-KV alternative against the safe 86K + bf16-KV baseline on the Barclays Set 3 corpus — full methodology, per-UC results, and decision in [`docs/experiments/ab-fp8kv-128k-vs-bf16kv-86k.md`](experiments/ab-fp8kv-128k-vs-bf16kv-86k.md). **Decision: stayed at 86K bf16-KV.** fp8-KV at 128K recovered 1 UC (a `--max-tokens` artifact, not a context-pressure fix) but cost 5–15 confidence points on 3 of 6 UCs (uncalibrated fp8 attention scaling factors, flagged by vLLM at start) and doubled wall time (21 → 44 min). The dedup engine code is the load-bearing fix; extra context past 86K delivered no measurable recall improvement because per-turn context usage stayed under 30K on this corpus anyway.)
- **Post-M12 — Two-pass stage-2 (exploration → findings → synthesis)** ✅ (information-preservation pass for gap analysis on UCs whose tool-call exploration accumulates more context than the 86K vLLM ceiling can hold alongside the 16384 `--max-tokens` reservation. Default-on engine behavior — set `DAV_STAGE2_TWO_PASS=0` to fall back to single-pass. Pass 1 explores the spec via MCP and emits a verbose structured findings JSON capturing every section, capability, constraint, cross-reference, and potential gap it observed; pass 2 starts in a fresh context with the findings + the original UC + still-available MCP tools, re-fetches anything pass 1 compressed too aggressively, and emits the canonical Analysis JSON. Both passes share the dynamic-max-tokens + cross-turn-dedup + spec-namespaces-scope plumbing already in agent.py. Run summary records `pass: pass1` / `pass: pass2` per turn so the UI prompts panel can render per-pass timelines. `STAGE2_PROMPT_VERSION` 1.7 → 1.8. New prompts in `engine/src/dav/ai/prompts.py`: `build_pass1_findings_system_prompt`, `build_pass2_analysis_system_prompt`, `build_pass2_user_prompt`.)
- **Post-M12 — Infrastructure-confidence per UC + run aggregate + pre-flight hint** ✅ (synthesizes the per-UC infrastructure-induced quality assessment from the engine's existing summary counters [`context_overflow_retries`, `budget_capped_turns`, `cross_turn_duplicates_blocked`, `section_title_misses`, `out_of_scope_blocked`] into a `{label, score, signals, explanation, recommendations}` object surfaced on every Analysis's `metadata.infrastructure_confidence`. Phase A: engine `Stage2Agent._compute_infrastructure_confidence()` + `AnalysisMetadata.infrastructure_confidence` field. Phase B: migration 013 adds five columns to `uc_analyses`; ingest reads them from the analysis YAML; `GET /api/use-cases/{uuid}/runs` surfaces the per-UC chip; new `GET /api/runs/{name}/infra-confidence-aggregate` returns a per-run breakdown; UI renders an inline chip in the Test history table with a tooltip carrying explanation + recommendations. Phase C: new `GET /api/runs/preflight-hint?set_id=X` looks at the last N runs for a Set and returns a warning banner if 2+ of them had any UC at `low` or `compromised`; New Run modal renders the banner at top of step 1 before the operator triggers. **Distinct from analytical confidence** — a UC can be analytically high-confidence while infrastructure-compromised (model committed early due to budget pressure without enough exploration) and vice versa.)
- **Post-M12 — Per-run inference model selector (tracked alternative)** — the New Run modal's model picker already flows `inference_endpoint` + `inference_model` through `RunTriggerIn` → PipelineRun params → stage-2 task, so operators can already override Qwen3-32B (86K context) with Sonnet 4.6 / Opus 4.7 (200K native context) on a per-run basis for deep-exploration UCs that two-pass on the local model can't fully fit. **Tracked as an alternative path** to two-pass; recommended for very long corpora when the API token cost is acceptable. Future enhancement: surface "switch to long-context model" as an inline suggestion on the Runs tab when a run completes with non-zero `context_overflow_retries` or `budget_capped_turns` on its summary records.
- **Post-M12 — run-corpus `--max-tokens` bump 6144 → 16384** ✅ (the A/B baseline's UC #6 failure was the canary: that UC's analysis is genuinely a 44-component / ~9000-token emission and 6144 chopped it mid-string. Iterated 6144 → 8192 → 16384; 8192 still failed UC #6 because the final response was 33,907 chars. 16384 cleared all 6 UCs cleanly in `dav-stage2-console-970095` with zero quality regression vs the baseline and +22% wall time concentrated entirely in UC #6's larger emission. The `--max-tokens` raise is zero-VRAM (soft cap, not pre-allocated) and zero-time when the model doesn't actually need it — Qwen3 stops at end-of-stream naturally on the shorter UCs.)
- **Post-M12 — Project-scoped Arch Review default model** ✅ (closed the Config-page gap noticed during the Qwen3.6-27B MTP A/B rollout: the arch-review feature — Review & Plan tab, `/api/arch-review` endpoint — had a per-model gate column [`model_configs.use_arch_review`] and a settable storage table [`model_defaults`] but no Config UI to set the project default. Three changes: (1) UI: new picker panel "Arch Review model" on Config under "UC Assist model", filtered to `use_arch_review=true` rows, mirroring the Evaluation default; left-nav entry added under AI Models so the section is discoverable; (2) API: `_VALID_DEFAULT_KEYS` extended with `arch-review`; `/api/arch-review` falls back to the project default when caller omits `model_config_id` / `endpoint_url+model_id`; explicit overrides still win; (3) UX polish: dropped the "Default" prefix from all three picker titles ["Evaluation model", "UC Assist model", "Arch Review model"]; added a Save button to the UC Assist panel for consistency with the other two pickers. Out of scope/deferred: auto-populating the Run & Plan tab's per-call model selector from the project default — today the picker still enforces explicit selection per call; the server-side fallback covers external callers and direct API usage.)
- **Post-M12 — Qwen3.6-27B-FP8 (MTP) on dual R9700** ✅ (A/B candidate registered as DAV's evaluation + UC Assist + arch-review default 2026-05-28. Hybrid Gated DeltaNet [3 linear-attention + 1 full-attention per 4 layers, 64 layers total], native 256K context, inline MTP heads activated via vLLM `qwen3_next_mtp` speculative-decoding method. Cold-boot measurements at 98K target: weights 15.06 GiB, KV pool 10.85 GiB / 304,378 tokens, 3.10× concurrent batches at full ceiling — vs Qwen3-32B-FP8's ~88K bf16-KV ceiling with ~no headroom. Boot total 425s [autotune + torch.compile dominant; subsequent restarts cache-warm]. Custom ROCm paged-attention kernel falls back to Triton for hybrid layout — ~10–20% decode penalty until upstream support lands. Full deployment + DMZ route + spamllm repointing committed in `llm-serving 6b23ae6/681c4ec` and `spamllm-poc a9206d3`. First A/B run on OSAC set: `dav-stage2-console-016622` triggered 2026-05-29 01:03 UTC against the same 15-UC set that produced PipelineRunTimeout on qwen3-32b 2026-05-28 19:42.)
- **Post-M12 — Per-(model, use) sampling profile system** ✅ (operators tune sampling per `(model_config_id, use_key)` via SQL/curl without engine rebuilds. Migration 014 adds `model_configs.capabilities` JSONB + new `model_use_profiles` table with use_key whitelist `{evaluation_verification, evaluation_explore, evaluation_reproduce, arch_review, uc_assist, enhancement}`. API endpoints `GET/PUT/DELETE /api/models/{mid}/profiles/{use_key}`. Engine: `EndpointConfig` grows `capabilities` + `use_key`; body builder drops capability-forbidden params (e.g. `min_p` under `speculative_decoding=true`); per-use profile resolves between mode defaults and CLI overrides; `client.effective_sampling()` denotes `{use_key, sent, dropped, capabilities}` into `run-summary.yaml` top-level AND `AnalysisMetadata.effective_sampling` per UC. Threaded through Tekton via `@/tmp/dav-capabilities.json`-style file syntax to avoid JSON-with-spaces argv-splitting. Commits 177863f / 614a878 / cbf15a2 / d367190 / 0cd7b5f.)
- **Post-M12 — `stage2_two_pass` per-run override** ✅ (lets operators flip between two-pass and single-pass stage-2 per run without engine rebuild. New Tekton task param `stage2-two-pass` threaded through Pipeline + `validations.trigger_run` + `RunTriggerIn.stage2_two_pass`; default `"1"` preserves two-pass behavior. Tested 2026-05-29 path 3 of the Qwen3.6-27B investigation — confirmed two-pass cascade was NOT the failure root cause [single-pass also failed], the tool-call parser was. Surfaced in commit `ecb7aac`.)
- **Post-M12 — DAV model sweep 2026-05-29** ✅ (full-day sweep of candidate models against OSAC set + arch-review + enhancement workloads. Tested: Qwen3.6-27B-FP8 [both `hermes` and `qwen3_xml` parsers, MTP on/off, multiple sampling profiles], Qwen3-Coder-30B-A3B-Instruct-FP8 [`qwen3_coder` parser, prompts v1.8 + v1.9]. **All non-baseline candidates blocked.** Root cause for Qwen3.6 failures: upstream vLLM 0.21.x tool-parser bug, filed at vllm-project/vllm#43713, fix proposed at PR #43714. Yesterday's "Qwen3.6 incompatible with DAV's prompts" attribution was wrong — it was always the parser. Coder model exhibits separate "fish for section titles" behavior pattern that consumes the agent-loop budget regardless of prompt tightening [STAGE2_PROMPT_VERSION 1.8 → 1.9 in commit `9dbb400`]. Single-call workflows on Coder [arch-review, enhancement] work fine. End-state: Qwen3-32B-FP8 restored as canonical default. Full memo at `/tmp/dav-model-sweep-memo.md`. Drafts ready for `/tmp/vllm-issue-43713-comment.md`, `/tmp/kyuz0-image-rebuild-issue.md`, `/tmp/qwen3xml-parser-patch-plan.md` [Plan B locally-patched image].)
- **Post-M12 — R9700 persistent kernel cache + stable abstraction route** ✅ (two infrastructure wins shipped during the model sweep that pay back regardless of which model is active. (1) New PVC `r9700-kernel-cache` [20 Gi RWX CephFS] mounted at `/var/cache/llm/{{.Name}}/` in all three R9700 ServingRuntimes via `TRITON_CACHE_DIR`/`VLLM_CACHE_ROOT`/`TORCHINDUCTOR_CACHE_DIR`/`AITER_JIT_DIR` env vars. First boot still autotunes [~15 min]; subsequent restarts of the same model should drop to ~3 min as the persisted Triton + torch.compile artifacts replay. Validated cache populated on Qwen3-32B first boot: 62 MB triton across 1017 files + 106 MB vLLM across 87 files. (2) Stable abstraction Service `r9700-llm` + DMZ Route `r9700.llm.ocp.roadfeldt.com` select on `gpu-tier=r9700` label and follow whichever R9700 ISVC is currently up. Every R9700 ServingRuntime publishes `r9700-llm` as a secondary `--served-model-name` alias [single-space form, not `=` — argparse `nargs="+"` requires it]. Consumers like spamllm pin endpoint+alias once and never re-config across ISVC swaps. Commits `llm-serving c6e910d` and `73fdc0b`.)
- **Post-M12 — MCP filename-shortcut + corpus-agnostic naming** ✅ (root-caused the 200+ section_title-miss-per-UC pattern on OSAC stage-2 runs that was previously chalked up to model behavior. The model calls `search_docs` (which correctly returns full handles like `dcm/architecture/DCM-Capabilities-Matrix.md`), then constructs shortened "<ns>/<filename>" forms for subsequent `get_document_section` calls because the full nested path is verbose in conversation context. The MCP's `_resolve_handle` only accepted (a) the exact full handle or (b) an exact unqualified relpath; the shortcut form missed every time, surfacing as "section not found" cascading into 504 timeouts as context grew. Added a third namespace+tail-segment fallback that resolves the shortcut to the unique document in that namespace whose path ends with the trailing segment. Same pass dropped DCM-specific naming throughout the engine (`_DCM_REFERENCE_PROFILE` → `_GENERIC_REFERENCE_PROFILE`, `fall_back_to_dcm` → `fall_back_to_generic`, etc.) — DAV ships consumer-agnostic and the built-in fallback profile is now a generic platform-architecture placeholder; consumers load their own ConsumerProfile externally. Commits `9804875`, `94fe3d0`.)
- **Post-M12 — MCP corpus refresh: scheduled CronJob + manual UI button** ✅ (closed the silent-staleness gap surfaced during the OSAC investigation. Previously, `dav-docs-mcp`'s served content was only refreshed at pod start; after 25h pod uptime, edits to spec/corpus repos weren't visible until an operator `oc rollout restart`. Two surfaces shipped: (1) hourly CronJob `dav-docs-mcp-refresh` with narrow RBAC [apps/deployments get+patch on the single deployment by resourceName, not the broader rollouts subresource]. Schedule, enabled flag, deployment name, and SA name all come from role defaults `dav_docs_mcp_refresh_*` so they're inventory-configurable. (2) Config UI panel under Pipeline Sources → MCP refresh with "↻ Refresh now" button + status panel (last_refreshed_at, source, by, rollout state). New endpoints `POST /api/mcp/refresh-now` and `GET /api/mcp/refresh-status`. The CronJob's schedule itself is NOT yet UI-editable — that's a tracked follow-up that needs a reconciler updating the K8s CronJob spec from a DB row. Commit `eaa9c73`.)
- **Post-M12 — MCP `get_capability` tool + stage-2 prompt v1.10** ✅ (root-caused the stubborn ~230 section_title-miss-per-run rate that remained AFTER the MCP filename-shortcut fix. With shortened handles now resolving, the OSAC re-test produced 0/15 and 233 misses — same as before. Investigation: the model was treating each capability row ID inside DCM-Capabilities-Matrix.md [`OBS-001`, `OBS-002`, ...] as a `section_title` argument to `get_document_section`, when they're table-row identifiers, not markdown headers. Every call missed. New tool `get_capability(capability_id)` indexes markdown-table rows whose first cell matches `[A-Z]{2,5}-\d{3}` and returns `{section, table_header, row}` as JSON. 396 capability rows indexed across the current spec corpus on first deploy. Stage-2 prompt v1.10 adds a single bullet pointing the model at the new tool and explicitly warning against the section_title misuse. Stage-2 prompt v1.9 was a regression [9dbb400 reverted in 3d36c3c, see model-sweep memo] so v1.10 skips ahead. Commit `1df7ae5`.)
- **Post-M12 — `severity: 'medium'` alias for `'moderate'`** ✅ (smaller parser-robustness fix surfaced by OSAC seed UC `uc-seed-006a`: model produced a full pass-1 analysis [358s wall time] then crashed on `ValueError: invalid severity label 'medium'` because the canonical 41-60 band label is `moderate`. The model carried `medium` over from the confidence axis which IS low/medium/high. Added a `_SEVERITY_ALIASES = {"medium": "moderate"}` lookup applied before the membership check in `normalize_severity`'s string and dict paths. Only `medium` aliased — `high`/`low` would be ambiguous between `major`/`critical` and `minor`/`advisory` so they stay un-aliased and continue to error. Unit test covers both code paths + case-insensitivity. Commit `b6a6546`.)
- **Post-M12 — `qwen3-32b` HAProxy route timeout 30s → 600s** ✅ (one more bottleneck behind the OSAC 504 cascade. The KServe-auto-generated Route inherited the cluster default ~30s; long stage-2 turns at 80-120K context routinely exceed it. The stable `r9700-llm` Route was already 600s. Annotation on the ISVC `haproxy.router.openshift.io/timeout: 600s` propagates through `odh-model-controller` to the auto-managed Route and survives reconciliation. Persisted in `llm-serving/05-inference-services/qwen3-32b.yaml` commit `llm-serving d40a1bb`.)

The four entries below were the second OSAC failure-chain pass (2026-05-30, Opus 4.8). Run `092466` went 0/15 → **11/15** after the first pass; the remaining 4 failures were peeled off one at a time, each fix exposing the next shallower bug.

- **Post-M12 — MCP `get_document_section` oversized-section cap** ✅ (the 4 residual `092466` failures were all cost/billing UCs with an identical fingerprint: the model fetched the DCM Foundational Capabilities Matrix as one section — 87,686 chars / ~22K tokens / 331 rows — which bloated context and triggered a ~15K-token runaway generation that blew the 600s route timeout. `get_capability` exists so the model needn't dump the matrix, but nothing *capped* the dump. Now any section over `_MAX_SECTION_CHARS` (32000) returns a 6000-char head + drill-down guidance: capability-matrix rows redirect to `get_capability(id)` with detected ID prefixes/samples, otherwise it lists narrower subsection titles. Corpus-agnostic — trips on size alone, no document special-cased. Verified live: matrix response 87,686 → ~8,192 chars (92% smaller), all 331 rows / 41 prefixes detected. Recovered 3 of the 4. Commit `a0dee22`.)
- **Post-M12 — stage-2 `max-tokens` 16384 → 10240 (deployment variable)** ✅ (the last matrix-cap-resistant failure was pure physics: `16384 tokens ÷ ~20 tok/s ≈ 745s > 600s` route timeout. A *legit* verbose analysis tops out ~9000 tokens [the documented UC #6 greenfield case] = ~450s, already under 600s; only degenerate over-generations land in the 13K–16K window that can't finish in time, and 16384's "margin" is exactly that window. The honest ceiling on this stack is `throughput × timeout ≈ 10,800 tokens`. Note `run_corpus.py:803`: the Tekton task's `--max-tokens` arg wins over any `model_use_profile`, so the task default is authoritative. Made it the inventory var `dav_stage2_max_tokens` (default 10240) per the "system-specific settings belong in deployment vars" rule — covers UC #6 with margin, keeps every generation under the route timeout at ~20 tok/s, tunable in lock-step with the timeout on a faster GPU. Eliminated 504s entirely. Commits `0f6346d` [+ engine task re-apply].)
- **Post-M12 — severity alias extended to full low/medium/high scale** ✅ (with 504s gone, run `101108` surfaced an otherwise-complete 270s analysis killed by `invalid severity label 'low'`. The earlier `b6a6546` aliased only `medium`, reasoning low/high were "too ambiguous" — reality refuted that: the model emits the whole 3-bucket low/medium/high scale borrowed from the confidence axis. Now `low→minor`, `medium→moderate`, `high→major` — the middle of the 5-level range, ordering preserved, `advisory`/`critical` reserved for explicit use. Covers the entire alternate vocabulary, not one label. Also: an aliased dict label like `{label:high, score:85}` carries a confidence-style score outside `major`'s 61-80 band; aliased labels now use the canonical default score instead of raising. Unknown labels [e.g. `catastrophic`] still raise. Commit `10fe660`.)
- **Post-M12 — run-drawer sparklines stay visible during live runs** ✅ (`renderRunDrawerMetrics()` wipes `#rdGpus` + vLLM cell innerHTML on every 3s poll and rebuilds tiles without re-injecting sparklines, while the timeseries only re-fetches every 60s — so GPU power/gfx and vLLM tps/running sparklines flashed in for one poll after each fetch then vanished for ~57s. Completed runs don't poll, so they rendered once and persisted [visible in historical runs only]. `_rdSparklines` is cached between fetches; re-rendering it at the end of every metrics render keeps the SVGs alive across the rebuild. Commit `75065be`.)
- **Post-M12 — self-improvement loop, Phase 1 (diagnose & propose)** ✅ (the OSAC 0/15→15/15 stabilization was a manual loop — observe failure signature → root-cause → propose a typed fix → A/B → keep/revert. Phase 1 turns the first half into a feature: `failure_taxonomy.py` classifies a run's `failures/*.error.txt` into typed signatures [route_504, output_truncation, severity_reject, fishing, context_overflow, …]; `diagnose.py` turns those into ranked typed proposals `{kind,target,rationale,proposed_change,predicted_effect,confidence}` via a rules layer that **re-derives the exact fixes made by hand this session** [proven by `test_self_improvement.py`] plus an optional LLM second opinion with the hard guardrails baked into its system prompt [classify before editing prompts; never 'harden to stop a behavior'; respect throughput×timeout]. Proposals are **filed for review, never applied** [migrate_015: `run_diagnoses` + `improvement_proposals`; endpoints `POST/GET /api/diagnose/{run_id}`, `GET /api/improvement-proposals`, `POST .../review`]. Validated live on run 109569. Design + phased plan in [self-improvement vision](dav-self-improvement-vision.md). Commit `7828547`.)
- **Post-M12 — self-improvement loop: review-queue UI** ✅ (the operator surface for Phase 1: a top-level **"Improve" tab** [🩺] with a two-pane review queue — status-filtered proposal list + a "diagnose a run" picker [LLM toggle] on the left, proposal detail + Accept/Reject [review-only, two-click] on the right, plus a run-drawer "🩺 Diagnose" button that runs the diagnoser in context. `/api/diagnose/{id}` now resolves a Tekton run name OR a workspace run_id so both entry points work. Built on the Inbox tab idiom. Next: Phase 2 — A/B-gated apply + auto-revert for prompt/profile kinds. Commit `f92c799`.)

M5-M8 enable PR comments as UC drivers: comments from configured
issue-source repos are pulled into a curation inbox, with an LLM-assisted
draft of a structured UC YAML for human review before saving. M11 enables
single-repo / multi-role registrations with per-use subpaths and brings
multi-source parity to the corpus pipeline.

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
- **Review Console v1 is operational.** The new ops frontend (Runs/Results/Use Cases/Config) was shipped 2026-05-21 and deployed via Ansible. The workspace PVC is mounted read-only by the API pod.
- Start with **Session A (Negative UCs)** unless explicitly redirected to console work
- The Phase 4 baseline report (`dav-phase-4-baseline-report.md`) is the reference for current corpus verdict shape
- The DCM session is in flight on the three gaps from Phase 4 — when those land, they'll produce PRs that benefit from Mode B existing, which is incentive for sequencing C/D before C1
- Tags to be aware of: DAV `v0.1.12` (already pushed)

---

*Document maintained by the DAV project. Edit as the roadmap evolves.*

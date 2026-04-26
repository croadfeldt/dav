# Operator Runbook — Deploy + Validate + Run

This runbook covers the path from an empty cluster to a completed DAV-against-DCM corpus run with structured findings. Single deployable artifact. Sequential phases; each phase has a verification gate.

**Target cluster:** OpenShift 4.18+, Pipelines operator pre-installed
**DAV repo:** local clone of your DAV repo (this codebase)
**Inference endpoint:** OpenAI-compatible `/v1` endpoint, LAN-reachable from cluster pods
**Spec repo:** Your consumer spec repo (DCM for first run)
**Corpus repo:** Your consumer corpus repo (`dcm-self-test-corpus` for first run)

This runbook uses shell variables for site-specific values. Set them once at the start of your session:

```bash
# Set these to your actual values
export DAV_CLUSTER_API="https://api.your-cluster.example.com:6443"
export DAV_INFERENCE_ENDPOINT="http://your-inference-host.local:8000/v1"
export DAV_SPEC_REPO="https://github.com/your-org/dcm.git"
export DAV_CORPUS_REPO="https://github.com/your-org/dcm-self-test-corpus.git"
export DAV_WEBHOOK_HOST="dav-webhook.apps.your-cluster.example.com"
export DAV_REPO_DIR="$HOME/git/dav"            # local checkout of DAV
export DAV_CORPUS_DIR="$HOME/git/dcm-self-test-corpus"  # local checkout of corpus
```

Subsequent commands in this runbook reference these variables. Copy-paste verbatim works.

**Estimated wall time end-to-end:** ~30 min deploy + ~15 min smoke test + ~10 min webhook setup + 3-6 hours real run = **half-day total**, most of which is the real run running unattended.

---

## Phase 0: Pre-flight

Before touching the cluster.

### 0.0 — Create site-specific config (`vars.local.yaml`)

Site-specific values (inference endpoint, cluster apps domain, consumer repo URLs, in-cluster service DNS) are **not** committed to the repo. They live in `vars.local.yaml`, gitignored, auto-loaded by Ansible.

```bash
cd ${DAV_REPO_DIR}
cp ansible/inventory/group_vars/all/vars.local.yaml.example \
   ansible/inventory/group_vars/all/vars.local.yaml

$EDITOR ansible/inventory/group_vars/all/vars.local.yaml
```

Required values (all 6 must be filled in or the playbook will fail-fast at the first task):

| Variable | Example | Notes |
|----------|---------|-------|
| `inference_primary_endpoint` | `http://your-host.local:8000/v1` | LAN-reachable from cluster pods |
| `inference_fallback_endpoint` | `http://vllm-tier3.{{ dav_namespace }}.svc:8000/v1` | In-cluster service DNS; the namespace template is fine as-is |
| `consumer_spec_repo_url` | `https://github.com/your-org/your-spec-repo.git` | Architecture spec repo (DCM for first run) |
| `consumer_corpus_repo_url` | `https://github.com/your-org/your-corpus-repo.git` | Use case corpus repo |
| `dav_webhook_hostname` | `dav-webhook.apps.<cluster>.<basedomain>` | Tekton EventListener route hostname |
| `review_console_hostname` | `dav-review.apps.<cluster>.<basedomain>` | Review console UI route hostname |

**Verification gate:** `git status` should NOT show `vars.local.yaml` as new/modified (it's gitignored). All 6 vars filled in with real values.

### 0.1 — Re-encrypt the vault

The bundled `ansible/inventory/group_vars/all/vault.yaml` contains stale values from prior deployments. Decide what's current and re-encrypt.

```bash
cd ${DAV_REPO_DIR}

# View current contents (will prompt for old vault password)
ansible-vault view ansible/inventory/group_vars/all/vault.yaml
```

Expected vault keys (verify each is present and current):

| Key | Purpose | If wrong |
|-----|---------|----------|
| `github_webhook_secret` | HMAC signing secret for GitHub webhook deliveries | Generate new (`openssl rand -hex 32`); update GitHub webhook config to match |
| `review_console_oauth_client_id` | OAuth client ID for review console UI | Get from your OpenShift OAuth client config |
| `review_console_oauth_client_secret` | OAuth client secret | Same |
| `review_console_db_password` | Postgres password for review console | Generate new (`openssl rand -base64 24`) |
| `review_console_session_secret` | Session signing key | Generate new (`openssl rand -hex 32`) |

If any are stale, edit:

```bash
ansible-vault edit ansible/inventory/group_vars/all/vault.yaml
```

If you want a clean slate (recommended for a fresh deploy):

```bash
# Back up the current encrypted file
cp ansible/inventory/group_vars/all/vault.yaml{,.bak}

# Use the example as a template
cp ansible/inventory/group_vars/all/vault.yaml.example /tmp/new-vault.yaml

# Edit values
$EDITOR /tmp/new-vault.yaml

# Encrypt with your password
ansible-vault encrypt /tmp/new-vault.yaml \
    --output ansible/inventory/group_vars/all/vault.yaml

rm /tmp/new-vault.yaml
```

**Verification gate:** `ansible-vault view ansible/inventory/group_vars/all/vault.yaml` shows the values you expect, no errors.

### 0.1a — Create the vault password file

`ansible.cfg` at the repo root points to `.vault_pass` for unattended vault unlock. Create the file:

```bash
cd ${DAV_REPO_DIR}
echo "your-vault-password-here" > .vault_pass
chmod 600 .vault_pass
```

`.vault_pass` is gitignored — verify it's not staged with `git status` (should not appear). After this, `ansible-playbook ansible/playbook.yaml` runs without `--ask-vault-pass` because the cfg auto-loads the password from this file.

If you'd rather type the password every time, skip this step and add `--ask-vault-pass` to all `ansible-playbook` invocations in this runbook.

### 0.2 — Add `dav-version.yaml` to the corpus repo

The engine reads `dav-version.yaml` from the corpus root to populate `consumer_version` in every Analysis output. Without it, `consumer_version` is empty and analyses can't be tied to a specific corpus state.

```bash
cd ${DAV_CORPUS_DIR}
cat > dav-version.yaml <<'EOF'
# DAV consumer version manifest. Read by the engine to populate
# AnalysisMetadata.consumer_version on every analysis output.
consumer_id: dcm
consumer_version: 1.0.0
EOF
git add dav-version.yaml
git commit -m "Add dav-version.yaml for consumer_version provenance"
git push
```

Bump `consumer_version` whenever the corpus or its supporting spec content changes meaningfully. SemVer is fine.

**Verification gate:** `dav-version.yaml` is on `main` of `${DAV_CORPUS_REPO}`.

### 0.3 — Confirm the inference endpoint is reachable from your laptop

```bash
curl -sf ${DAV_INFERENCE_ENDPOINT}/models
```

Expected: a JSON response listing the loaded model. If this fails, the cluster won't reach it either — fix it before deploying.

### 0.4 — Confirm cluster login

```bash
oc whoami
oc cluster-info
```

Expected: your user, the your cluster API cluster API URL.

### 0.5 — Confirm `dav` namespace doesn't already exist

```bash
oc get ns dav 2>&1 | grep "NotFound" && echo "good — namespace will be created fresh"
```

If `dav` already exists from a prior run, decide whether to delete it (`oc delete ns dav`) or keep it. The playbook is idempotent and will work either way, but a fresh namespace eliminates state-drift questions.

---

## Phase 1: Deploy

```bash
cd ${DAV_REPO_DIR}
ansible-playbook ansible/playbook.yaml
```

Type vault password when prompted. The playbook runs through:

1. OpenShift Pipelines operator check (no-op if already installed)
2. Namespace, RBAC, PVCs (`dav-workspace`, `dav-model-cache`)
3. Source ConfigMaps for spec + corpus repos
4. vLLM tier-3 fallback (NVIDIA 4060 Ti on a worker node — only deploys if the GPU node label matches; harmless if it doesn't)
5. MCP server build + deploy (`dav-docs-mcp`)
6. Engine image build + Tekton task install
7. Tekton pipeline + triggers + event listener + webhook route
8. Review console (UI + API + Postgres)
9. Validation tasks

**Estimated time:** 15-30 minutes. Most of it is image builds (engine, MCP, review console UI/API). If image builds succeed quickly and pods come up, total can be under 15 min.

### 1a — First-time secrets

The `secrets.yaml` task is tagged `never` and doesn't run on a normal invocation. Seed secrets with:

```bash
ansible-playbook ansible/playbook.yaml --tags secrets
```

Verify:

```bash
oc get secret github-webhook-secret -n dav
oc get secrets -n dav | grep -E "review-console|oauth"
```

### 1b — Verify the deploy

```bash
# All pods up?
oc get pods -n dav
# Expect: dav-docs-mcp, dav-review-api, dav-review-db, dav-review-ui all Running.
# vllm-tier3 Running only if the GPU node label matched.

# Pipeline registered?
oc get pipeline.tekton.dev dav-stage2 -n dav
oc get task -n dav
# Expect dav-git-sync and dav-run-corpus tasks.

# MCP server healthy?
oc exec -n dav deploy/dav-docs-mcp -- curl -sf http://localhost:8080/health
# Expect: 200 OK with health JSON.

# Webhook route admitted?
oc get route dav-webhook -n dav -o jsonpath='{.spec.host}{"\n"}'
# Expect: ${DAV_WEBHOOK_HOST}
```

**Verification gate (mandatory before Phase 2):**
- All expected pods Running
- `dav-stage2` pipeline registered
- MCP `/health` returns 200
- Webhook route admitted (host resolves)

If any fails, **stop**. Diagnose before proceeding. Common causes:
- Pod ImagePullBackOff → BuildConfig failed; check `oc get builds -n dav`
- MCP unhealthy → spec ConfigMap wrong or git clone failing in init-container; check `oc logs -n dav deploy/dav-docs-mcp -c init-clone`
- Webhook route not admitted → cluster ingress controller issue; check `oc describe route dav-webhook -n dav`

---

## Phase 2: Smoke Test (BookCatalog Exemplar)

Before pointing at the real DCM corpus, prove the pipeline works end-to-end with a known-good synthetic UC. The two BookCatalog exemplars under `examples/exemplar-ucs/` are designed for this.

### 2.1 — Run via the engine pod (manual, fast)

```bash
# Open a shell in the engine container
oc run -n dav --rm -it \
    --image="image-registry.openshift-image-registry.svc:5000/dav/dav-engine:latest" \
    --restart=Never \
    --serviceaccount=dav-pipeline-sa \
    smoke-test \
    -- /bin/bash

# (now inside the pod)
mkdir -p /tmp/smoke
python -m dav.stages.stage2_analyze \
    --use-case /workspace/dav/examples/exemplar-ucs/exemplar-bookcatalog-register-happy-path.yaml \
    --output /tmp/smoke/analysis.yaml \
    --inference-endpoint ${DAV_INFERENCE_ENDPOINT} \
    --inference-model qwen \
    --mcp-url http://dav-docs-mcp.dav.svc:8080 \
    --mode reproduce \
    --no-enable-thinking \
    --max-tool-calls 30

cat /tmp/smoke/analysis.yaml
```

**Expected wall time (calibrated against dual-R9700 split-layer Q8_0 32B):**
- Reproduce mode (N=1): ~30-40 min per UC for moderate-complexity UCs
- Verification mode (N=3): ~90-120 min per UC

The agent's per-turn latency grows roughly linearly with conversation context size because prompt caching is disabled (intentional, for determinism). A 50k-token context per turn takes ~5-9 min through split-layer; a 10-turn analysis hits 30-40 min wall.

For a 2-UC BookCatalog smoke test in reproduce mode, expect ~60-80 min total. Plan ahead: the default Tekton PipelineRun timeout is 1h cluster-wide, which is shorter than this. Pass `--pipeline-timeout 24h` on `tkn pipeline start` (already in the command below) to avoid mid-run timeouts.

If you want a faster smoke-test feedback loop, run a single UC out of the engine pod via `oc run` ad-hoc (option 2.1 above) — that path can complete one UC in similar time but skips Tekton overhead, and you get clearer engine logs for debugging.

If you don't have `oc run` access to ad-hoc image, alternative is to trigger the full pipeline against the BookCatalog UC (slower but more representative):

### 2.2 — Run via the Tekton pipeline (production path)

```bash
tkn pipeline start dav-stage2 \
    -n dav \
    --workspace name=shared-data,claimName=dav-workspace \
    --param consumer-spec-repo-url=${DAV_SPEC_REPO} \
    --param consumer-spec-repo-branch=main \
    --param consumer-corpus-repo-url=${DAV_REPO} \
    --param consumer-corpus-repo-branch=main \
    --param corpus-uc-subpath=examples/exemplar-ucs \
    --param mode=reproduce \
    --pipeline-timeout 24h \
    --serviceaccount dav-pipeline-sa \
    --use-param-defaults \
    --showlog
```

(Note the corpus parameters point at the DAV repo itself for the smoke test, since the BookCatalog exemplars live there. After smoke test, switch to the real DCM corpus.)

**Expected:** PipelineRun goes through `cleanup-workspace` → `sync-spec` ∥ `sync-corpus` → `run-corpus`. The cleanup task wipes any prior `spec/` and `corpus/` directories from the shared PVC; the sync tasks fresh-clone in parallel; then run-corpus iterates the UCs. Two analyses written to the workspace under `/results/<run-id>/analyses/`. Both should have non-error verdicts.

To inspect after:

```bash
# Find the latest PipelineRun
LATEST_PR=$(oc get pipelinerun -n dav --sort-by=.status.startTime -o name | tail -1)
oc describe -n dav $LATEST_PR

# Tekton's `tkn pr logs` shows logs:
tkn pr logs -f $(echo $LATEST_PR | cut -d/ -f2) -n dav
```

### 2.3 — Verification gate

The smoke test passed if:
- ✅ The reproduce-mode run completes without runner errors
- ✅ The output Analysis YAML validates against the schema (no parse errors when re-loading it)
- ✅ The verdict is plausible (`supported` for happy-path, `partially_supported` or `not_supported` for policy-violation)
- ✅ MCP tool calls appear in agent logs (the agent grounded its analysis in spec content)
- ✅ Wall time is under 15 min for a single UC at reproduce mode

If any of these fail, **diagnose before Phase 3**. Common smoke-test failure modes:

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| MCP tool calls fail with `connection refused` | DNS or service routing in `dav` namespace | Check `oc get svc -n dav dav-docs-mcp`; service should expose port 8080 |
| Agent runs but analysis has no `components_required` etc. | Prompt isn't generating valid output (model mismatch, thinking-mode token leak) | Confirm `--no-enable-thinking` is set; check raw agent output in logs |
| JSON schema validation fails on output | Model returning malformed JSON | Increase `--max-tool-calls` if it's running out before producing final answer; or check for guided-JSON support on the inference endpoint |
| Engine crashes on import | Image was built against a broken commit | `oc get builds -n dav`; rebuild engine image: `ansible-playbook ... --tags engine` |
| Wall time > 30 min | Inference endpoint slow or saturated | `curl ${DAV_INFERENCE_ENDPOINT}/models` from inside a cluster pod (`oc rsh` then curl); check llama.cpp is responsive |

---

## Phase 3: Webhook Setup

Real run will be triggered by webhook on push/PR to the corpus repo. Set this up before the run.

### 3.1 — Get the webhook URL

```bash
oc get route dav-webhook -n dav -o jsonpath='https://{.spec.host}{"\n"}'
# Expected: https://${DAV_WEBHOOK_HOST}
```

### 3.2 — Get the webhook secret

```bash
ansible-vault view ansible/inventory/group_vars/all/vault.yaml | grep github_webhook_secret
# Or from the running secret:
oc get secret github-webhook-secret -n dav -o jsonpath='{.data.secret}' | base64 -d; echo
```

### 3.3 — Configure the webhook on GitHub

For `${DAV_CORPUS_REPO}`:

1. GitHub repo → Settings → Webhooks → Add webhook
2. **Payload URL:** `https://${DAV_WEBHOOK_HOST}`
3. **Content type:** `application/json`
4. **Secret:** paste the value from 3.2
5. **SSL verification:** Enable
6. **Events:** Just the push event, OR push + pull_request if you want both
7. **Active:** ✅
8. Save

### 3.4 — Verify webhook delivery

GitHub → Webhooks → click the new hook → Recent Deliveries tab.

Trigger a delivery (push a tiny commit to the corpus, or click "Redeliver" on the test ping):

```bash
cd ${DAV_CORPUS_DIR}
echo "" >> README.md  # trivial change
git commit -am "Trigger webhook delivery test"
git push
```

Expected:
- GitHub side: green checkmark, response 200
- Cluster side: `oc get pipelinerun -n dav --sort-by=.status.startTime` shows a new PR with `generateName: dav-stage2-`

If GitHub shows a 401, the secret is wrong (re-check the vault value matches what you put in GitHub). If 5xx, the EventListener pod is having trouble — `oc logs -n dav deploy/el-dav-repo-listener`.

**Don't proceed to Phase 4 until webhook delivery returns 200 and a PipelineRun starts.**

---

## Phase 4: Real Run

Real run = full DCM corpus, verification mode (N=3), all-criteria-on success target.

### 4.1 — Confirm the corpus state

```bash
cd ${DAV_CORPUS_DIR}
git pull
ls use-cases/        # Or whatever subdirectory holds the UCs
cat dav-version.yaml # Confirm 1.0.0 from Phase 0.2

# Validate locally before deploying
cd ${DAV_REPO_DIR}
python <<'EOF'
import yaml
from pathlib import Path
from dav.core.use_case_schema import UseCase
from dav.core.consumer_profile import get_dcm_reference_profile

p = get_dcm_reference_profile()
corpus = Path("${DAV_CORPUS_DIR}/use-cases")
for path in sorted(corpus.glob("*.yaml")):
    if path.name == "README.yaml":
        continue
    with open(path) as f:
        data = yaml.safe_load(f)
    uc = UseCase.from_dict(data)
    errors = uc.validate(p)
    print(f"{'OK' if not errors else 'FAIL'} {path.name}")
    for e in errors:
        print(f"   - {e}")
EOF
```

If any UC fails validation, fix the corpus before running. Validation failure during the run wastes inference time (each UC starts before realizing schema is wrong).

### 4.2 — Trigger the run

**Option A: Manual `tkn`** (recommended for first real run, even with webhook configured — gives you direct visibility and explicit param control):

```bash
tkn pipeline start dav-stage2 \
    -n dav \
    --workspace name=shared-data,claimName=dav-workspace \
    --param consumer-spec-repo-url=${DAV_SPEC_REPO} \
    --param consumer-spec-repo-branch=main \
    --param consumer-corpus-repo-url=${DAV_CORPUS_REPO} \
    --param consumer-corpus-repo-branch=main \
    --param mode=verification \
    --param sample-count=3 \
    --pipeline-timeout 24h \
    --serviceaccount dav-pipeline-sa \
    --use-param-defaults \
    --showlog
```

**Option B: Webhook trigger** — push a commit to the corpus repo. Useful for the operational pattern but provides less visibility into params.

### 4.3 — Monitor the first 30 minutes

The run-loop usually goes: kick off, first 2-3 UCs complete, surface any operational bugs, run continues. Watch for:

```bash
# In one terminal — follow the PipelineRun logs
LATEST_PR=$(oc get pipelinerun -n dav --sort-by=.status.startTime -o name | tail -1)
tkn pr logs -f $(echo $LATEST_PR | cut -d/ -f2) -n dav

# In another terminal — watch pod status
watch -n 5 'oc get pods -n dav -l tekton.dev/pipelineRun'

# Check inference endpoint isn't getting wedged
watch -n 30 'curl -sf ${DAV_INFERENCE_ENDPOINT}/models | head -c 100; echo'
```

**Triage triggers — when to abort vs let it finish:**

| Signal | Action |
|--------|--------|
| First UC takes > 60 min wall time | **Abort.** Inference endpoint is slow or hung. Investigate before retrying. |
| Multiple UCs in a row produce schema-validation errors | **Abort.** Output format is broken; running 6 more UCs won't fix it. |
| Single UC fails with traceback in `failures/` but next one succeeds | **Let it finish.** Per-UC failure isolation is working as designed. |
| Wall time creeping but UCs completing | **Let it finish.** Variance is normal for verification mode. |
| MCP timeout on a specific UC | **Let it finish unless it repeats.** One timeout is transient; three in a row means MCP is wedged. |
| EventListener / pipeline pod restart mid-run | **Abort and investigate.** Pipeline interruption corrupts run state. |

To abort cleanly:

```bash
tkn pr cancel $(echo $LATEST_PR | cut -d/ -f2) -n dav
```

### 4.4 — Capture findings

When the run completes, capture results:

```bash
LATEST_PR=$(oc get pipelinerun -n dav --sort-by=.status.startTime -o name | tail -1)

# Get the workspace PVC and copy results out
# (DAV writes to /workspace/runs/<run-id>/ inside the pod)
RUN_POD=$(oc get pod -n dav -l tekton.dev/pipelineRun=$(basename $LATEST_PR) -o name | head -1)

# Find the run-id
RUN_ID=$(oc exec -n dav $RUN_POD -- ls /workspace/runs | head -1)

# Copy results to your laptop
mkdir -p ~/dav-runs/$RUN_ID
oc cp -n dav $(echo $RUN_POD | cut -d/ -f2):/workspace/runs/$RUN_ID/ ~/dav-runs/$RUN_ID/

# Inspect
cat ~/dav-runs/$RUN_ID/run-summary.yaml
ls ~/dav-runs/$RUN_ID/analyses/
ls ~/dav-runs/$RUN_ID/failures/   # ideally empty
```

### 4.5 — Findings template

For each UC, capture:

| UC | Verdict | Confidence | Notable gaps | Wall time | Anomalies |
|----|---------|------------|--------------|-----------|-----------|
| uc001 | supported | high | — | 12m | none |
| uc008 | partially_supported | medium | atomic onboarding undefined | 38m | sample-vote 2:1 |
| ... | | | | | |

Plus per-run summary:
- **Total wall time:** (sum from `run-summary.yaml`)
- **Successful UCs:** N/total
- **Failed UCs:** list with error from `failures/`
- **Verdict distribution:** how many `supported` / `partially_supported` / `not_supported`
- **Compared to Phase 1a/1b prior:** verdicts shifted? Stayed stable?
- **Surprises:** anything the analyzer found you didn't expect; anything it missed that you did expect

That's the structured artifact this whole arc was building toward — the per-UC architectural findings against the current DCM spec, with confidence calibrated by ensemble vote.

### 4.6 — Specific UC to scrutinize: `uc008`

You flagged `uc008` (atomic onboarding) earlier as a real architectural gap surfaced in Phase 1b, queued behind migration. The run will produce a fresh analysis. Compare:

- **Phase 1b finding:** partial support, medium confidence, gap = compound onboarding model not specified
- **Fresh run finding:** ?

If the fresh run agrees, the gap is real and stable across analyzer iterations — strong signal to address in DCM spec work. If it disagrees, that's interesting too — figure out why before treating it as ground truth.

---

## Phase 5: After the Run

Decisions for what comes next based on what the run surfaced.

### If the run succeeded cleanly

- Architectural findings are the deliverable. Write them up against your DCM spec workstream priorities.
- Tag the corpus with the version that ran (`git tag -a dav-run-2026-04-25 -m "Full DCM corpus run, verification mode"`) so future runs have a reference point.
- Decide if you want this run scheduled (cronjob → tkn pipeline start) or only PR-triggered (webhook).

### If the run surfaced bugs (most likely outcome)

- Fix the bug in the relevant code path
- Re-deploy the affected component (`ansible-playbook ... --tags engine` for engine fixes, `--tags mcp` for MCP fixes)
- Re-run

### If wall time was way off the estimate

- Profile inference endpoint independently — `curl` directly to llama.cpp with a sample prompt, measure latency
- Consider mode change: maybe `reproduce` (N=1) is enough for regression and verification (N=3) is overkill for routine runs
- Or: corpus is bigger than estimated; budget accordingly

### If verdicts disagree with Phase 1a/1b prior

- Read the disagreeing analyses carefully. Did the prompt change between then and now? (Check `STAGE2_PROMPT_VERSION` in the analyses.) Did the spec content change? Did the model change?
- One disagreement is interesting; multiple disagreements signal a calibration drift. Re-baseline if needed.

---

## Quick reference: full command list

If you just want the commands inline without prose:

```bash
# Phase 0
cp ansible/inventory/group_vars/all/vars.local.yaml.example \
   ansible/inventory/group_vars/all/vars.local.yaml
$EDITOR ansible/inventory/group_vars/all/vars.local.yaml   # fill in 6 required vars
ansible-vault edit ansible/inventory/group_vars/all/vault.yaml
echo "your-vault-password" > .vault_pass && chmod 600 .vault_pass
cd ${DAV_CORPUS_DIR}
echo -e "consumer_id: dcm\nconsumer_version: 1.0.0" > dav-version.yaml
git add dav-version.yaml && git commit -m "Add dav-version.yaml" && git push
cd -
# Replace with your actual primary inference endpoint URL
curl -sf "$(grep inference_primary_endpoint ansible/inventory/group_vars/all/vars.local.yaml | awk -F'"' '{print $2}')/models"
oc whoami

# Phase 1
ansible-playbook ansible/playbook.yaml
ansible-playbook ansible/playbook.yaml --tags secrets
oc get pods -n dav
oc get pipeline.tekton.dev dav-stage2 -n dav
oc exec -n dav deploy/dav-docs-mcp -- curl -sf http://localhost:8080/health
oc get route dav-webhook -n dav

# Phase 2 (smoke test, manual run)
oc run -n dav --rm -it --image="image-registry.openshift-image-registry.svc:5000/dav/dav-engine:latest" --restart=Never --serviceaccount=dav-pipeline-sa smoke-test -- /bin/bash
# (inside pod)
python -m dav.stages.stage2_analyze --use-case /workspace/dav/examples/exemplar-ucs/exemplar-bookcatalog-register-happy-path.yaml --output /tmp/smoke.yaml --inference-endpoint ${DAV_INFERENCE_ENDPOINT} --inference-model qwen --mcp-url http://dav-docs-mcp.dav.svc:8080 --mode reproduce --no-enable-thinking --max-tool-calls 30

# Phase 3 (webhook setup is GitHub UI work; commands here are verification only)
oc get route dav-webhook -n dav -o jsonpath='https://{.spec.host}{"\n"}'
oc get secret github-webhook-secret -n dav -o jsonpath='{.data.secret}' | base64 -d

# Phase 4 (real run)
tkn pipeline start dav-stage2 -n dav \
    --workspace name=shared-data,claimName=dav-workspace \
    --param consumer-spec-repo-url=${DAV_SPEC_REPO} \
    --param consumer-spec-repo-branch=main \
    --param consumer-corpus-repo-url=${DAV_CORPUS_REPO} \
    --param consumer-corpus-repo-branch=main \
    --param mode=verification \
    --param sample-count=3 \
    --pipeline-timeout 24h \
    --serviceaccount dav-pipeline-sa --use-param-defaults --showlog
```

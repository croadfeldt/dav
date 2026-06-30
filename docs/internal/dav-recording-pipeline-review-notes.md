# Recording→UC Pipeline — Review Notes / Second Opinion

_Companion to `dav-recording-pipeline-requirements.md`. Written by a separate Claude
(Opus 4.8) session at the operator's request as a second opinion **before** implementation.
Read the requirements doc first; this only adds recommendations and flags three divergences
to decide on. `docs/internal/` is gitignored — keep it that way (NDA-adjacent)._

The requirements doc is sound and the security spine (no recording data leaves the homelab
trust boundary) is correct and non-negotiable. Three things to decide before building.

---

## 1. Model selection — reuse DAV's model config, but be precise about which steps

**Recommendation:** reuse DAV's existing model-config / endpoint mechanism (the "AI Models"
config + `model_config_id`) rather than inventing new config. But there are **three** distinct
inference points and they are not interchangeable:

| Step | Selector? | Notes |
|------|-----------|-------|
| **STT (whisper.cpp)** | **No endpoint selector** | It's a local CPU library, not an OpenAI-compatible endpoint. Offer a **model-size** choice (small.en / medium / large) instead. Never an external endpoint — audio is the rawest NDA content. |
| **Vision (diagram/whiteboard description)** | Yes — endpoint selector | Reuse model config. Frames are the *most* sensitive content (customer architecture on whiteboards). Constrain/default to local. |
| **UC extraction (final LLM)** | Yes — endpoint selector | The doc's explicit external opt-in lives here. Default local (Qwen3-32B). |

**The one attribute the current model config probably lacks and MUST gain:** a **locality /
trust flag per endpoint (local vs external)**, defaulting to local. Selecting an external
endpoint must surface an explicit, unmissable consent — *"this sends NDA recording content
(transcript / frames) to `<provider>`"* — because that is literally the doc's security rule #3.
A bare model dropdown with no locality awareness would quietly violate the trust boundary.

**UX suggestion:** model this as a single **"processing profile"** (everything defaults to
local, with per-step overrides) rather than three independent selectors cluttering the submit
form. One default-safe choice, advanced override for the steps that matter.

---

## 2. Storage — modest, if TTL cleanup stays

The heavy intermediate is the **WAV**: 16 kHz mono 16-bit = ~32 KB/s = **~115 MB per audio-hour**,
uncompressed (a 3-hour recording → ~345 MB of WAV alone). Frames are cheap (~5–50 MB/recording).
Per active job, peak working set ≈ **~1 GB** (upload + WAV + frames).

Recommended layout:

- **Working PVC (encrypted CephFS, RWX):** size ~1–1.5 GB × max concurrent jobs. Solo operator
  running a batch sequentially → 2–3 GB is plenty; **5–10 GB** is comfortable headroom.
- **Whisper model cache:** ~500 MB (small.en), up to ~3 GB if `large` is adopted later. Bake into
  the image **or** mount a tiny dedicated PVC so it doesn't re-download on pod restart.
- **Retention split:** TTL-delete the heavy media (upload / WAV / frames) per the doc, but
  **retain the transcript + frame-descriptions** (text — KB–MB each, encrypted). Hundreds of
  recordings = tens of MB total.

**Why retain the text** (this is the high-value coupling with §1): keeping the transcript makes
re-extraction cheap — swap the extraction endpoint and re-run **only Phase 5**, with no
50-minute re-transcription. That turns the model selector into something genuinely useful (A/B
a local vs external extraction model on the same transcript) instead of a one-shot setting. It
also gives the operator a reviewable record without keeping the raw audio around.

---

## 3. Architecture divergence to weigh — don't run the pipeline in the main API pod

The requirements doc puts the whole media pipeline (ffmpeg + whisper + tesseract + opencv)
**inside the API pod** with **module-dict job state**. Push back on both:

- A 3-hour transcription pegs **4–6 CPU for ~50 min**; doing that in the interactive API pod
  risks degrading the live console and OOM (whisper adds ~1 GB RSS). The media deps also bloat
  the API image.
- **Recommendation:** a **dedicated recording-worker deployment** (its own image with the media
  deps; GPU affinity available for the vision step). The API stays lean and just brokers
  submit/poll/delete.
- That forces **DB-backed job state** (a `recording_jobs` table) instead of a module-dict —
  which is **more robust anyway**: survives pod restarts, works if the API ever scales past one
  replica, and gives project-scoping + auditability for free (also satisfies the doc's
  "user in project A cannot see project B's jobs" criterion structurally rather than by
  in-memory filtering).

If the operator wants the fastest possible Phase-A MVP and accepts the constraints (single API
replica, no concurrent long jobs, results lost on restart), the in-pod module-dict path is
*acceptable as a first cut* — but plan the `recording_jobs` table + worker split as Phase A.5
before this sees real batch use. Calling it out so it's a decision, not a default.

---

## Two synergies with already-shipped work

- **Auth is already done.** The doc's `Authorization: Bearer <PAT>` requirement on
  `POST /api/use-cases/from-recording` is satisfied by the PAT work (#167): an agent or a
  dedicated recorder box can submit recordings with a scoped, revocable token acting as a
  project-scoped identity. No new auth needed.
- **`X-DAV-Project` + `require_priv(P_PROJECT_USECASES)`** already give you the project scoping
  and authorization the acceptance criteria demand — follow those existing patterns exactly.

---

_Net: build it. Reuse the model config (with a locality flag + consent), keep storage small via
text-only retention on an encrypted CephFS PVC, and seriously consider the worker + DB-job-state
split before batch use. Phase A (audio-only) remains the right first ship._

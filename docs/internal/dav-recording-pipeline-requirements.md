# DAV Recording-to-Use-Case Pipeline — Requirements & Design Document

## 1. What This Is

DAV is a tool that evaluates use cases against architectural specifications. Use cases today are created manually — either typed in through the UI, bulk-imported from pasted text, or pushed via API. The single biggest source of new use cases is **meeting recordings**: customer calls, architecture reviews, workshops, and demos where requirements surface in conversation.

This document describes a new DAV capability: a pipeline that accepts audio or video recordings of meetings and automatically produces structured use cases ready for architecture evaluation.

### The problem it solves

Chris Roadfeldt (DAV's operator) regularly records meetings with customers at major financial institutions — Barclays, PNC, Bank of America, Truist, US Bank, JPMC. In a single working session, 10 recordings totaling ~10 hours of audio were processed manually: each one had to be converted, transcribed, read, analyzed for use case content, formatted into DAV's UC YAML schema, and pushed via API. The manual pipeline works but doesn't scale.

### What the pipeline does

1. Accepts an audio or video file upload
2. Extracts and transcribes the audio (speech-to-text)
3. Extracts meaningful visual content from video (slides, diagrams, screen shares)
4. Combines audio transcript + visual context
5. Uses an LLM to identify and extract structured use cases from the combined content
6. Returns the use cases in DAV's standard format for review and persistence

---

## 2. Security Requirements

**This is the most important section of this document.** The recordings contain NDA-protected conversations with customers in regulated industries. Every design decision must satisfy these constraints.

### What's in the recordings

- Customer architecture details under NDA (Barclays Apex internals, PNC infrastructure, Truist platform services)
- Unreleased Red Hat product information (Project LightWell, AAP Automation Orchestrator, DCM v2)
- Customer names, organizational structures, personnel, and occasionally financial data mentioned in passing
- Competitive positioning and pricing discussions
- Proprietary diagrams drawn on whiteboards or shared on screen

### Hard rules

1. **No data leaves the trust boundary.** The trust boundary is Chris's homelab OpenShift cluster and local machines. No audio, video, frames, transcripts, or analysis results may be sent to any third-party cloud service — not for transcription, not for vision/OCR, not for LLM analysis.

2. **All ML inference runs locally.** Speech-to-text, image analysis, and use case extraction must use models deployed on the cluster or local hardware. No OpenAI Whisper API, no Google Cloud Vision, no Azure Cognitive Services, no Anthropic API for processing recording content.

3. **Exception for UC extraction:** The operator may choose to use an external LLM (via DAV's model configuration) for the final use case extraction step. This is an explicit opt-in — the operator understands that the transcript text will be sent to that provider. For maximum security, a local model (Qwen3-32B on the R9700 GPUs) should be the default.

4. **Storage encryption at rest.** Uploaded recordings, extracted frames, and transcripts are stored on CephFS PVCs (encrypted at rest on the OCP cluster). Temporary files on local disk are cleaned up immediately after processing.

5. **Access control.** The pipeline endpoints require authentication and are scoped to a DAV project. Only project members can submit recordings or view results.

6. **Artifact cleanup.** All intermediate artifacts (uploaded recording, WAV conversion, extracted frames, raw transcript) are deleted after a configurable TTL (default: 1 hour after job completion). A DELETE endpoint allows immediate cleanup.

---

## 3. Architecture

### Component stack

Every component runs locally with no external network dependency:

| Component | Purpose | Where it runs |
|-----------|---------|---------------|
| **ffmpeg** | Extract audio track from video; extract keyframes at scene changes | API pod |
| **pywhispercpp** (whisper.cpp) | Speech-to-text transcription | API pod, CPU-only |
| **Tesseract OCR** | Extract text from slides, terminals, code screenshots | API pod |
| **Qwen3-32B** (vLLM, multimodal) | Describe non-text visual content (architecture diagrams, whiteboards) | R9700 GPU pod, on-cluster |
| **Qwen3-32B** or operator-configured model | Extract structured use cases from combined transcript + visual descriptions | On-cluster (default) or operator's choice |

### Pipeline phases

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌────────────┐
│  Upload      │────▶│  Transcribe  │────▶│  Extract Frames  │────▶│  Analyze Visuals │────▶│  Extract   │
│  (audio or   │     │  Audio       │     │  (video only)    │     │  (OCR + LLM)     │     │  Use Cases │
│  video file) │     │  (Whisper)   │     │  (ffmpeg scene   │     │                  │     │  (LLM)     │
│              │     │              │     │   detection)     │     │                  │     │            │
└─────────────┘     └──────────────┘     └──────────────────┘     └─────────────────┘     └────────────┘
                         ~15 min/hr           <1 min                  ~5 min/50 frames        ~2 min
                         CPU only             CPU only                GPU (on-cluster)         GPU
```

### Phase details

**Phase 1 — Ingest & split**
- Accept multipart file upload (m4a, mp4, mov, wav, webm, mkv, mp3, ogg)
- If video: extract audio track to 16kHz mono WAV via ffmpeg
- If audio-only: convert to 16kHz mono WAV via ffmpeg
- File size limit: 500MB (accommodates 3-hour video files)

**Phase 2 — Transcribe audio**
- pywhispercpp with the `small.en` model (~487MB, downloads once, cached)
- Runs in a thread pool (`asyncio.to_thread`) since it's CPU-bound
- Output: timestamped text segments `[MM:SS - MM:SS] text`
- Performance: ~15x realtime on CPU (1 hour audio ≈ 15 minutes processing)
- No speaker diarization in MVP (added in a later phase)

**Phase 3 — Extract keyframes (video only)**
- ffmpeg scene-change detection: `select='gt(scene,0.3)'` extracts frames where >30% of pixels changed
- Fixed-interval fallback: one frame every 30 seconds if no scene change detected
- Perceptual hash deduplication: skip near-duplicate frames (presenter returns to same slide)
- Talking-head filter: skip frames that are just a webcam with no text or diagram content (use edge density heuristic)
- Target: reduce a 1-hour video from 3600 potential frames to ~20-50 meaningful keyframes

**Phase 4 — Analyze visual content**
- For each meaningful keyframe, classify and extract content:
  - **Text-heavy frame** (slide deck, terminal, code) → Tesseract OCR → extract text verbatim
  - **Diagram/whiteboard** (architecture drawings, flowcharts) → send to Qwen3-32B vision → natural language description
  - **Mixed** → OCR for text regions + LLM for diagram regions
- Output: timestamped visual descriptions `[MM:SS] [slide] "DCM Architecture diagram showing centralized control plane with four data stores..."` 
- These descriptions are appended to the audio transcript to create a combined timeline

**Phase 5 — Extract use cases**
- Merge audio transcript + visual descriptions into a single combined document
- Feed to `uc_assist.extract_bulk()` (existing DAV function) with optional operator-provided context
- Visual context enriches extraction: the LLM knows what was on screen when something was discussed
- Output: standard DAV bulk extraction format — array of `{yaml_content, rationale, source_excerpt}`
- Operator reviews extracted UCs in the standard bulk review UI before persisting

### Async execution model

The full pipeline takes 15-45 minutes depending on recording length. It cannot run synchronously in an HTTP request.

**Submit:** `POST /api/use-cases/from-recording` returns immediately with `{"job_id": "rec-<uuid>", "status": "processing"}`

**Poll:** `GET /api/use-cases/from-recording/{job_id}` returns current status, progress percentage, and available intermediate results (transcript becomes available before frame analysis completes, etc.)

**Cancel/cleanup:** `DELETE /api/use-cases/from-recording/{job_id}` stops processing and removes all artifacts

Job state is tracked in a module-level dictionary (same pattern used by DAV's enhancement streaming). Jobs are automatically purged 1 hour after completion.

---

## 4. API Specification

### Submit recording

```
POST /api/use-cases/from-recording
Content-Type: multipart/form-data
Authorization: Bearer <PAT or session cookie>
X-DAV-Project: <project_id>

Fields:
  file:             <binary>  (required) Audio or video file
  context:          <string>  (optional) Extraction guidance, e.g. "Truist network automation, focus on branch provisioning workflows"
  model_config_id:  <int>     (optional) Model config for UC extraction step
  extract_visuals:  <bool>    (optional, default: true for video, false for audio-only)
  frame_interval:   <int>     (optional, default: 30) Seconds between fallback frame captures

Response (202 Accepted):
{
  "job_id": "rec-a1b2c3d4e5f6",
  "status": "transcribing",
  "message": "Recording accepted. Poll GET /api/use-cases/from-recording/rec-a1b2c3d4e5f6 for status."
}
```

### Poll status

```
GET /api/use-cases/from-recording/{job_id}
Authorization: Bearer <PAT or session cookie>

Response (200):
{
  "job_id": "rec-a1b2c3d4e5f6",
  "status": "transcribing | extracting-frames | analyzing-visuals | extracting-ucs | done | failed",
  "progress": 0.65,
  "phase": "analyzing-visuals",
  "duration_seconds": 847,
  "file_name": "2026-06-15 Truist Kranthi.m4a",
  "transcript_ready": true,
  "transcript": "...",
  "frames_extracted": 47,
  "frames_analyzed": 23,
  "frame_descriptions": [
    {"timestamp": "14:23", "type": "slide", "description": "..."},
    {"timestamp": "27:45", "type": "diagram", "description": "..."}
  ],
  "items": [
    {"yaml_content": "...", "rationale": "...", "source_excerpt": "..."}
  ],
  "error": null
}
```

Fields appear progressively: `transcript` is available once Phase 2 completes even while Phase 3-4 are still running. `items` appears only when status is `done`.

### Delete job

```
DELETE /api/use-cases/from-recording/{job_id}
Authorization: Bearer <PAT or session cookie>

Response (200):
{"ok": true, "artifacts_cleaned": true}
```

---

## 5. Implementation Phases

### Phase A — Audio-only MVP
**Scope:** Transcription + UC extraction. No video frame analysis.
**New files:**
- `review-console/api/app/transcribe.py` — Whisper transcription wrapper
- Additions to `review-console/api/app/main.py` — endpoints + background job management

**Dependencies to add:**
- `pywhispercpp>=1.4.0` in requirements.txt
- `ffmpeg-free` in Containerfile (dnf install)

**This alone covers ~90% of the value.** Every meeting recording processed in this session was audio-only analysis. Ship this first.

### Phase B — Video keyframe extraction + OCR
**Scope:** Extract keyframes via ffmpeg, OCR text-heavy frames via Tesseract, append to transcript.
**New files:**
- `review-console/api/app/frame_extract.py` — keyframe extraction + classification + OCR

**Dependencies to add:**
- `pytesseract` or `tesserocr` in requirements.txt
- `tesseract` in Containerfile
- `opencv-python-headless` for frame classification heuristics

**This gets ~80% of visual value with zero LLM/GPU cost for frame analysis.** Slides and terminal content are captured via OCR; only diagrams are missed.

### Phase C — LLM vision for diagrams
**Scope:** Send non-text frames (architecture diagrams, whiteboards) to multimodal LLM for natural language descriptions.
**Prerequisite:** Verify Qwen3-32B vLLM deployment supports multimodal input. If not, evaluate alternatives (LLaVA, Qwen-VL, etc.) that run on the R9700 GPUs.
**Changes:**
- Add vision call path in `frame_extract.py`
- Add vision model config option to the endpoint

**This is the highest-value addition for architecture meetings** where the important content is drawn on a whiteboard or shown as a diagram, not spoken aloud.

### Phase D — Speaker diarization
**Scope:** Add speaker labels to the transcript ("Speaker 1:", "Speaker 2:" or ideally identified by name).
**Options:**
- `whisperx` — adds diarization on top of Whisper, uses pyannote.audio
- Custom pipeline with pyannote.audio separately

**Value:** Improves UC attribution ("Kevin asked for X" vs "someone asked for X"). Also improves the overall transcript quality for human consumption.

---

## 6. Resource Requirements

### API pod changes

| Resource | Current | With Phase A | With Phase A+B |
|----------|---------|-------------|----------------|
| Memory limit | ~512MB | ~1GB (Whisper model) | ~1.2GB (+OCR) |
| CPU | 1 core | 4-6 cores during transcription | Same |
| Storage (temp) | Minimal | ~500MB per active job | ~700MB per active job |
| New system packages | None | ffmpeg-free | +tesseract |

### Processing time estimates

| Recording length | Phase A (audio only) | Phase A+B (audio + OCR) | Phase A+B+C (full) |
|-----------------|---------------------|------------------------|-------------------|
| 30 min | ~8 min | ~10 min | ~13 min |
| 1 hour | ~17 min | ~20 min | ~25 min |
| 3 hours | ~50 min | ~55 min | ~65 min |

### Whisper model

The `small.en` model (~487MB) downloads on first use and caches to `~/.local/share/pywhispercpp/models/`. In the container, this should be baked into the image or mounted via a PVC to avoid re-downloading on pod restart.

---

## 7. How to Use This Document with Claude

This document is designed to be handed to a Claude instance (Opus 4.8 or equivalent) as context for implementing the pipeline. The Claude instance should:

1. **Read this document first** for requirements, security constraints, and architecture decisions.

2. **Read the existing codebase** at `/Users/chris/git/dav/review-console/api/app/`:
   - `main.py` — the FastAPI application. Key patterns to follow:
     - File upload: `import_use_cases()` at line ~6205 (uses `UploadFile = File(...)`)
     - Background jobs: `_active_gen` dict + `asyncio.create_task()` at line ~10946
     - Bulk UC extraction endpoint: `uc_bulk_extract()` at line ~4354
   - `uc_assist.py` — UC extraction logic. `extract_bulk()` at line ~299 is the function to call with the transcript text.
   - `Containerfile` — base image is UBI9 Python 3.11.

3. **Implement Phase A first** (audio-only MVP):
   - Create `transcribe.py` — a sync function `transcribe_audio(file_bytes, format_hint) -> str` that handles ffmpeg conversion + Whisper transcription + temp file cleanup. Designed to run inside `asyncio.to_thread()`.
   - Add endpoints to `main.py` — POST (submit), GET (poll), DELETE (cleanup).
   - Add `pywhispercpp>=1.4.0` to `requirements.txt`.
   - Add `RUN dnf install -y ffmpeg-free` to `Containerfile`.

4. **Follow existing code patterns exactly.** The codebase has strong conventions:
   - Auth: `await require_priv(request, rbac.P_PROJECT_USECASES)` 
   - Project scoping: `await _active_project_id(request, conn)` via `X-DAV-Project` header
   - Error handling: `HTTPException` with structured detail dicts
   - No comments unless the WHY is non-obvious

5. **Test with:**
   ```bash
   curl -X POST https://dav.roadfeldt.com:8843/api/use-cases/from-recording \
     -H "Authorization: Bearer <token>" \
     -H "X-DAV-Project: 20" \
     -F "file=@/path/to/recording.m4a" \
     -F "context=Customer architecture meeting"
   ```

6. **Security verification:**
   - Confirm no HTTP calls leave the pod during transcription (tcpdump or network policy)
   - Confirm temp files are cleaned up after job completion
   - Confirm job results are project-scoped (user in project A cannot see jobs from project B)

---

## 8. Acceptance Criteria

- [ ] Audio files (m4a, mp3, wav) are accepted and transcribed without external API calls
- [ ] Video files (mp4, mov, webm) have audio extracted and transcribed
- [ ] Job runs asynchronously with pollable status
- [ ] Transcript is available for review before UC extraction completes
- [ ] Extracted UCs match the quality of manually-produced UCs from the same recording
- [ ] All temp files are cleaned up after job completion or TTL expiry
- [ ] Endpoint requires authentication and respects project scoping
- [ ] No data leaves the trust boundary during processing
- [ ] Phase B: Text-heavy video frames produce OCR text appended to transcript
- [ ] Phase C: Architecture diagrams produce natural language descriptions via local LLM

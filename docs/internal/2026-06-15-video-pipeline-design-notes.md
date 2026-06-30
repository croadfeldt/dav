# Video-to-UC Pipeline — Design Notes & Security Analysis

## The Vision

A DAV-native pipeline that ingests meeting recordings (audio + video) and produces:
1. **Timestamped transcript** (from audio track)
2. **Visual content extraction** (from video track — slides, whiteboards, screen shares, demos)
3. **Combined analysis** — LLM synthesizes audio + visual context into richer UC extraction

This turns a 3-hour customer meeting recording into structured, actionable use cases with visual evidence — automatically.

## Security Constraints (Non-Negotiable)

These recordings contain:
- **NDA-protected customer conversations** (Barclays, PNC, Truist, US Bank, JPMC)
- **Proprietary architecture diagrams** (Apex internals, customer infrastructure layouts)
- **Unreleased product information** (Project LightWell, AAP Orchestrator)
- **Customer names, org charts, financial data** (mentioned in passing)
- **Red Hat internal strategy** (pricing discussions, competitive positioning)

### What this means for the pipeline:

1. **No external API calls for transcription or vision.** Cannot send audio/video frames to OpenAI Whisper API, Google Cloud Vision, Azure Cognitive Services, or any cloud-hosted service. Everything must run locally or on Chris's OCP cluster.

2. **No data leaves the trust boundary.** The trust boundary is Chris's homelab OCP cluster + local machines (stark, sully). Audio, video frames, transcripts, and extracted content must never transit a third-party network.

3. **The LLM for UC extraction is already local.** DAV uses Qwen3-32B running on the R9700 GPUs in the homelab. This is fine — the data stays on-cluster.

4. **The UC extraction step (bulk-from-text) MAY use external LLMs** if configured with an Anthropic/OpenAI model_config. For this pipeline, we need to ensure the transcript text going to UC extraction doesn't include raw customer-identifiable content if an external model is used. Options:
   - Only allow local model configs for from-recording flows
   - Strip/redact PII before sending to external models
   - Accept the risk if the model config is explicitly chosen by the operator

5. **Storage at rest.** Uploaded recordings, transcripts, and extracted frames must be stored on encrypted storage (the OCP PVCs are on CephFS which is encrypted at rest). Temp files must be cleaned up.

6. **Access control.** The from-recording endpoint must require authentication. Recordings should be scoped to a project — only project members can see the results.

## Technical Architecture

### Component Stack (all local, no external dependencies)

| Component | Purpose | Runs On | Security |
|-----------|---------|---------|----------|
| ffmpeg | Audio extraction + video frame extraction | API pod | Local binary, no network |
| pywhispercpp (whisper.cpp) | Speech-to-text | API pod (CPU) | Local model, no network |
| OpenCV or ffmpeg | Keyframe extraction from video | API pod | Local, no network |
| Qwen3-32B (vLLM) | Frame analysis (describe what's on screen) | R9700 GPU pod | On-cluster, no external |
| Qwen3-32B or configured model | UC extraction from combined transcript | On-cluster or configured | Operator's choice |

### Pipeline Phases

#### Phase 1 — Ingest & Split
- Accept video upload (mp4, mov, webm, mkv) or audio-only (m4a, wav, mp3)
- Extract audio track → 16kHz mono WAV (ffmpeg)
- If video: extract keyframes at scene changes or fixed intervals (every 30s, configurable)
- Store everything in temp directory scoped to job ID

#### Phase 2 — Transcribe Audio
- pywhispercpp with small.en model (CPU, ~15x realtime)
- Output: timestamped segments `[MM:SS - MM:SS] text`
- No external API calls

#### Phase 3 — Analyze Visual Content
- For each keyframe, determine if it contains meaningful content:
  - **Slide deck** — text-heavy, structured layout → OCR + describe
  - **Whiteboard/diagram** — shapes, arrows, handwriting → describe architecture
  - **Screen share (code/terminal)** — monospace text → OCR
  - **Talking heads only** — skip (no information value)
- Use local Qwen3-32B vision capabilities (vLLM supports multimodal) to describe each meaningful frame
- Output: timestamped frame descriptions `[MM:SS] [slide] "DCM Architecture - showing control plane with 4 data stores..."`

#### Phase 4 — Combine & Extract UCs
- Merge audio transcript + visual descriptions into a combined timeline
- Feed combined content to UC extraction (uc_assist.extract_bulk or enhanced version)
- Visual context enriches the extraction: "At 14:23, presenter showed the dependency graph diagram while discussing workload portability" → better UC than audio alone

#### Phase 5 — Deliver Results
- Return to the existing bulk review flow (items with yaml_content, rationale, source_excerpt)
- Additionally store: raw transcript, keyframe images, frame descriptions
- All scoped to the job and the project

### Keyframe Extraction Strategy

Not every frame is worth analyzing. Strategy to minimize compute:

1. **Scene change detection** — ffmpeg `select='gt(scene,0.3)'` filter extracts frames where >30% of pixels changed. Catches slide transitions, screen share switches, whiteboard additions.
2. **Fixed interval fallback** — every 30s if no scene change detected (ensures nothing is missed in slow-panning demos)
3. **Dedup** — perceptual hash (pHash) to skip near-duplicate frames (presenter returns to same slide)
4. **Talking-head filter** — simple heuristic: if frame is mostly a face/webcam with no text or diagram elements, skip it. Can use edge density or text detection (high edge density + text regions = slide/diagram; low = face).

Target: reduce a 1-hour video from 3600 potential frames to ~20-50 meaningful keyframes.

### Resource Requirements

| Phase | CPU | Memory | Time (1hr recording) | GPU |
|-------|-----|--------|---------------------|-----|
| Ingest/split | Low | 200MB | <1 min | No |
| Transcribe | High (6 threads) | 500MB (model) | ~15 min | No |
| Frame extraction | Low | 100MB | <1 min | No |
| Frame analysis | Low (API call) | N/A | ~5 min (50 frames × ~6s each) | Qwen3-32B |
| UC extraction | Low (API call) | N/A | ~2 min | Qwen3-32B |
| **Total** | | **~800MB peak** | **~23 min** | Yes (phase 4-5) |

### API Design

```
POST /api/use-cases/from-recording
  Content-Type: multipart/form-data
  file: <video or audio file>
  context: "optional extraction guidance"
  model_config_id: <optional, for UC extraction model>
  extract_visuals: true|false (default true if video, false if audio-only)
  frame_interval: 30 (seconds, default)

Returns: {"job_id": "rec-<uuid>", "status": "processing"}

GET /api/use-cases/from-recording/{job_id}
Returns: {
  "job_id": "rec-xxx",
  "status": "transcribing|extracting-frames|analyzing-visuals|extracting-ucs|done|failed",
  "progress": 0.65,
  "phase": "analyzing-visuals",
  "transcript_ready": true,
  "frames_extracted": 47,
  "frames_analyzed": 23,
  "transcript": "...",           // available once phase 2 completes
  "frame_descriptions": [...],   // available once phase 3 completes
  "items": [...]                 // available once phase 4 completes
}

DELETE /api/use-cases/from-recording/{job_id}
  Cleans up all stored artifacts (recording, frames, transcript)
```

## Security Model Summary

| Concern | Mitigation |
|---------|-----------|
| Audio leaves trust boundary | Whisper runs locally (pywhispercpp), no API calls |
| Video frames leave trust boundary | Frame analysis via on-cluster Qwen3-32B (vLLM), no external |
| Transcript sent to external LLM | Operator chooses model_config — can restrict to local only |
| Recordings stored unencrypted | CephFS PVCs are encrypted at rest; temp files cleaned on job completion |
| Unauthorized access to recordings | Endpoint requires auth + project membership; jobs scoped to project |
| Recordings persist indefinitely | Auto-cleanup after configurable TTL (default 1 hour); DELETE endpoint for immediate cleanup |
| Customer PII in transcripts | Transcripts stay on-cluster; if external model used for UC extraction, operator accepts risk |
| Leaked frames contain sensitive diagrams | Frames stored only in temp/PVC, never transmitted externally, cleaned up with job |

## Open Questions

1. **Qwen3-32B vision support** — Does the current vLLM deployment support multimodal (image) input? If not, need to either upgrade or use a separate vision model. Alternative: use Tesseract OCR for text-heavy frames and skip LLM vision entirely for the MVP.

2. **Model for frame analysis** — Should this use the same model config as UC extraction, or a dedicated vision model config? The frame descriptions are intermediate artifacts, not customer-facing, so a smaller/faster model might suffice.

3. **Diarization** — Current Whisper setup has no speaker labels. Adding `whisperx` or similar for speaker diarization would improve transcript quality significantly for multi-speaker meetings. Worth the added complexity?

4. **Video file size limits** — A 3-hour MP4 screen share can be 1-2GB. Do we accept files that large? Need to consider upload timeout and memory during processing.

5. **Batch processing** — Should the endpoint accept multiple files (e.g., a meeting split across 3 recordings)? Or handle that at the UI level with multiple uploads to the same job?

## Implementation Phases

### Phase A (MVP) — Audio-only pipeline (current plan)
- `POST /api/use-cases/from-recording` with audio transcription + UC extraction
- No video frame analysis
- Ship this first, it's 90% of the value

### Phase B — Add video frame extraction + OCR
- Extract keyframes via ffmpeg scene detection
- Tesseract OCR for text-heavy frames (slides, terminals)
- Append OCR text to transcript as `[SLIDE @ MM:SS] extracted text...`
- No LLM vision needed — just OCR
- This gets ~80% of the visual value with zero external dependencies

### Phase C — Add LLM vision for diagrams/whiteboards
- Send non-text frames (architecture diagrams, whiteboard drawings) to Qwen3-32B vision
- Requires multimodal vLLM support
- Highest value for architecture meetings but most complex

### Phase D — Speaker diarization
- Add whisperx or equivalent for speaker labels
- Enables "who said what" which improves UC attribution

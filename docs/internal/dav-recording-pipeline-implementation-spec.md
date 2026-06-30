# Recording→UC Pipeline — Implementation Specification

_Finalized decisions from requirements review + second-opinion review. This is the build spec._
_Read `dav-recording-pipeline-requirements.md` for context and `dav-recording-pipeline-review-notes.md` for the review that informed these decisions._

---

## Decisions Made

1. **Worker + DB from the start.** No in-pod module-dict shortcut. A separate `recording-worker` deployment handles transcription and frame analysis. The API pod stays lean and just brokers submit/poll/delete. Job state lives in a `recording_jobs` DB table.

2. **Retain transcript text, delete media.** The heavy artifacts (uploaded recording, WAV conversion, extracted frames) are deleted on TTL. The lightweight text artifacts (transcript, frame descriptions) are retained in the DB permanently. This enables re-running UC extraction with a different model without re-transcribing.

3. **Locality flag on model configs.** Model configs gain a `locality` field (`local` | `external`, default `local`). When an operator selects an external model for UC extraction on recording content, the UI and API surface an explicit consent warning. STT (Whisper) is always local — never an endpoint selector, just a model-size choice.

4. **Processing profile.** The submit endpoint accepts a single `whisper_model` field (default `small.en`) for STT. Vision and UC extraction use model configs with the existing `model_config_id` mechanism, defaulting to local.

---

## Database Migration

New table: `recording_jobs`

```sql
CREATE TABLE recording_jobs (
    id              SERIAL PRIMARY KEY,
    job_id          TEXT NOT NULL UNIQUE,          -- 'rec-<uuid>'
    project_id      INT NOT NULL REFERENCES projects(id),
    status          TEXT NOT NULL DEFAULT 'queued', -- queued|transcribing|extracting-frames|analyzing-visuals|extracting-ucs|done|failed
    progress        REAL NOT NULL DEFAULT 0.0,     -- 0.0 to 1.0
    phase           TEXT,                          -- current phase label

    -- Input
    file_name       TEXT NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    file_mime       TEXT,
    context         TEXT,                          -- operator-provided extraction guidance
    whisper_model   TEXT NOT NULL DEFAULT 'small.en',
    extract_model_config_id INT,                   -- FK to model_configs, for UC extraction step
    extract_visuals BOOLEAN NOT NULL DEFAULT TRUE,
    frame_interval  INT NOT NULL DEFAULT 30,

    -- Working storage path (on the worker PVC)
    work_dir        TEXT,                          -- e.g., /workspace/recordings/rec-<uuid>/

    -- Results (retained after media cleanup)
    transcript      TEXT,                          -- full transcript text, retained permanently
    frame_descriptions JSONB,                      -- [{timestamp, type, description}, ...], retained permanently
    items           JSONB,                         -- [{yaml_content, rationale, source_excerpt}, ...], the extracted UCs
    error           TEXT,                          -- error message if failed

    -- Metadata
    duration_seconds REAL,                         -- total processing time
    audio_duration_seconds REAL,                   -- length of the recording itself
    frames_extracted INT DEFAULT 0,
    frames_analyzed  INT DEFAULT 0,
    media_cleaned    BOOLEAN NOT NULL DEFAULT FALSE, -- true after TTL cleanup of heavy artifacts
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_recording_jobs_project ON recording_jobs(project_id);
CREATE INDEX idx_recording_jobs_status ON recording_jobs(status);
```

Add `locality` column to `model_configs`:

```sql
ALTER TABLE model_configs ADD COLUMN locality TEXT NOT NULL DEFAULT 'local';
-- Values: 'local' (on-cluster, no data leaves trust boundary) or 'external' (cloud provider)
```

---

## New Deployment: recording-worker

A separate pod that watches the `recording_jobs` table for `status = 'queued'` jobs, claims them, and processes them.

### Container Image

Separate Containerfile: `review-console/worker/Containerfile`

```dockerfile
FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0
RUN dnf install -y --nodocs ffmpeg-free tesseract && dnf clean all
USER 1001

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Pre-download whisper model into the image so first job doesn't wait
RUN python3 -c "from pywhispercpp.model import Model; Model('small.en')"

CMD ["python3", "-m", "app.worker"]
```

### Worker requirements.txt

```
pywhispercpp>=1.4.0
asyncpg>=0.29
httpx>=0.27
pyyaml>=6.0
pytesseract>=0.3
opencv-python-headless>=4.9
```

### Worker loop (`review-console/worker/app/worker.py`)

```
while True:
    job = claim_next_queued_job()  -- UPDATE ... SET status='transcribing' WHERE status='queued' RETURNING *
    if job:
        process(job)
    else:
        sleep(5)
```

The worker:
1. Claims a job (atomic UPDATE with RETURNING to handle concurrency)
2. Downloads the recording file from the work_dir (API writes it there on submit)
3. Phase 2: ffmpeg convert → Whisper transcribe → UPDATE transcript column
4. Phase 3: If video + extract_visuals: ffmpeg keyframes → classify → OCR/skip
5. Phase 4: If diagram frames: call on-cluster vision model via HTTP
6. Phase 5: Call UC extraction via HTTP (same uc_assist endpoint, or direct DB + LLM call)
7. UPDATE status='done', items=<extracted UCs>
8. Schedule media cleanup after TTL

### Shared storage

The API pod and worker pod both mount the same PVC at `/workspace/recordings/`. The API writes the uploaded file there; the worker reads it, processes it, and writes intermediate artifacts. After completion + TTL, the worker deletes the media files but leaves the DB record (transcript + frame descriptions + items) intact.

PVC sizing: **5-10 GB** on encrypted CephFS (RWX). Comfortable for several concurrent jobs.

### Whisper model cache

Baked into the worker container image (the `RUN python3 -c "..."` line in the Containerfile downloads it at build time). Alternatively, mount a small PVC at `~/.local/share/pywhispercpp/models/` if you want to swap model sizes without rebuilding.

---

## API Endpoints (in main.py)

### Submit recording

```
POST /api/use-cases/from-recording
Content-Type: multipart/form-data
Authorization: Bearer <PAT>
X-DAV-Project: <project_id>

Fields:
  file:               UploadFile (required, max 500MB)
  context:            str (optional, max 4000 chars)
  extract_model_config_id: int (optional, for UC extraction model)
  whisper_model:      str (optional, default "small.en", allowed: small.en|medium.en|large)
  extract_visuals:    bool (optional, default true for video, false for audio)
  frame_interval:     int (optional, default 30)
```

Handler logic:
1. Auth + project scoping (existing patterns)
2. Validate file size and MIME type
3. Generate job_id = `rec-<uuid.uuid4().hex[:12]>`
4. Write uploaded file to `/workspace/recordings/<job_id>/original.<ext>`
5. INSERT into `recording_jobs` with status='queued'
6. If `extract_model_config_id` points to a model with `locality='external'`, return a warning field in the response: `"locality_warning": "UC extraction will send transcript text to <provider>. Proceed?"`
7. Return 202: `{"job_id": "rec-xxx", "status": "queued"}`

### Poll status

```
GET /api/use-cases/from-recording/{job_id}
Authorization: Bearer <PAT>
X-DAV-Project: <project_id>
```

Handler logic:
1. Auth + project scoping
2. SELECT from recording_jobs WHERE job_id = $1 AND project_id = $2
3. Return current state with progressive fields (transcript appears before items)

Response:
```json
{
  "job_id": "rec-xxx",
  "status": "transcribing",
  "progress": 0.45,
  "phase": "transcribing",
  "file_name": "2026-06-15 Truist Kranthi.m4a",
  "audio_duration_seconds": 3643,
  "transcript_ready": true,
  "transcript": "...",
  "frames_extracted": 0,
  "frames_analyzed": 0,
  "frame_descriptions": null,
  "items": null,
  "error": null,
  "created_at": "...",
  "started_at": "...",
  "completed_at": null
}
```

### List jobs

```
GET /api/use-cases/from-recording
Authorization: Bearer <PAT>
X-DAV-Project: <project_id>
```

Returns all jobs for the active project (summary, no transcript/items bodies).

### Delete job

```
DELETE /api/use-cases/from-recording/{job_id}
Authorization: Bearer <PAT>
X-DAV-Project: <project_id>
```

Stops processing (if in progress), deletes media from work_dir, deletes DB row.

### Re-extract UCs from existing transcript

```
POST /api/use-cases/from-recording/{job_id}/re-extract
Authorization: Bearer <PAT>
X-DAV-Project: <project_id>

Body (JSON):
  context:            str (optional, new/updated guidance)
  extract_model_config_id: int (optional, different model)
```

This is the payoff of retaining transcripts. Takes an existing completed job, re-runs only Phase 5 (UC extraction) with a different model or context. Creates a new items result without re-transcribing. Updates the same job row (overwrites items, resets status to extracting-ucs → done).

---

## Model Config Locality Flag

### Migration

```sql
ALTER TABLE model_configs ADD COLUMN locality TEXT NOT NULL DEFAULT 'local';
```

### API changes

- `GET /api/model-configs` response includes `locality` field
- `POST /api/model-configs` and `PUT` accept `locality`
- UI model selector shows a badge: `[local]` or `[external]` next to each model config

### Consent flow

When `POST /api/use-cases/from-recording` is called with a `extract_model_config_id` that has `locality='external'`:
- API returns the 202 response with an additional `locality_warning` field
- UI shows a confirmation dialog: _"The selected model ({model_name}) is hosted externally by {provider}. Transcript content from this recording will be sent to that provider. This recording may contain NDA-protected content. Continue?"_
- For API/CLI callers: the warning is informational. The operator already chose the model explicitly.

---

## Files to Create / Modify

### New files

| File | Purpose |
|------|---------|
| `review-console/worker/Containerfile` | Worker container image with ffmpeg, whisper, tesseract |
| `review-console/worker/requirements.txt` | Worker Python dependencies |
| `review-console/worker/app/__init__.py` | Package init |
| `review-console/worker/app/worker.py` | Main worker loop — claim jobs, process, update DB |
| `review-console/worker/app/transcribe.py` | Whisper transcription wrapper (sync, runs in worker) |
| `review-console/worker/app/frames.py` | Keyframe extraction, classification, OCR (Phase B) |
| `review-console/api/app/migrate_NNN_recording_jobs.py` | DB migration for recording_jobs table + locality column |
| `ansible/roles/dav/templates/recording-worker-deployment.yaml.j2` | K8s deployment manifest for worker |
| `ansible/roles/dav/templates/recording-worker-pvc.yaml.j2` | PVC for shared recording workspace |

### Modified files

| File | Changes |
|------|---------|
| `review-console/api/app/main.py` | Add 4 endpoints (submit, poll, list, delete, re-extract). ~120 lines. No heavy deps — just DB operations. |
| `review-console/api/app/main.py` | Add `locality` to model config CRUD endpoints |
| `review-console/ui/index.html` | "From Recording" button + modal (follow-on, not blocking) |
| `ansible/roles/dav/tasks/main.yml` | Add recording-worker deployment + PVC to the playbook |
| `ansible/roles/dav/defaults/main.yml` | Add recording worker config variables |

---

## Ansible Variables (defaults)

```yaml
dav_recording_worker_enabled: true
dav_recording_worker_image: "{{ dav_api_image }}"  # shares base, or separate image
dav_recording_worker_replicas: 1
dav_recording_worker_whisper_model: "small.en"
dav_recording_worker_media_ttl_hours: 1
dav_recording_workspace_pvc_size: "10Gi"
dav_recording_workspace_storage_class: "ocs-external-storagecluster-cephfs"
```

---

## Verification

1. **Submit:** Upload an m4a via curl, confirm 202 + job_id returned, confirm file written to PVC
2. **Worker claims:** Confirm worker pod logs show job claimed and transcription started
3. **Progress:** Poll GET endpoint, confirm status transitions and progress updates
4. **Transcript retention:** After completion, confirm transcript is in the DB. Delete media from PVC. Confirm transcript still accessible via GET.
5. **Re-extract:** Call re-extract with a different model config. Confirm new items generated from retained transcript without re-transcribing.
6. **Locality warning:** Configure an external model config. Submit a recording pointing to it. Confirm warning in response.
7. **Project scoping:** Submit a recording in project A. Confirm it's not visible from project B.
8. **Cleanup:** Confirm media files are deleted from PVC after TTL. Confirm DB row and transcript persist.
9. **Security:** Confirm no outbound network calls during transcription (network policy or tcpdump).
10. **Container:** Build worker image, deploy to OCP, confirm ffmpeg + whisper + tesseract work.

---

## Implementation Order

1. DB migration (recording_jobs table + locality column)
2. API endpoints (submit, poll, list, delete) — thin, just DB operations
3. Worker scaffolding (loop, claim, DB update)
4. transcribe.py in worker (ffmpeg + whisper)
5. Ansible deployment manifests (worker deployment + PVC)
6. Deploy and test end-to-end with audio-only
7. Re-extract endpoint
8. Model config locality flag + consent UX
9. frames.py (keyframe extraction + OCR) — Phase B
10. UI modal — follow-on

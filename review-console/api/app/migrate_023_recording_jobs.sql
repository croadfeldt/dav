-- #176 recording→use-case pipeline: DB-backed job state (dedicated-worker architecture).
-- The API enqueues a job (file bytes in-row for Phase A; PVC/object-store is the scale path);
-- a separate dav-recording-worker claims it (FOR UPDATE SKIP LOCKED), transcribes locally
-- (ffmpeg + whisper.cpp), extracts UCs (uc_assist.extract_bulk), and writes results back.
-- Project-scoped + TTL-expired. NO recording data leaves the trust boundary (local inference).
CREATE TABLE IF NOT EXISTS recording_jobs (
    job_id           text PRIMARY KEY,
    project_id       integer,
    submitted_by     text,
    status           text NOT NULL DEFAULT 'queued',  -- queued|claimed|transcribing|extracting-ucs|done|failed|cancelled
    phase            text,
    progress         real DEFAULT 0,
    file_name        text,
    content_type     text,
    file_bytes       bytea,            -- cleared on completion / TTL (Phase A; PVC later)
    file_size        bigint,
    context          text,
    model_config_id  integer,
    transcript       text,
    items            jsonb,            -- extracted UC drafts [{rationale, source_excerpt, yaml_content}]
    error            text,
    duration_seconds integer,
    worker           text,
    created_at       timestamptz DEFAULT now(),
    updated_at       timestamptz DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,
    expires_at       timestamptz
);
CREATE INDEX IF NOT EXISTS recording_jobs_queued_idx ON recording_jobs (created_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS recording_jobs_project_idx ON recording_jobs (project_id);

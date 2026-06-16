"""dav-recording-worker (#176) — a SEPARATE deployment from the API.

Claims `recording_jobs` rows (FOR UPDATE SKIP LOCKED), transcribes locally
(ffmpeg → 16kHz mono WAV → whisper.cpp), extracts UC drafts via
`uc_assist.extract_bulk`, and writes results back. Heavy media work never touches
the live API pod. NO recording data leaves the trust boundary — all inference is
local (whisper.cpp on CPU; the extraction LLM is the project's configured model,
local by default).

Run as: python -m app.recording_worker   (WORKDIR /opt/app-root/src)
Env: DATABASE_URL (required), WHISPER_MODEL (default small.en),
     RECORDING_POLL_SECONDS (default 5), WHISPER_CACHE (writable model dir).
"""
import asyncio
import json
import logging
import os
import socket
import subprocess
import tempfile

import asyncpg

from . import uc_assist

log = logging.getLogger("dav-recording-worker")

DB_DSN = os.environ["DATABASE_URL"]
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small.en")
POLL_SECONDS = float(os.environ.get("RECORDING_POLL_SECONDS", "5"))
WORKER_ID = os.environ.get("HOSTNAME") or socket.gethostname()


def _to_wav(src_path: str, wav_path: str) -> None:
    """Extract/convert to 16kHz mono WAV (audio from video too). ffmpeg, local."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", src_path, "-ar", "16000", "-ac", "1", wav_path],
        check=True, capture_output=True)


def _transcribe(wav_path: str) -> str:
    """whisper.cpp via pywhispercpp (CPU). Lazy import so the worker process still
    boots if the model/binding has issues — failures surface per-job, not at start."""
    from pywhispercpp.model import Model
    model = Model(WHISPER_MODEL)
    out = []
    for seg in model.transcribe(wav_path):
        txt = (getattr(seg, "text", "") or "").strip()
        if txt:
            out.append(txt)
    return "\n".join(out).strip()


async def _resolve_cfg(conn, project_id, model_config_id):
    """Mirror the bulk-from-text resolver: explicit model_config_id, else the
    project's uc-authoring default. Local model by default."""
    if model_config_id is not None:
        row = await conn.fetchrow("SELECT * FROM model_configs WHERE id=$1 AND enabled", model_config_id)
        if row:
            return dict(row)
    did = await conn.fetchval(
        "SELECT model_config_id FROM model_defaults WHERE key='uc-authoring' AND project_id=$1", project_id)
    if did is not None:
        row = await conn.fetchrow(
            "SELECT * FROM model_configs WHERE id=$1 AND (project_id IS NULL OR project_id=$2) AND enabled",
            did, project_id)
        if row:
            return dict(row)
    return None


async def _process(pool, job) -> None:
    job_id = job["job_id"]

    async def _set(**cols):
        keys = list(cols)
        sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(keys))
        async with pool.acquire() as c:
            await c.execute(f"UPDATE recording_jobs SET {sets}, updated_at=now() WHERE job_id=$1",
                            job_id, *[cols[k] for k in keys])

    try:
        await _set(status="transcribing", phase="transcribing", progress=0.1)
        data = job["file_bytes"]
        if not data:
            await _set(status="failed", phase="failed", error="no file bytes (expired or cancelled)", progress=1.0)
            return
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, (job["file_name"] or "recording.bin").replace("/", "_"))
            with open(src, "wb") as f:
                f.write(data)
            wav = os.path.join(td, "audio.wav")
            await asyncio.to_thread(_to_wav, src, wav)
            await _set(phase="transcribing", progress=0.3)
            transcript = await asyncio.to_thread(_transcribe, wav)
        if not transcript:
            await _set(status="failed", phase="failed", error="empty transcript", progress=1.0)
            return
        await _set(transcript=transcript, status="extracting-ucs", phase="extracting-ucs", progress=0.7)
        async with pool.acquire() as c:
            cfg = await _resolve_cfg(c, job["project_id"], job["model_config_id"])
        result = await uc_assist.extract_bulk(text=transcript, context=job["context"], cfg=cfg)
        if result.get("error"):
            await _set(status="failed", phase="failed", error=str(result["error"])[:2000], progress=1.0)
            return
        items = result.get("items", [])
        async with pool.acquire() as c:
            await c.execute(
                "UPDATE recording_jobs SET status='done', phase='done', progress=1.0, "
                "items=$2, file_bytes=NULL, finished_at=now(), updated_at=now() WHERE job_id=$1",
                job_id, json.dumps(items))
        log.info("job %s done: %d UC draft(s)", job_id, len(items))
    except Exception as e:  # noqa: BLE001 — never let one job kill the worker
        log.exception("job %s failed", job_id)
        try:
            await _set(status="failed", phase="failed", error=str(e)[:2000], progress=1.0)
        except Exception:
            pass


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=3, command_timeout=120)
    log.info("dav-recording-worker %s up; whisper=%s; poll=%ss", WORKER_ID, WHISPER_MODEL, POLL_SECONDS)
    while True:
        try:
            async with pool.acquire() as conn:
                job = await conn.fetchrow(
                    "UPDATE recording_jobs SET status='claimed', worker=$1, started_at=now(), updated_at=now() "
                    "WHERE job_id = (SELECT job_id FROM recording_jobs WHERE status='queued' "
                    "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *", WORKER_ID)
            if job:
                await _process(pool, job)
                continue
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())

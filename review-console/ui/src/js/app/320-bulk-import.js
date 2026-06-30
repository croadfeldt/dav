// ══════════════════════════ BULK IMPORT (M12a / ADR-008) ══════════════════════════
//
// Paste a transcript / notes; the LLM extracts N distinct UC drafts; reviewer
// trims and saves; then chooses Set assignment (skip / new / existing / individual).
//
// State lives only on the modal — no globals — so reopening starts fresh.

let _biExtracted = [];   // [{yaml_content, rationale, source_excerpt, _keep, _ucUuid?}]
let _biSavedUCs = [];    // [{uuid, title}] populated after Step 2 save

function openBulkImport() {
  document.getElementById('bulkImportModal').classList.add('open');
  document.getElementById('biStep1').style.display = '';
  document.getElementById('biStep2').style.display = 'none';
  document.getElementById('biStep3').style.display = 'none';
  document.getElementById('biSourceText').value = '';
  document.getElementById('biContext').value = '';
  document.getElementById('biStatus').textContent = '';
  document.getElementById('biExtractStatus').textContent = '';
  document.getElementById('biResults').innerHTML = '';
  document.getElementById('biSavedSummary').textContent = '';
  document.getElementById('biSetNewName').value = '';
  document.getElementById('biBackBtn').style.display = 'none';
  const primary = document.getElementById('biPrimaryBtn');
  primary.textContent = 'Extract UCs';
  primary.disabled = false;
  primary.onclick = biRunExtract;
  _biExtracted = [];
  _biSavedUCs = [];
  _populateOverrideSel('biModelSel', 'uc-authoring');
  const txtRadio = document.querySelector('input[name="biSource"][value="text"]'); if (txtRadio) txtRadio.checked = true;
  const recFile = document.getElementById('biRecordingFile'); if (recFile) recFile.value = '';
  const recProg = document.getElementById('biRecordingProgress'); if (recProg) recProg.innerHTML = '';
  _biSetSource('text');
}

function closeBulkImport() {
  document.getElementById('bulkImportModal').classList.remove('open');
}

// Source toggle: paste text vs upload a recording (#176). The recording path uploads to
// the dav-recording-worker (local transcribe + extract) and funnels drafts into the same
// review step. (Browser-side transcription is the privacy-preferred enhancement — #180.)
let _biMode = 'text';
function _biSetSource(mode) {
  _biMode = mode;
  const t = document.getElementById('biTextRow'), r = document.getElementById('biRecordingRow');
  if (t) t.style.display = mode === 'text' ? '' : 'none';
  if (r) r.style.display = (mode === 'recording' || mode === 'browser') ? '' : 'none';
  const note = document.getElementById('biSourceNote');
  if (note) {
    if (mode === 'recording') {
      note.innerHTML = '<span style="color:var(--orange,#c8861a);">⚠ This uploads the recording to a remote server for transcription.</span> For better privacy &amp; security, prefer <strong>🔒 Transcribe in browser</strong> — that keeps the recording on your device and sends only the transcript text.';
    } else if (mode === 'browser') {
      note.innerHTML = 'Audio/video is transcribed <strong>in your browser</strong> (whisper; video audio via ffmpeg.wasm) — the recording never leaves your device. Only the resulting <strong>transcript text</strong> is then sent to the remote model, which creates the UCs. First run downloads the model(s) (then cached).';
    } else { note.innerHTML = ''; }
  }
}

// Shared "drafts → review step" transition, used by both the text and recording paths.
function _biShowDrafts(items) {
  const primary = document.getElementById('biPrimaryBtn');
  _biExtracted = items.map(it => ({ ...it, _keep: true }));
  biRenderResults();
  document.getElementById('biStep1').style.display = 'none';
  document.getElementById('biStep2').style.display = '';
  document.getElementById('biBackBtn').style.display = '';
  document.getElementById('biBackBtn').onclick = () => {
    document.getElementById('biStep1').style.display = '';
    document.getElementById('biStep2').style.display = 'none';
    document.getElementById('biBackBtn').style.display = 'none';
    primary.textContent = 'Extract UCs';
    primary.onclick = biRunExtract;
  };
  primary.textContent = 'Save selected →';
  primary.disabled = false;
  primary.onclick = biSaveSelected;
  document.getElementById('biStatus').textContent = '';
  document.getElementById('biExtractStatus').textContent =
    `${items.length} draft${items.length === 1 ? '' : 's'} proposed — uncheck the ones to skip, then click Save selected.`;
}

async function biRunExtractFromRecording() {
  const fileEl = document.getElementById('biRecordingFile');
  const file = fileEl && fileEl.files && fileEl.files[0];
  if (!file) { toast('Choose a recording file first', true); return; }
  const primary = document.getElementById('biPrimaryBtn');
  primary.disabled = true;
  const prog = document.getElementById('biRecordingProgress');
  const setProg = (m) => { if (prog) prog.innerHTML = m; };
  document.getElementById('biStatus').textContent = '';
  setProg('<span class="llm-spinner"></span>uploading…');
  const PHASE = {
    queued: 'queued…', claimed: 'starting…', transcribing: 'transcribing audio locally (whisper)…',
    'extracting-ucs': 'extracting use cases…', done: 'done', failed: 'failed',
  };
  try {
    const mb = _overrideModelBody('biModelSel');
    const fd = new FormData();
    fd.append('file', file);
    const ctx = document.getElementById('biContext').value.trim();
    if (ctx) fd.append('context', ctx);
    if (mb.model_config_id) fd.append('model_config_id', mb.model_config_id);
    const res = await fetch(API + '/api/use-cases/from-recording', {
      method: 'POST',
      headers: { ...(typeof _activeProject !== 'undefined' && _activeProject ? { 'X-DAV-Project': _activeProject } : {}) },
      body: fd,
    });
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    const { job_id } = await res.json();
    let tries = 0;
    for (;;) {
      await new Promise(r => setTimeout(r, 4000));
      let j;
      try { j = await api('/api/use-cases/from-recording/' + job_id); }
      catch (e) { setProg(`<span style="color:var(--red)">${esc(e.message)}</span>`); primary.disabled = false; return; }
      const ph = j.phase || j.status;
      setProg(`<span class="llm-spinner"></span>${esc(PHASE[ph] || ph)}${j.transcript_ready ? ' · transcript ready' : ''}`);
      if (j.status === 'done') {
        const items = j.items || [];
        if (!items.length) { setProg('Transcribed, but the model found nothing UC-shaped in it.'); primary.disabled = false; return; }
        setProg(`Transcribed + extracted ${items.length} draft${items.length === 1 ? '' : 's'}.`);
        _biShowDrafts(items);
        return;
      }
      if (j.status === 'failed' || j.status === 'cancelled') {
        setProg(`<span style="color:var(--red)">${esc(j.error || j.status)}</span>`);
        primary.disabled = false;
        return;
      }
      if (++tries > 450) { setProg('<span style="color:var(--red)">timed out waiting for the worker</span>'); primary.disabled = false; return; }
    }
  } catch (e) {
    setProg(`<span style="color:var(--red)">${esc(e.message)}</span>`);
    primary.disabled = false;
  }
}

// ── In-browser transcription (#180) — the audio NEVER leaves the device. ──
// Whisper runs client-side via Transformers.js (WebGPU, WASM fallback); only the
// resulting transcript text is sent to the remote model for extraction. The speech
// model downloads from a CDN on first use and is cached in the browser (no audio uploads).
const _BS_TJS_URL = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3';
const _BS_MODEL = 'Xenova/whisper-base.en';   // base.en = good speed/quality for the browser
let _bsTranscriber = null;

// ffmpeg.wasm (in-browser) — extracts the audio track from any container (video too),
// single-threaded core so it runs without cross-origin isolation. The file never leaves.
const _BS_FFMPEG_VER = '0.12.10', _BS_FFMPEG_UTIL_VER = '0.12.1', _BS_FFMPEG_CORE_VER = '0.12.6';
let _bsFfmpeg = null;
let _bsFfProgressCb = null;
async function _bsGetFfmpeg(setProg) {
  if (_bsFfmpeg) return _bsFfmpeg;
  if (setProg) setProg('<span class="llm-spinner"></span>loading ffmpeg in-browser (~30 MB first run; cached after)…');
  const FF = await import(`https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@${_BS_FFMPEG_VER}/dist/esm/index.js`);
  const U = await import(`https://cdn.jsdelivr.net/npm/@ffmpeg/util@${_BS_FFMPEG_UTIL_VER}/dist/esm/index.js`);
  const ffmpeg = new FF.FFmpeg();
  ffmpeg.on('progress', ({ progress }) => { if (_bsFfProgressCb) _bsFfProgressCb(progress); });
  const base = `https://cdn.jsdelivr.net/npm/@ffmpeg/core@${_BS_FFMPEG_CORE_VER}/dist/esm`;
  await ffmpeg.load({
    coreURL: await U.toBlobURL(`${base}/ffmpeg-core.js`, 'text/javascript'),
    wasmURL: await U.toBlobURL(`${base}/ffmpeg-core.wasm`, 'application/wasm'),
  });
  _bsFfmpeg = ffmpeg;
  return ffmpeg;
}
async function _bsFfmpegExtractWav(file, setProg) {
  const ffmpeg = await _bsGetFfmpeg(setProg);
  const ext = (file.name && file.name.includes('.')) ? file.name.split('.').pop().toLowerCase().replace(/[^a-z0-9]/g, '') : 'bin';
  const inName = 'in.' + (ext || 'bin'), outName = 'out.wav';
  _bsFfProgressCb = (p) => { if (setProg) setProg(`<span class="llm-spinner"></span>extracting audio from the file (ffmpeg) — ${Math.round((p || 0) * 100)}%…`); };
  if (setProg) setProg('<span class="llm-spinner"></span>extracting audio from the file (ffmpeg, in-browser)…');
  try {
    await ffmpeg.writeFile(inName, new Uint8Array(await file.arrayBuffer()));
    await ffmpeg.exec(['-i', inName, '-vn', '-ar', '16000', '-ac', '1', '-f', 'wav', outName]);
    const data = await ffmpeg.readFile(outName);
    try { await ffmpeg.deleteFile(inName); await ffmpeg.deleteFile(outName); } catch {}
    return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
  } finally { _bsFfProgressCb = null; }
}
async function _bsDecodeTo16kMono(file, setProg) {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) throw new Error('Web Audio API not available in this browser.');
  const isVideo = (file.type && file.type.startsWith('video/')) ||
    /\.(mp4|mov|mkv|webm|avi|m4v|wmv|flv|ts|mpg|mpeg)$/i.test(file.name || '');
  let abuf = isVideo ? await _bsFfmpegExtractWav(file, setProg) : await file.arrayBuffer();
  const ac = new AC();
  let decoded;
  try {
    decoded = await ac.decodeAudioData(abuf.slice(0));
  } catch (e) {
    if (!isVideo) {
      // audio codec the browser can't decode natively (e.g. some m4a/opus) → ffmpeg, then retry
      try { abuf = await _bsFfmpegExtractWav(file, setProg); decoded = await ac.decodeAudioData(abuf.slice(0)); }
      catch (e2) { try { ac.close(); } catch {} throw new Error('Could not decode this file in-browser. Try the "Upload recording" path (server worker).'); }
    } else {
      try { ac.close(); } catch {}
      throw new Error('Could not decode the extracted audio. Try the "Upload recording" path (server worker).');
    }
  }
  try { ac.close(); } catch {}
  const rate = 16000;
  const frames = Math.max(1, Math.ceil(decoded.duration * rate));
  const off = new OfflineAudioContext(1, frames, rate);
  const src = off.createBufferSource();
  src.buffer = decoded;
  src.connect(off.destination);
  src.start(0);
  const rendered = await off.startRendering();
  return rendered.getChannelData(0);
}
function _bsFmtEta(s) { s = Math.round(s); if (s < 60) return s + 's'; const m = Math.floor(s / 60), r = s % 60; return m + 'm' + (r ? ' ' + r + 's' : ''); }

async function _bsGetTranscriber(setProg) {
  if (_bsTranscriber) return _bsTranscriber;
  const mod = await import(_BS_TJS_URL);
  const { pipeline, env } = mod;
  try { env.allowLocalModels = false; } catch {}
  // No cross-origin isolation (no COOP/COEP) → SharedArrayBuffer is unavailable, so force
  // single-threaded WASM; otherwise onnxruntime-web's threaded path fails on the fallback.
  try { env.backends.onnx.wasm.numThreads = 1; } catch {}
  const device = (typeof navigator !== 'undefined' && navigator.gpu) ? 'webgpu' : 'wasm';
  _bsTranscriber = await pipeline('automatic-speech-recognition', _BS_MODEL, {
    device,
    progress_callback: (p) => {
      if (p && p.status === 'progress' && p.file) {
        setProg(`<span class="llm-spinner"></span>downloading speech model (${device}): ${esc(p.file)} ${Math.round(p.progress || 0)}%`);
      }
    },
  });
  return _bsTranscriber;
}

async function biRunExtractInBrowser() {
  const fileEl = document.getElementById('biRecordingFile');
  const file = fileEl && fileEl.files && fileEl.files[0];
  if (!file) { toast('Choose a recording file first', true); return; }
  const primary = document.getElementById('biPrimaryBtn');
  primary.disabled = true;
  const prog = document.getElementById('biRecordingProgress');
  const setProg = (m) => { if (prog) prog.innerHTML = m; };
  document.getElementById('biStatus').textContent = '';
  try {
    setProg('<span class="llm-spinner"></span>decoding audio locally…');
    const audio = await _bsDecodeTo16kMono(file, setProg);
    setProg('<span class="llm-spinner"></span>loading the speech model (first run downloads it; cached after)…');
    const transcriber = await _bsGetTranscriber(setProg);
    const totalSec = audio.length / 16000;
    const t0 = Date.now();
    const updateProg = (curSec) => {
      const frac = Math.max(0, Math.min(0.99, curSec / Math.max(1, totalSec)));
      const elapsed = (Date.now() - t0) / 1000;
      const eta = frac > 0.03 ? (elapsed * (1 - frac) / frac) : null;
      setProg(`<span class="llm-spinner"></span>transcribing in your browser — ${Math.round(frac * 100)}%${eta ? ` · ~${_bsFmtEta(eta)} left` : ''} (audio never leaves this device)`);
    };
    setProg('<span class="llm-spinner"></span>transcribing in your browser — the recording never leaves this device…');
    let streamer = null;
    try {
      const { WhisperTextStreamer } = await import(_BS_TJS_URL);
      const tp = transcriber.processor.feature_extractor.config.chunk_length / transcriber.model.config.max_source_positions;
      streamer = new WhisperTextStreamer(transcriber.tokenizer, {
        time_precision: tp,
        on_chunk_start: (t) => updateProg(t),
        on_chunk_end: (t) => updateProg(t),
      });
    } catch (_) { streamer = null; }
    const opts = { chunk_length_s: 30, stride_length_s: 5, return_timestamps: true };
    if (streamer) opts.streamer = streamer;
    const out = await transcriber(audio, opts);
    const transcript = ((out && out.text) || '').trim();
    if (!transcript) { setProg('No speech detected in the recording.'); primary.disabled = false; return; }
    document.getElementById('biSourceText').value = transcript;
    const txtRadio = document.querySelector('input[name="biSource"][value="text"]'); if (txtRadio) txtRadio.checked = true;
    _biSetSource('text');
    setProg(`Transcribed locally (${transcript.length} chars). Only this transcript is sent for extraction.`);
    await biRunExtract();   // text path — sends just the transcript to the remote model
  } catch (e) {
    setProg(`<span style="color:var(--red)">${esc(e.message)}</span>`);
    primary.disabled = false;
  }
}

async function biRunExtract() {
  if (_biMode === 'recording') return biRunExtractFromRecording();
  if (_biMode === 'browser') return biRunExtractInBrowser();
  const text = document.getElementById('biSourceText').value.trim();
  if (!text) { toast('Paste some text first', true); return; }
  const primary = document.getElementById('biPrimaryBtn');
  primary.disabled = true;
  document.getElementById('biStatus').innerHTML = '<span class="llm-spinner"></span>extracting UCs…';
  const payload = {
    text,
    context: document.getElementById('biContext').value.trim() || null,
    ..._overrideModelBody('biModelSel'),
  };
  const pane = document.getElementById('biStep1');
  const approxMin = Math.max(1, Math.ceil(text.length / 4000));
  const hideBusy = _showBusy(pane, `Extracting UCs from your text — large transcripts can take ${approxMin}–${approxMin * 2} min on local models. Don't close this window.`);
  try {
    const resp = await api('/api/use-cases/bulk-from-text', {
      method: 'POST', body: JSON.stringify(payload),
    });
    const items = resp.items || [];
    if (!items.length) {
      const why = resp.no_ucs_reason || 'Model found nothing UC-shaped in the source text.';
      document.getElementById('biStatus').innerHTML =
        `<span style="color:var(--text-faint)">${esc(why)}</span>`;
      primary.disabled = false;
      return;
    }
    _biShowDrafts(items);
  } catch (e) {
    document.getElementById('biStatus').innerHTML =
      `<span style="color:var(--red)">${esc(e.message)}</span>`;
    primary.disabled = false;
  } finally {
    hideBusy();
  }
}

function biRenderResults() {
  const el = document.getElementById('biResults');
  el.innerHTML = '';
  _biExtracted.forEach((it, idx) => {
    const card = document.createElement('div');
    const failed = !!it._biSaveError;
    const saved  = !!it._ucUuid;
    const accent = failed ? 'var(--red)' : (saved ? 'var(--green)' : 'var(--border)');
    card.style.cssText = `border:1px solid ${accent}; border-radius:2px; background:var(--bg-input); padding:8px 10px;`;
    const titleMatch = /^title:\s*(.+)$/m.exec(it.yaml_content || '');
    const title = titleMatch ? titleMatch[1].trim().replace(/^["']|["']$/g, '') : '(untitled draft)';
    const detailOpen = failed ? '' : 'none';
    const statusBadge = saved
      ? `<span style="font-size:9px; padding:2px 6px; background:var(--green-bg, rgba(80,180,80,0.15)); color:var(--green); border-radius:2px; font-weight:600;">SAVED</span>`
      : failed
        ? `<span style="font-size:9px; padding:2px 6px; background:rgba(217,101,58,0.15); color:var(--red); border-radius:2px; font-weight:600;">FAILED</span>`
        : '';
    card.innerHTML = `
      <div style="display:flex; align-items:center; gap:8px;">
        <input type="checkbox" data-idx="${idx}" class="bi-keep" ${it._keep ? 'checked' : ''}
               ${saved ? 'disabled' : ''}
               style="width:auto; height:auto; accent-color:var(--accent);" />
        <strong style="flex:1; font-size:13px;">${esc(title)}</strong>
        ${statusBadge}
        <button class="btn ghost btn-sm" data-idx="${idx}" data-act="toggle" style="font-size:10px;">▾ details</button>
      </div>
      ${failed ? `<div style="margin-top:6px; padding:6px 8px; background:rgba(217,101,58,0.10); border-left:3px solid var(--red); font-size:11px; color:var(--text);">
        <strong>Validation failed:</strong> ${esc(it._biSaveError)}<br>
        <span style="color:var(--text-faint); font-size:10px;">Fix the YAML below (expand details ▾) and click <strong>↻ Retry save</strong>.</span>
      </div>` : ''}
      <div data-detail="${idx}" style="display:${detailOpen}; margin-top:6px;">
        ${it.rationale ? `<div style="font-size:11px; color:var(--text-faint); margin-bottom:4px;"><strong>why:</strong> ${esc(it.rationale)}</div>` : ''}
        ${it.source_excerpt ? `<div style="font-size:11px; color:var(--text-faint); margin-bottom:4px; font-style:italic;">"${esc(it.source_excerpt)}"</div>` : ''}
        <textarea data-idx="${idx}" class="bi-yaml" rows="10" spellcheck="false" style="width:100%; font-family:ui-monospace, monospace; font-size:11px; line-height:1.5;">${esc(it.yaml_content || '')}</textarea>
      </div>`;
    el.appendChild(card);
  });
  el.querySelectorAll('input.bi-keep').forEach(box => {
    box.addEventListener('change', e => {
      _biExtracted[parseInt(e.target.dataset.idx, 10)]._keep = e.target.checked;
    });
  });
  el.querySelectorAll('textarea.bi-yaml').forEach(t => {
    t.addEventListener('input', e => {
      _biExtracted[parseInt(e.target.dataset.idx, 10)].yaml_content = e.target.value;
    });
  });
  el.querySelectorAll('button[data-act="toggle"]').forEach(b => {
    b.addEventListener('click', e => {
      const idx = e.currentTarget.dataset.idx;
      const detail = el.querySelector(`[data-detail="${idx}"]`);
      detail.style.display = detail.style.display === 'none' ? '' : 'none';
    });
  });
}

async function biSaveSelected() {
  const toSave = _biExtracted.filter(it => it._keep);
  if (!toSave.length) { toast('Nothing selected to save', true); return; }
  const primary = document.getElementById('biPrimaryBtn');
  primary.disabled = true;
  const status = document.getElementById('biStatus');
  _biSavedUCs = [];
  const failures = [];   // { index, title, error_detail }
  for (let i = 0; i < toSave.length; i++) {
    const it = toSave[i];
    status.textContent = `saving ${i+1}/${toSave.length}…`;
    const titleMatch = /^title:\s*(.+)$/m.exec(it.yaml_content || '');
    const title = titleMatch ? titleMatch[1].trim().replace(/^["']|["']$/g, '') : `(item ${i+1} — untitled)`;
    try {
      const resp = await api('/api/use-cases', {
        method: 'POST',
        body: JSON.stringify({yaml_content: it.yaml_content, tags: []}),
      });
      const uuid = resp.uuid || resp.use_case?.uuid;
      _biSavedUCs.push({uuid, title, yaml_content: it.yaml_content});
      it._ucUuid = uuid;
      it._biSaveError = null;
    } catch (e) {
      // Best-effort extract validation errors from the structured 400 body
      let detail = e.message;
      try {
        const parsed = JSON.parse(e.message.replace(/^[^{]*/, ''));
        const inner = parsed.detail && typeof parsed.detail === 'object' ? parsed.detail : parsed;
        if (inner.errors && Array.isArray(inner.errors)) {
          detail = inner.errors.join('; ');
        } else if (inner.message) {
          detail = inner.message;
        }
      } catch(_) { /* keep raw message */ }
      failures.push({index: i+1, title, error: detail, _it: it});
      it._biSaveError = detail;
    }
  }
  status.textContent = '';
  biRenderResults();   // re-render cards so failed UCs show their inline error
  const okN = _biSavedUCs.length;
  const failN = failures.length;
  const summaryEl = document.getElementById('biSavedSummary');
  // Hard block on zero successes — DON'T silently proceed to Set creation
  if (okN === 0) {
    summaryEl.style.color = 'var(--red)';
    summaryEl.innerHTML =
      `<strong>0 UCs saved</strong> — all ${failN} failed engine validation.<br>`
      + `<div style="margin-top:6px;font-size:11px;color:var(--text-dim);">Each failed card below shows its specific error inline; common cause is a value put in the wrong <code>dimensions.*</code> slot (e.g., <code>expiry_enforcement</code> belongs in <code>lifecycle_phase</code>, not <code>failure_mode</code>). Fix the YAML in-place and click <strong>↻ Retry save</strong>, or click <strong>← Back</strong> to re-extract.</div>`;
    primary.textContent = '↻ Retry save';
    primary.disabled = false;
    primary.onclick = biSaveSelected;   // retry path
    // Re-show Back so the user can re-extract or pivot
    const backBtn = document.getElementById('biBackBtn');
    if (backBtn) backBtn.style.display = '';
    return;
  }
  summaryEl.style.color = '';
  summaryEl.innerHTML =
    `<strong>${okN}</strong> UC${okN===1?'':'s'} saved as drafts.` +
    (failN ? ` <span style="color:var(--red)">${failN} failed</span> — see inline errors on the cards above; fix and re-extract if you want them too.` : '');
  document.getElementById('biStep2').style.display = 'none';
  document.getElementById('biStep3').style.display = '';
  await biPrepStep3();
  document.getElementById('biBackBtn').style.display = 'none';
  primary.textContent = 'Finish';
  primary.disabled = false;
  primary.onclick = biApplySetAssignment;
}

async function biPrepStep3() {
  // ALWAYS re-fetch sets so the dropdown reflects current server state.
  // (Using cached `allSets` once cost us: a set deleted earlier in the
  // session showed up in the dropdown, member-add POSTs 404'd, errors
  // were silently swallowed, and the user ended up thinking the Scoping Set
  // assignment had deleted the set when really it never wrote a member.)
  try { await loadSets(); } catch(_) {}
  const sel = document.getElementById('biSetExistingSel');
  sel.innerHTML = '<option value="">— pick a set —</option>' +
    (allSets || []).filter(s => s.id !== ALL_SET_ID).map(s => `<option value="${s.id}">${esc(s.name)}${s.is_default?' (default)':''}</option>`).join('');
  // Individual-pickers grid
  const grid = document.getElementById('biIndividualPickers');
  grid.innerHTML = '';
  _biSavedUCs.forEach(uc => {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex; align-items:center; gap:6px; font-size:11px;';
    const opts = '<option value="">— none —</option>' +
      (allSets || []).filter(s => s.id !== ALL_SET_ID).map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    row.innerHTML = `<span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${esc(uc.title)}</span>
      <select data-ucuuid="${esc(uc.uuid)}" class="bi-indiv-sel" style="flex:0 0 200px;">${opts}</select>`;
    grid.appendChild(row);
  });
  // Wire radio→subfield visibility
  document.querySelectorAll('input[name="biSetMode"]').forEach(r => {
    r.addEventListener('change', () => {
      const mode = document.querySelector('input[name="biSetMode"]:checked').value;
      document.getElementById('biIndividualPickers').style.display = (mode === 'individual') ? '' : 'none';
    });
  });
}

async function biApplySetAssignment() {
  const mode = document.querySelector('input[name="biSetMode"]:checked').value;
  const primary = document.getElementById('biPrimaryBtn');
  primary.disabled = true;
  const status = document.getElementById('biStatus');
  try {
    let targetSetId = null;
    if (mode === 'new') {
      const name = document.getElementById('biSetNewName').value.trim();
      if (!name) { toast('Name the new Set or pick another option', true); primary.disabled = false; return; }
      status.textContent = 'creating set…';
      const created = await api('/api/sets', {method:'POST', body:JSON.stringify({name, description: 'created from bulk import'})});
      targetSetId = created.id || created.set?.id;
    } else if (mode === 'existing') {
      const v = document.getElementById('biSetExistingSel').value;
      if (!v) { toast('Pick an existing Set', true); primary.disabled = false; return; }
      targetSetId = parseInt(v, 10);
    }
    // Track per-UC member-add outcomes so the operator gets a clear final
    // summary instead of a silent "success" toast when many adds 404'd.
    // The bug this guards against: a stale set id from a deleted-but-cached
    // dropdown entry → all member-adds 404 → bulk modal claims success →
    // operator thinks the bulk flow deleted the set when it never wrote.
    const addOk = []; const addFail = [];
    if (mode === 'skip') {
      // Nothing to do — drafts already saved
    } else if (mode === 'individual') {
      const sels = document.querySelectorAll('select.bi-indiv-sel');
      for (const s of sels) {
        const sid = s.value ? parseInt(s.value, 10) : null;
        if (!sid) continue;
        status.textContent = `assigning ${s.dataset.ucuuid.slice(0,8)}…`;
        try {
          await api(`/api/sets/${sid}/members`, {
            method: 'POST',
            body: JSON.stringify({uc_uuid: s.dataset.ucuuid, uc_source: 'managed'}),
          });
          addOk.push(s.dataset.ucuuid);
        } catch (e) {
          addFail.push({uc_uuid: s.dataset.ucuuid, set_id: sid, error: e.message});
        }
      }
    } else if (targetSetId) {
      for (const uc of _biSavedUCs) {
        status.textContent = `adding ${uc.title.slice(0,30)}…`;
        try {
          await api(`/api/sets/${targetSetId}/members`, {
            method: 'POST',
            body: JSON.stringify({uc_uuid: uc.uuid, uc_source: 'managed'}),
          });
          addOk.push(uc.uuid);
        } catch (e) {
          addFail.push({uc_uuid: uc.uuid, title: uc.title, set_id: targetSetId, error: e.message});
        }
      }
    }
    if (addFail.length) {
      // Don't close the modal — show the failures so the operator can
      // pick a different set / new set / skip and recover.
      const summary = document.getElementById('biSavedSummary');
      const setLabel = mode === 'existing'
        ? (allSets.find(s => s.id === targetSetId)?.name || `id=${targetSetId}`)
        : (mode === 'new' ? 'new set' : 'individual selections');
      summary.style.color = '';
      summary.innerHTML = `<strong>${_biSavedUCs.length}</strong> UC${_biSavedUCs.length===1?'':'s'} saved as drafts.<br>`
        + `<span style="color:var(--red);"><strong>${addFail.length}</strong> set membership${addFail.length===1?'':'s'} failed</strong> — ${esc(setLabel)} may have been deleted, or the UC was rejected.</span>`
        + `<details style="margin-top:6px;font-size:11px;"><summary style="cursor:pointer;color:var(--text-faint);">show failure details</summary>`
        + `<ul style="margin:4px 0 0 16px;padding:0;">`
        + addFail.map(f => `<li>${esc(f.title || f.uc_uuid.slice(0,12))}: ${esc(f.error)}</li>`).join('')
        + `</ul></details>`
        + `<div style="margin-top:8px;font-size:11px;color:var(--text-dim);">The UCs themselves are saved (visible in the Use Cases list). Pick a different set option above and click <strong>Finish</strong> again to retry; the already-added members won't be duplicated (the API is idempotent on conflict).</div>`;
      primary.disabled = false;
      return;   // stay on step 3
    }
    toast(`Bulk import complete: ${_biSavedUCs.length} UC(s) saved, ${addOk.length} added to set`);
    closeBulkImport();
    try { await loadUCs(); } catch(_) {}
    try { await loadSets(); } catch(_) {}
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    primary.disabled = false;
  }
}

function log_failures(uuid, e) {
  console.warn('bulk: set assignment failed for', uuid, e);
}

// ══════════════ Maturity Wall (#147 slice 3) ══════════════
// FlightPath-style wall: capabilities grouped into ordered categories/bands, each scored 0–5 in
// the framework's appraisal scale (heat-mapped), with a state switcher (Current/Phase 1-3/Desired)
// and an Inflection-Point divider. Reads /api/assessments/{id}/maturity-wall (or the seed skeleton).
let _mwState = 'current';
let _mwWall = null;

async function loadMaturityWall(){
  const sel = document.getElementById('mwAssessSel');
  const status = document.getElementById('mwStatus');
  if (!sel) return;
  try {
    const r = await api('/api/assessments');
    const list = r.assessments || [];
    const prev = sel.value;
    sel.innerHTML = '<option value="">— framework skeleton (no scores) —</option>'
      + list.map(a => `<option value="${esc(a.id)}">${esc(a.handle || a.id)}</option>`).join('');
    if (prev) sel.value = prev;
  } catch(e){ if (status) status.textContent = 'Failed to load assessments: ' + e.message; }
  await _mwRender();
}

function _mwScaleColor(wall, v){
  if (v === null || v === undefined) return 'var(--bg-raised)';
  const lvl = (wall.scale || []).find(s => s.value === v);
  return lvl ? lvl.color : 'var(--bg-raised)';
}

async function _mwRender(){
  const sel = document.getElementById('mwAssessSel');
  const status = document.getElementById('mwStatus');
  const aid = sel && sel.value;
  let wall = null;
  try {
    if (aid){
      wall = await api(`/api/assessments/${encodeURIComponent(aid)}/maturity-wall?state=${encodeURIComponent(_mwState)}`);
    } else {
      const fl = await api('/api/assessment-frameworks');
      const seed = (fl.frameworks || []).find(f => f.is_seed) || (fl.frameworks || [])[0];
      if (seed){ wall = await api(`/api/assessment-frameworks/${encodeURIComponent(seed.id)}`); wall.state = _mwState; }
    }
  } catch(e){ if (status) status.textContent = 'Load failed: ' + e.message; return; }
  if (!wall){ if (status) status.textContent = 'No framework available — seed missing.'; return; }
  _mwWall = wall;
  const fwEl = document.getElementById('mwFramework'); if (fwEl) fwEl.textContent = wall.name || wall.key || '—';
  _mwRenderStates(wall); _mwRenderScale(wall); _mwRenderWall(wall);
  const ov = document.getElementById('mwOverall');
  if (ov) ov.innerHTML = (wall.overall != null)
    ? `Overall ${_matBubble(Math.round(wall.overall), wall.maturity_target)} <span style="color:var(--text-faint);">· ${wall.assessed || 0} assessed</span>`
    : (aid ? '<span style="color:var(--text-faint);">Not yet scored for this state</span>'
           : '<span style="color:var(--text-faint);">Pick an assessment to populate from its findings</span>');
  if (status) status.textContent = '';
}

function _mwRenderStates(wall){
  const box = document.getElementById('mwStates'); if (!box) return;
  const states = wall.states || [];
  if (states.length && !states.some(s => s.key === _mwState)) _mwState = states[0].key;
  box.innerHTML = states.map(s =>
    `<button class="btn ${s.key === _mwState ? 'primary' : 'ghost'} btn-sm mw-state" data-state="${esc(s.key)}">${esc(s.label)}</button>`).join('');
  box.querySelectorAll('.mw-state').forEach(b => b.addEventListener('click', () => { _mwState = b.dataset.state; _mwRender(); }));
}

function _mwRenderScale(wall){
  const box = document.getElementById('mwScale'); if (!box) return;
  // Reuse the Assessments detail legend so the wall reads identically (per Chris's style pref).
  box.innerHTML = _matLegend(wall.scale, wall.maturity_target);
}

// A maturity-wall capability pill — same visual language as the Assessments detail (_capPill):
// name colored by 1–5 maturity heat, target gets a green ring; unscored = neutral skeleton.
function _mwPill(cap, target){
  const m = (cap.maturity === undefined) ? null : cap.maturity;
  let bg, txt = '#fff', border = '1px solid rgba(0,0,0,0.3)', extra = '';
  if (m !== null){
    bg = _MAT_COLORS[m] || 'var(--text-faint)';
    if (target !== null && target !== undefined && m === target) extra = 'box-shadow:0 0 0 2px rgba(63,174,74,0.5);';
  } else if (cap.state === 'n/a'){ bg = 'transparent'; txt = 'var(--text-faint)'; border = '1px dashed var(--text-faint)'; }
  else if (cap.state === 'absent'){ bg = '#8a3a32'; }
  else if (cap.state === 'partial'){ bg = '#b8860b'; }
  else { bg = 'var(--bg-raised)'; txt = 'var(--text-dim)'; border = '1px solid var(--border)'; }  // unscored skeleton
  const badge = (m !== null) ? ('m' + m) : (cap.state ? (cap.state === 'n/a' ? 'N/A' : esc(cap.state)) : '–');
  const tip = [cap.label, cap.rationale || '', cap.source && cap.source !== 'finding' ? ('· ' + cap.source) : ''].filter(Boolean).join(' — ');
  return `<div class="mw-pill" title="${esc(tip)}" data-fid="${esc(cap.finding_id || '')}" data-capid="${esc(cap.id || '')}" style="display:flex;align-items:center;gap:8px;padding:4px 10px;border-radius:12px;background:${bg};color:${txt};border:${border};${extra}margin:3px 0;font-size:11px;">`
    + `<span style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(cap.label)}</span>`
    + `<span style="margin-left:auto;font-size:9px;opacity:0.92;text-transform:uppercase;letter-spacing:0.04em;flex-shrink:0;">${badge}</span></div>`;
}

function _mwRenderWall(wall){
  const box = document.getElementById('mwWall'); if (!box) return;
  const target = wall.maturity_target;
  // Category-card grid in the Assessments-detail style. Flatten bands→categories (findings-driven
  // = one unlabeled band; framework = labelled bands shown as a category prefix).
  const cats = [];
  (wall.bands || []).forEach(b => (b.categories || []).forEach(c => cats.push({ ...c, band: b.band })));
  if (!cats.length){ box.innerHTML = '<div class="empty" style="padding:14px;">No categories to show.</div>'; return; }
  let h = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;align-items:start;">';
  cats.forEach(cat => {
    h += `<div style="background:var(--bg-deep,rgba(0,0,0,0.18));border:1px solid var(--border);border-radius:4px;padding:8px 10px;">
      <div style="font-size:11px;font-weight:700;border-bottom:1px solid var(--border);padding-bottom:4px;margin-bottom:4px;display:flex;justify-content:space-between;gap:6px;align-items:center;">
        <span>${cat.band ? `<span style="color:var(--text-faint);font-weight:400;">${esc(cat.band)} · </span>` : ''}${esc(cat.label)} <span style="color:var(--text-faint);font-weight:400;">(${(cat.capabilities || []).length})</span></span>
        ${cat.rollup != null ? `<span title="category mean maturity ${cat.rollup}" style="flex-shrink:0;">${_matBubble(Math.round(cat.rollup), target)}</span>` : ''}
      </div>
      ${(cat.capabilities || []).map(c => _mwPill(c, target)).join('')}
    </div>`;
  });
  h += '</div>';
  box.innerHTML = h;
}

document.getElementById('mwAssessSel')?.addEventListener('change', _mwRender);
document.getElementById('mwReloadBtn')?.addEventListener('click', loadMaturityWall);

async function loadAssessments() {
  const list = document.getElementById('asList');
  list.innerHTML = '<div class="empty">loading…</div>';
  let data;
  try { data = await api('/api/assessments'); }
  catch (e) { list.innerHTML = `<div style="color:var(--red);font-size:11px;padding:10px;">${esc(e.message)}</div>`; return; }
  const items = data.assessments || [];
  if (!items.length) { list.innerHTML = '<div class="empty">No assessments yet. Ingest the synthetic example to get started.</div>'; return; }
  let h = '';
  for (const a of items) {
    const sel = a.id === _asSel ? 'background:var(--bg-hover,rgba(255,255,255,0.05));' : '';
    const when = a.created_at ? new Date(a.created_at).toLocaleDateString() : '';
    h += `<div onclick="renderAssessment('${esc(a.id)}')" style="cursor:pointer;padding:9px 14px;border-bottom:1px solid var(--border);${sel}">`
      + `<div style="font-size:12px;font-weight:600;">${esc(a.handle)}</div>`
      + `<div style="font-size:10px;color:var(--text-dim);margin-top:2px;">${esc(a.assessment_type)} · ${esc(a.pillar)} · ${when}</div>`
      + `<div style="font-size:10px;margin-top:3px;"><span style="color:var(--text-faint);">${a.findings} finding${a.findings==1?'':'s'}</span>`
      + (a.gaps > 0 ? ` · <span style="color:var(--amber,gold);">${a.gaps} gap${a.gaps==1?'':'s'}</span>` : '') + `</div></div>`;
  }
  list.innerHTML = h;
}
async function ingestAssessmentFixture() {
  const btn = document.getElementById('asIngestFixtureBtn');
  const status = document.getElementById('asStatus');
  btn.disabled = true; status.textContent = 'ingesting synthetic example…';
  try {
    const r = await api('/api/assessments/ingest', { method:'POST', body: JSON.stringify({ use_fixture: true }) });
    status.textContent = `✓ ingested ${r.findings} findings (${r.mapped} mapped, ${r.gaps} gaps)`;
    toast('✓ Synthetic assessment ingested');
    await loadAssessments();
    loadAssessmentSelector();
    if (r.assessment_id) renderAssessment(r.assessment_id);
  } catch (e) { status.textContent = ''; toast('✗ ' + e.message); }
  finally { btn.disabled = false; }
}
// Ingest a real assessment from a pasted/uploaded canonical-format JSON payload.
function toggleAssessmentIngest() {
  const f = document.getElementById('asIngestForm');
  if (f) f.style.display = f.style.display === 'none' ? '' : 'none';
}
let _asIngestFile = null;   // #105: a staged binary (PDF) file for server-side extraction
async function _asPopulateIngestModels() {
  const sel = document.getElementById('asIngestModelSel');
  if (!sel) return;
  try {
    const models = await api('/api/models');               // scope-aware (project ∪ platform)
    const cur = sel.value;
    sel.innerHTML = '<option value="">— project default —</option>' +
      (models || []).filter(m => m.enabled !== false).map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join('');
    sel.value = cur;
  } catch (e) { /* keep the default option */ }
}
function _asIngestModeChanged() {
  const mode = document.getElementById('asIngestMode')?.value || 'json';
  const hint = document.getElementById('asIngestHint');
  const ta = document.getElementById('asIngestJson');
  const modelRow = document.getElementById('asIngestModelRow');
  if (modelRow) modelRow.style.display = (mode === 'model') ? 'inline-flex' : 'none';
  if (mode === 'model') {
    _asPopulateIngestModels();
    if (hint) hint.innerHTML = 'Paste the assessment <b>text/notes</b>, or upload a <b>PDF, image, or slide deck</b> (also txt/md/csv/json/yaml). The selected model extracts it into the UDLM Assessment contract. <b>Images & slide-deck PDFs</b> are read by a <b>vision model</b> (🖼 auto for images / image-only PDFs) — pick a vision-capable model (e.g. qwen2.5-vl).';
    if (ta) ta.placeholder = 'Paste the assessment artifact text — or upload a PDF / file below…';
  } else {
    if (hint) hint.innerHTML = 'Paste a <b>canonical</b> assessment JSON (or upload a file). Shape: <code style="font-size:9px;">{handle, assessment_type, pillar, findings:[{capability, category, state:present|partial|absent|n/a, maturity:1-5, evidence, notes}]}</code>';
    if (ta) ta.placeholder = '{"handle":"…","assessment_type":"automation","findings":[…]}';
  }
}
function _asLoadIngestFile(input) {
  const file = input.files && input.files[0];
  _asIngestFile = null;
  if (!file) return;
  const name = (file.name || '').toLowerCase();
  const isImage = /\.(png|jpg|jpeg|gif|webp|bmp)$/.test(name);
  // PDF or image (binary) → stage for server-side extraction; force model mode (the extractor
  // reads it). Images auto-enable vision (#113); image-only PDFs auto-fall-back to vision server-side.
  if (name.endsWith('.pdf') || isImage) {
    _asIngestFile = file;
    const ta = document.getElementById('asIngestJson');
    if (ta) ta.value = `[${isImage ? 'Image' : 'PDF'} staged: ${file.name} — extracted server-side on Ingest]`;
    const modeSel = document.getElementById('asIngestMode');
    if (modeSel && modeSel.value !== 'model') { modeSel.value = 'model'; _asIngestModeChanged(); }
    const vis = document.getElementById('asIngestVision');
    if (vis && isImage) vis.checked = true;   // images require vision
    return;
  }
  // text / structured → read into the textarea
  const reader = new FileReader();
  reader.onload = () => { const ta = document.getElementById('asIngestJson'); if (ta) ta.value = reader.result; };
  reader.readAsText(file);
}
// Multipart POST (FormData) — like api() but without a JSON content-type so the browser
// sets the multipart boundary; carries the active-project header.
async function _apiForm(url, formData) {
  const headers = {};
  if (typeof _activeProject !== 'undefined' && _activeProject) headers['X-DAV-Project'] = _activeProject;
  const res = await fetch(url, { method: 'POST', headers, body: formData, credentials: 'same-origin' });
  if (!res.ok) {
    // Surface a REAL reason — JSON detail when present, else a cleaned text/HTML body (e.g. an
    // nginx 413 page that never reached the API), always tagged with the HTTP status.
    let msg = '';
    const ct = res.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      try { const j = await res.json(); const d = j.detail; msg = (typeof d === 'string' ? d : (d ? JSON.stringify(d) : '')); } catch {}
    } else {
      try { msg = (await res.text()).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200); } catch {}
    }
    if (res.status === 413) msg = `File too large — it exceeds the server upload limit (was the deck > the limit?). ${msg}`.trim();
    if (!msg) msg = res.statusText || 'request failed';
    throw new Error(`HTTP ${res.status}: ${msg}`);
  }
  return res.json();
}
// Live "something is happening" feedback for the (potentially long) model/vision ingest:
// a ticking spinner + elapsed seconds in the status line, and a disabled Ingest button.
let _asBusyTimer = null;
function _asBusy(on, label) {
  const status = document.getElementById('asStatus');
  const btn = document.getElementById('asIngestSubmit');
  if (btn) btn.disabled = !!on;
  if (_asBusyTimer) { clearInterval(_asBusyTimer); _asBusyTimer = null; }
  if (!on) return;
  const t0 = Date.now();
  const spin = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏'];
  let i = 0;
  const tick = () => {
    const s = Math.floor((Date.now() - t0) / 1000);
    const slow = s >= 20 ? ' <span style="color:var(--text-faint);">(vision over many pages can take a minute+)</span>' : '';
    if (status) status.innerHTML = `<span style="color:var(--accent);">${spin[i = (i + 1) % spin.length]}</span> ${esc(label)} <span style="color:var(--text-faint);">· ${s}s</span>${slow}`;
  };
  tick();
  _asBusyTimer = setInterval(tick, 200);
}
function _asIngestDone(r, status) {
  _asBusy(false);
  const via = r.via === 'vision' ? `🖼 vision${r.pages ? ` · ${r.pages} page${r.pages === 1 ? '' : 's'}` : ''}`
            : (r.via === 'text' ? 'text' : '');
  if (status) status.textContent = `✓ ingested ${r.findings} findings (${r.mapped} mapped, ${r.gaps} gaps)${via ? ' · ' + via : ''}${r.model ? ' · ' + r.model : ''}`;
  toast('✓ Assessment ingested');
  toggleAssessmentIngest();
  document.getElementById('asIngestJson').value = '';
  _asIngestFile = null;
  const fi = document.getElementById('asIngestFile'); if (fi) fi.value = '';
  loadAssessments();
  loadAssessmentSelector();
  if (r.assessment_id) renderAssessment(r.assessment_id);
}
async function ingestAssessment() {
  const mode = document.getElementById('asIngestMode')?.value || 'json';
  const status = document.getElementById('asStatus');
  const modelId = parseInt(document.getElementById('asIngestModelSel')?.value || '', 10);
  // PDF / image (staged file) → multipart server-side extraction (model mode only).
  if (mode === 'model' && _asIngestFile) {
    const isVision = !!document.getElementById('asIngestVision')?.checked;
    const fname = _asIngestFile.name || 'file';
    _asBusy(true, isVision
      ? `Reading "${fname}" with vision model — rendering pages + extracting…`
      : `Extracting "${fname}" via model — text, or vision if it's a slide deck…`);
    try {
      const fd = new FormData();
      fd.append('file', _asIngestFile);
      if (modelId) fd.append('model_config_id', String(modelId));
      if (isVision) fd.append('vision', 'true');
      _asIngestDone(await _apiForm('/api/assessments/ingest-file', fd), status);
    } catch (e) { _asBusy(false); status.innerHTML = '<span style="color:var(--red);">✗ ' + esc(e.message) + '</span>'; toast('✗ ' + e.message, true); }
    return;
  }
  const raw = (document.getElementById('asIngestJson').value || '').trim();
  if (!raw) { toast('Paste or upload assessment content', true); return; }
  if (mode === 'model') _asBusy(true, 'Extracting via model…');
  else status.textContent = 'ingesting…';
  try {
    let r;
    if (mode === 'model') {
      const body = { content: raw };
      if (modelId) body.model_config_id = modelId;
      r = await api('/api/assessments/ingest-model', { method:'POST', body: JSON.stringify(body) });
    } else {
      let payload;
      try { payload = JSON.parse(raw); }
      catch (e) { toast('Invalid JSON: ' + e.message, true); status.textContent = '✗ invalid JSON'; return; }
      r = await api('/api/assessments/ingest', { method:'POST', body: JSON.stringify({ assessment: payload }) });
    }
    _asIngestDone(r, status);
  } catch (e) { _asBusy(false); status.innerHTML = '<span style="color:var(--red);">✗ ' + esc(e.message) + '</span>'; toast('✗ ' + e.message, true); }
}
async function renderAssessment(id) {
  _asSel = id;
  const box = document.getElementById('asDetail');
  box.innerHTML = '<div class="empty">loading…</div>';
  let a;
  try { a = await api('/api/assessments/' + encodeURIComponent(id)); }
  catch (e) { box.innerHTML = `<div style="color:var(--red);font-size:11px;padding:10px;">${esc(e.message)}</div>`; return; }
  loadAssessments();   // refresh list highlight
  const gs = a.gap_summary || { by_state:{}, by_normalization:{}, gaps:[] };
  const bs = gs.by_state || {}, bn = gs.by_normalization || {};
  const pill = (label, n, color) => `<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:${color};color:#000;font-size:10px;font-weight:600;margin-right:6px;">${esc(label)}: ${n||0}</span>`;
  let h = `<div style="font-size:15px;font-weight:600;">${esc(a.handle)}</div>`
    + `<div style="font-size:11px;color:var(--text-dim);margin:3px 0 10px;">${esc(a.assessment_type)} · ${esc(a.pillar)}`
    + (a.source ? ` · source ${esc(a.source)}` : '') + (a.created_by ? ` · by ${esc(a.created_by)}` : '')
    + ` · <span title="classification">${esc(a.classification||'')}</span></div>`;
  if (a.summary) h += `<div style="font-size:12px;margin-bottom:12px;">${esc(a.summary)}</div>`;
  h += `<div style="background:var(--bg-deep,rgba(0,0,0,0.2));border:1px solid var(--border);border-radius:2px;padding:10px 12px;margin-bottom:12px;">`
    + `<div class="panel-title" style="margin-bottom:6px;">Gap summary — the roadmap signal</div>`
    + `<div style="margin-bottom:6px;">${pill('present', bs.present, 'var(--green)')}${pill('partial', bs.partial, 'var(--amber,gold)')}${pill('absent', bs.absent, 'var(--red)')}${bs['n/a'] ? pill('n/a', bs['n/a'], 'var(--text-faint)') : ''}</div>`
    + `<div style="font-size:11px;color:var(--text-dim);">Normalization: ${bn.normalized||0} on taxonomy · ${bn['proposed-taxonomy-gap']||0} taxonomy-gap (back-fill) · ${bn.unmapped||0} unmapped</div>`
    + (gs.maturity ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">Maturity vs target ${gs.maturity.target}: `
        + `<b style="color:var(--green);">${gs.maturity.at_or_above_target||0}</b> at/above · `
        + `<b style="color:var(--amber,gold);">${gs.maturity.below_target||0}</b> below · `
        + `${gs.maturity.na||0} N/A</div>` + _matLegend(a.maturity_scale, a.maturity_target) : '')
    + `</div>`;
  // Group capabilities by category; each category anchors a vertical list of colored pills.
  const byCat = {};
  for (const f of (a.findings || [])) { const k = f.category || 'Uncategorized'; (byCat[k] = byCat[k] || []).push(f); }
  const cats = Object.keys(byCat).sort((x, y) => x === 'Uncategorized' ? 1 : y === 'Uncategorized' ? -1 : x.localeCompare(y));
  h += `<div class="panel-title" style="margin-bottom:6px;">Capabilities by category (${(a.findings||[]).length}) <span style="color:var(--text-faint);font-weight:400;font-size:10px;">° = taxonomy gap · hover a pill for detail</span></div>`;
  h += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;align-items:start;">`;
  for (const cat of cats) {
    h += `<div style="background:var(--bg-deep,rgba(0,0,0,0.18));border:1px solid var(--border);border-radius:4px;padding:8px 10px;">`
      + `<div style="font-size:11px;font-weight:700;border-bottom:1px solid var(--border);padding-bottom:4px;margin-bottom:4px;">${esc(cat)} <span style="color:var(--text-faint);font-weight:400;">(${byCat[cat].length})</span></div>`;
    for (const f of byCat[cat]) h += _capPill(f, a.maturity_target);
    h += `</div>`;
  }
  h += `</div>`;
  box.innerHTML = h;
}

// ── Prompts & Improvement (F8): per-project, per-stage prompt management ──────
let _pmMeta = null;          // registry meta for the selected stage
function switchPiTab(which) {
  document.querySelectorAll('.pi-tab').forEach(b => b.classList.toggle('active', b.dataset.pi === which));
  document.getElementById('promptPane').style.display = which === 'prompts' ? '' : 'none';
  document.getElementById('improvePane').style.display = which === 'improve' ? '' : 'none';
  if (which === 'prompts' && !_pmMeta) pmInit();
}
async function pmInit() {
  const sel = document.getElementById('pmStage');
  let data;
  try { data = await api('/api/prompts/stages'); }
  catch (e) { document.getElementById('pmBody').innerHTML = `<div style="color:var(--red);font-size:11px;">${esc(e.message)}</div>`; return; }
  const stages = data.stages || [];
  sel.innerHTML = stages.map(s => `<option value="${esc(s.key)}">${esc(s.label)}</option>`).join('');
  if (stages.length) pmLoadStage();
}
function _pmStatusBadge(status) {
  const map = {
    'append-live': ['live', 'var(--green)', 'Additional context is applied at runtime now.'],
    'stored-held': ['stored — held', 'var(--amber,gold)', 'Stored & previewable, but NOT yet applied at runtime (A/B required before enabling — eval-sensitive).'],
  };
  const [t, c, tip] = map[status] || [status, 'var(--text-faint)', ''];
  return `<span title="${esc(tip)}" style="display:inline-block;padding:2px 8px;border-radius:10px;background:${c};color:#000;font-size:10px;font-weight:600;">${esc(t)}</span>`;
}
// #93 promotion: flip the Evaluation (stage-2) prompt live / held.
async function _pmSetApplied(on) {
  if (on && !confirm('Apply this Evaluation prompt to ALL future runs?\n\nThis changes eval behavior for every subsequent run. Validate it with an A/B first (Improve → New A/B → evaluation prompt).')) {
    pmLoadStage(); return;   // re-render to revert the checkbox
  }
  try {
    await api('/api/prompts/stage2/applied', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ applied: on }) });
    toast(on ? 'Evaluation prompt is now LIVE on runs' : 'Evaluation prompt reverted to held (preview only)');
    pmLoadStage();
  } catch (e) { toast(e.message, true); pmLoadStage(); }
}
async function pmLoadStage() {
  const stage = document.getElementById('pmStage').value;
  const body = document.getElementById('pmBody');
  document.getElementById('pmSaveStatus').textContent = '';
  body.innerHTML = '<div class="empty">loading…</div>';
  let d;
  try { d = await api('/api/prompts/project/' + encodeURIComponent(stage)); }
  catch (e) { body.innerHTML = `<div style="color:var(--red);font-size:11px;">${esc(e.message)}</div>`; return; }
  _pmMeta = d.meta || { sections: [], append: {} };
  // #125: each prompt pairs 1:1 with a model role — show which model runs it (same verbiage).
  const _roleModel = _pmMeta.role ? ` · runs on <span style="color:var(--text-dim);">${esc(_defaultModelName(_pmMeta.role))}</span>` : '';
  // #93 promotion: the engine Evaluation prompt is stored-held by default; an "Apply to live runs"
  // toggle promotes it (so NORMAL runs inject it). Only for the engine stage; promote after an A/B.
  const _isEngine = (_pmMeta.surface === 'engine');
  const _applyToggle = _isEngine
    ? `<label title="Promote this Evaluation prompt to LIVE — normal runs will inject it. Validate with an A/B (Improve → New A/B → evaluation prompt) first." style="display:inline-flex;align-items:center;gap:4px;margin-left:10px;font-size:10px;cursor:pointer;${d.applied ? 'color:var(--green);' : 'color:var(--text-faint);'}"><input type="checkbox" id="pmApplyLive" ${d.applied ? 'checked' : ''} onchange="_pmSetApplied(this.checked)" style="width:auto;height:auto;accent-color:var(--green);"> ${d.applied ? '● LIVE on runs' : 'Apply to live runs'}</label>`
    : '';
  document.getElementById('pmStageStatus').innerHTML = _pmStatusBadge(d.applied && _isEngine ? 'append-live' : _pmMeta.status)
    + `<span style="color:var(--text-faint);">${_roleModel}</span>` + _applyToggle;
  const ov = d.section_overrides || {};
  const appendLabel = (_pmMeta.append && _pmMeta.append.label) || 'Additional context';
  const appendLive = !!(_pmMeta.append && _pmMeta.append.live);
  let h = `<div style="font-size:11px;color:var(--text-dim);margin-bottom:14px;">${esc(_pmMeta.description || '')}</div>`;
  h += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start;">';
  // Left column — editors
  h += '<div>';
  h += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;"><span style="font-weight:600;font-size:12px;">${esc(appendLabel)}`
     + (appendLive ? '' : ` <span style="color:var(--amber,gold);font-weight:400;font-size:10px;">(stored; applied once this stage is enabled)</span>`) + `</span>`
     + `<button class="btn ghost btn-sm" style="margin-left:auto;" onclick="pmAssist('append','pmAppend')" title="AI-assisted draft / refine from a described intent">✨ Assist</button></div>`;
  h += `<textarea id="pmAppend" oninput="pmRefreshPreview()" style="width:100%;min-height:90px;font-size:12px;font-family:var(--mono,monospace);" placeholder="Project-specific grounding context, conventions, glossary, constraints…">${esc(d.content || '')}</textarea>`;
  for (const sec of (_pmMeta.sections || [])) {
    const secId = 'pmSec_' + String(sec.name).replace(/[^a-zA-Z0-9]/g, '_');
    h += `<div style="margin-top:14px;display:flex;align-items:center;gap:8px;"><span style="font-weight:600;font-size:12px;">Override section: ${esc(sec.label)}</span>`
       + `<button class="btn ghost btn-sm" style="margin-left:auto;" onclick="pmAssist('section:${esc(sec.name)}','${secId}')" title="AI-assisted draft / refine from a described intent">✨ Assist</button></div>`;
    h += `<div style="font-size:10px;color:var(--text-faint);margin-bottom:4px;">${esc(sec.description || '')}</div>`;
    h += `<details style="margin-bottom:4px;"><summary style="font-size:10px;color:var(--text-dim);cursor:pointer;">show base text</summary><pre style="white-space:pre-wrap;font-size:10px;color:var(--text-faint);background:var(--bg-deep,rgba(0,0,0,0.2));padding:8px;border-radius:2px;max-height:160px;overflow:auto;">${esc(sec.base || '')}</pre></details>`;
    h += `<textarea id="${secId}" data-sec="${esc(sec.name)}" oninput="pmRefreshPreview()" style="width:100%;min-height:80px;font-size:12px;font-family:var(--mono,monospace);" placeholder="Leave blank to use the base section unchanged.">${esc(ov[sec.name] || '')}</textarea>`;
  }
  h += `<div style="margin-top:12px;"><button class="btn primary btn-sm" onclick="pmSave()">Save</button></div>`;
  h += '</div>';
  // Right column — live preview
  h += '<div><div style="font-weight:600;font-size:12px;margin-bottom:4px;">Assembled prompt (preview)</div>'
     + '<pre id="pmPreview" style="white-space:pre-wrap;font-size:11px;font-family:var(--mono,monospace);background:var(--bg-deep,rgba(0,0,0,0.2));border:1px solid var(--border);padding:10px;border-radius:2px;max-height:60vh;overflow:auto;"></pre></div>';
  h += '</div>';
  body.innerHTML = h;
  pmRefreshPreview();
}
function _pmGather() {
  const append = (document.getElementById('pmAppend')?.value || '');
  const overrides = {};
  document.querySelectorAll('#pmBody textarea[data-sec]').forEach(t => {
    if ((t.value || '').trim() !== '') overrides[t.dataset.sec] = t.value;
  });
  return { append, overrides };
}
function pmRefreshPreview() {
  if (!_pmMeta) return;
  const { append, overrides } = _pmGather();
  const parts = [];
  for (const sec of (_pmMeta.sections || [])) {
    const ov = overrides[sec.name];
    const text = (ov && ov.trim() !== '') ? ov : (sec.base || '');
    if (text) parts.push(text);
  }
  if (append.trim() !== '') parts.push('## Project context & instructions (set by the architect — honor these)\n' + append.trim());
  const pre = document.getElementById('pmPreview');
  if (pre) pre.textContent = parts.join('\n\n').trim() || '(empty)';
}
async function pmSave() {
  const stage = document.getElementById('pmStage').value;
  const { append, overrides } = _pmGather();
  const st = document.getElementById('pmSaveStatus');
  st.textContent = 'saving…';
  try {
    await api('/api/stage-context/' + encodeURIComponent(stage), {
      method: 'PUT',
      body: JSON.stringify({ content: append, section_overrides: overrides }),
    });
    st.textContent = '✓ saved';
    toast('✓ Prompt customization saved');
  } catch (e) { st.textContent = ''; toast('✗ ' + e.message); }
}
// Prompt assistant — describe the intent, AI drafts/refines the text into the target
// textarea (human reviews + edits + saves; nothing is auto-applied). Server-side, reuses
// the project's authoring model. arc: assistant drafts → editor refines → static A/B validates.
async function pmAssist(target, taId) {
  const ta = document.getElementById(taId);
  if (!ta) return;
  const intent = prompt('✨ Describe the intent — what should this prompt text do?');
  if (!intent || !intent.trim()) return;
  const stage = document.getElementById('pmStage').value;
  const old = ta.value;
  ta.disabled = true; ta.value = '✨ generating…';
  try {
    const r = await api('/api/prompts/assist', {
      method: 'POST',
      body: JSON.stringify({ stage, target, intent, current: old }),
    });
    ta.value = (r.suggestion && r.suggestion.trim()) ? r.suggestion : old;
    pmRefreshPreview();
    toast('✨ Draft generated — review & edit before saving');
  } catch (e) { ta.value = old; toast('✗ ' + e.message); }
  finally { ta.disabled = false; }
}

// ── Capability Map (F5): bidirectional UC ↔ capability matrix ────────────────
let _cmSel = null;
async function loadCapMap() {
  // 3b: Cap Map is scoped by the shared masthead Scoping Set (run-agnostic, latest eval per UC).
  // Both the old run picker and the in-panel Set picker are retired — scope lives in the masthead.
  const runSel = document.getElementById('cmRunSel'); if (runSel) runSel.style.display = 'none';
  const setWrap = document.getElementById('cmScopeWrap'); if (setWrap) setWrap.style.display = 'none';
  try { if (!allSets || !allSets.length) await loadSets(); } catch (_) {}
  populateScopeSel();
  renderCapMap();   // auto-load for the current masthead scope (scope changes re-render via setScope)
}

async function renderCapMap() {
  // 3b: scope by the shared masthead Scoping Set (no run_id → latest eval per UC, may span runs).
  const setId = _activeScope;
  const box = document.getElementById('cmMatrix');
  const status = document.getElementById('cmStatus');
  _cmSel = null;
  box.innerHTML = '<div class="empty">loading…</div>'; status.textContent = '';
  let data;
  try {
    data = await api('/api/analysis/uc-capability-map' + (setId ? `?set_id=${encodeURIComponent(setId)}` : ''));
  } catch (e) {
    box.innerHTML = `<div style="color:var(--red);font-size:11px;">${esc(e.message)}</div>`
      + `<div style="font-size:10px;color:var(--text-faint);margin-top:4px;">If a UC predates capability tracking, re-ingest it from the Results tab.</div>`;
    return;
  }
  const ucs = data.ucs || [], caps = data.capabilities || [], edges = data.edges || [];
  if (!ucs.length || !caps.length) {
    box.innerHTML = `<div class="empty">No UC ↔ capability edges in this scope${setId ? ' (Set)' : ''}.</div>`;
    return;
  }
  const capIdx = new Map(caps.map((c, i) => [c.id, i]));
  const ucIdx = new Map(ucs.map((u, i) => [u.uuid, i]));
  const cell = new Map();
  for (const e of edges) {
    const ui = ucIdx.get(e.uc), ci = capIdx.get(e.cap);
    if (ui === undefined || ci === undefined) continue;
    cell.set(ui + ':' + ci, e.weight);
  }
  let h = '<table class="capmap"><thead><tr>';
  h += `<th class="cm-corner" title="${ucs.length} UCs × ${caps.length} capabilities">UC ＼ Cap</th>`;
  caps.forEach((c, ci) => {
    const star = c.foundational ? `<span class="cm-found" title="foundational — ${c.transitive_dependents} depend on it">★</span> ` : '';
    // #132 disposition lens: a thin colored underline on the column header — the R4 verdict
    // at a glance without crowding the dense matrix. Subdomain rides the hover title.
    const dm = DISPOSITIONS[c.disposition];
    const dispBorder = dm ? `border-bottom:2px solid ${dm.color};` : '';
    const dispTip = dm ? ` · ${dm.label} (≈ ${dm.time})` : '';
    const subTip = c.subdomain && CLASSIFICATIONS[c.subdomain] ? ` · ${CLASSIFICATIONS[c.subdomain].label}` : '';
    h += `<th class="cm-caphead" data-ci="${ci}" onclick="cmHighlight('cap',${ci})" style="${dispBorder}" `
      + `title="${esc(c.name)} — demanded by ${c.demand} UC${c.demand === 1 ? '' : 's'}`
      + `${c.foundational ? ` · foundational (${c.transitive_dependents} dependents)` : ''}${subTip}${dispTip}`
      + `${c.usage ? `\n\n${esc(c.usage)}` : ''}">`
      + `<div>${star}${esc(c.name)} (${c.demand})</div></th>`;
  });
  h += '</tr></thead><tbody>';
  ucs.forEach((u, ui) => {
    h += `<tr data-ui="${ui}"><td class="cm-uc" data-ui="${ui}" onclick="cmHighlight('uc',${ui})" title="${esc(u.label)}">${esc(u.label)}</td>`;
    caps.forEach((c, ci) => {
      const w = cell.get(ui + ':' + ci);
      h += w
        ? `<td class="cm-cell on" data-ui="${ui}" data-ci="${ci}" title="${esc(u.label)} → ${esc(c.name)} (${w})"></td>`
        : `<td class="cm-cell" data-ui="${ui}" data-ci="${ci}"></td>`;
    });
    h += '</tr>';
  });
  h += '</tbody></table>';
  box.innerHTML = h;
  status.textContent = `${ucs.length} UCs · ${caps.length} capabilities · ${edges.length} edges`;
}

function cmHighlight(kind, idx) {
  const key = kind + idx;
  document.querySelectorAll('#cmMatrix .hl').forEach(el => el.classList.remove('hl'));
  if (_cmSel === key) { _cmSel = null; return; }   // toggle off
  _cmSel = key;
  if (kind === 'cap') {
    document.querySelectorAll(`#cmMatrix [data-ci="${idx}"]`).forEach(el => el.classList.add('hl'));
    document.querySelectorAll(`#cmMatrix td.cm-cell.on[data-ci="${idx}"]`).forEach(td => {
      const lab = document.querySelector(`#cmMatrix .cm-uc[data-ui="${td.getAttribute('data-ui')}"]`);
      if (lab) lab.classList.add('hl');
    });
  } else {
    document.querySelectorAll(`#cmMatrix tr[data-ui="${idx}"] > *`).forEach(el => el.classList.add('hl'));
    document.querySelectorAll(`#cmMatrix td.cm-cell.on[data-ui="${idx}"]`).forEach(td => {
      const head = document.querySelector(`#cmMatrix .cm-caphead[data-ci="${td.getAttribute('data-ci')}"]`);
      if (head) head.classList.add('hl');
    });
  }
}

// ── Capability demand density (DCM feature #2) ──────────────────────────────
// Run-scoped: aggregates which capabilities the most UCs in a run demand.
// Reads /api/analysis/capability-density (populated at ingest); no model needed.
async function loadCapabilityMap() {
  // 3b: scope by the Scoping Set (no run_id → latest eval per UC, may span runs).
  const el = document.getElementById('rpCapMap');
  if (!el) return;
  el.innerHTML = '<div style="font-size:11px;color:var(--text-faint);">Loading…</div>';
  let data;
  try {
    data = await api('/api/analysis/capability-density' + scopeQuery());
  } catch (e) {
    el.innerHTML = `<div style="font-size:11px;color:var(--red);">${esc(e.message)}</div>`
      + `<div style="font-size:10px;color:var(--text-faint);margin-top:4px;">If a UC predates capability tracking, re-ingest it from the Results tab, then retry.</div>`;
    return;
  }
  renderCapabilityMap(data);
}

function renderCapabilityMap(data) {
  const el = document.getElementById('rpCapMap');
  if (!el) return;
  const caps = data.capabilities || [];
  const total = data.total_ucs || 0;
  if (!caps.length) {
    el.innerHTML = `<div style="font-size:11px;color:var(--text-faint);">No capabilities recorded for this run`
      + (total ? ` (${total} UC${total===1?'':'s'} analyzed).` : `.`)
      + ` Re-ingest the run if it predates capability tracking.</div>`;
    return;
  }
  // uuid → handle labels from the current run summary, when it's the loaded run.
  const handleByUuid = {};
  if (activeRunSummary && activeRunSummary.run_id === data.run_id) {
    (activeRunSummary.ucs || []).forEach(u => { handleByUuid[u.uc_uuid] = u.uc_handle || u.uc_uuid; });
  }
  const maxCount = caps[0].uc_count || 1;   // sorted desc → first is the max
  let html = `<div style="font-size:11px;color:var(--text-faint);margin-bottom:10px;">`
    + `${caps.length} capabilit${caps.length===1?'y':'ies'} demanded across ${total} analyzed UC${total===1?'':'s'} — ranked by how many UCs need each. Click a row to see which.</div>`;
  caps.forEach((c, i) => {
    const pct = total ? Math.round(c.demand_ratio * 100) : 0;
    const barPct = Math.max(2, Math.round((c.uc_count / maxCount) * 100));
    const ns = (c.namespaces || []).map(n => `<span class="tag">${esc(n)}</span>`).join(' ');
    const conf = (c.avg_confidence != null) ? ` · avg conf ${esc(String(c.avg_confidence))}` : '';
    const ucBtns = (c.uc_uuids || []).map(u =>
      `<button class="btn ghost btn-sm capmap-uc" data-uuid="${esc(u)}" style="margin:2px 4px 2px 0;font-family:var(--mono,monospace);font-size:10px;" title="Open ${esc(u)}">${esc(handleByUuid[u] || u)}</button>`
    ).join('');
    html += `
      <div class="capmap-row" data-cap="${i}" style="margin-bottom:9px;cursor:pointer;">
        <div style="display:flex;align-items:baseline;gap:8px;justify-content:space-between;">
          <span style="font-size:12px;color:var(--text);min-width:0;">${esc(c.name || c.capability_id)}${c.name?` <span style="font-family:var(--mono,monospace);font-size:9px;color:var(--text-faint);">${esc(c.capability_id)}</span>`:''}</span>
          <span style="display:flex;gap:6px;align-items:center;white-space:nowrap;flex-shrink:0;">
            ${_classBadge(c.subdomain)}${_dispBadge(c.disposition)}
            ${c.distinct_customers ? `<span style="font-size:10px;color:var(--text-dim);" title="distinct customers demanding this capability — the capability-level funding signal">👥 ${c.distinct_customers}</span>` : ''}
            <span style="font-size:11px;color:var(--text-faint);">${c.uc_count}/${total} UCs · ${pct}%${conf}</span>
          </span>
        </div>
        ${c.usage?`<div style="font-size:10px;color:var(--text-dim);margin-top:2px;">${esc(c.usage)}</div>`:''}
        <div style="height:6px;background:var(--bg-raised);border-radius:3px;margin-top:3px;overflow:hidden;">
          <div style="height:100%;width:${barPct}%;background:var(--accent);"></div>
        </div>
        ${ns ? `<div style="margin-top:3px;">${ns}</div>` : ''}
        <div class="capmap-ucs" data-cap-ucs="${i}" style="display:none;margin-top:6px;padding-left:8px;border-left:2px solid var(--border);">${ucBtns}</div>
      </div>`;
  });
  el.innerHTML = html;
  // Row click toggles the demanding-UC drill-in; UC buttons jump to that UC.
  el.querySelectorAll('.capmap-row').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('.capmap-uc')) return;
      const sub = el.querySelector(`.capmap-ucs[data-cap-ucs="${row.dataset.cap}"]`);
      if (sub) sub.style.display = sub.style.display === 'none' ? 'block' : 'none';
    });
  });
  el.querySelectorAll('.capmap-uc').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      switchView('usecases');
      selectUC(btn.dataset.uuid);
    });
  });
}

let _engCapMode = 'density';   // which Engineering cap-map view to (re)load on entry + scope change
document.getElementById('rpCapMapBtn')?.addEventListener('click', () => { _engCapMode = 'density'; loadCapabilityMap(); });

// ── Foundational capability detection (DCM feature #3) ──────────────────────
// Ranks capabilities by how many others transitively depend on them. Shares the
// rpCapMap container with the demand view; reads /api/analysis/foundational-capabilities.
async function loadFoundational() {
  // 3b: scope by the Scoping Set (no run_id → latest eval per UC, may span runs).
  const el = document.getElementById('rpCapMap');
  if (!el) return;
  el.innerHTML = '<div style="font-size:11px;color:var(--text-faint);">Loading…</div>';
  let data;
  try {
    data = await api('/api/analysis/foundational-capabilities' + scopeQuery());
  } catch (e) {
    el.innerHTML = `<div style="font-size:11px;color:var(--red);">${esc(e.message)}</div>`;
    return;
  }
  renderFoundational(data);
}

function renderFoundational(data) {
  const el = document.getElementById('rpCapMap');
  if (!el) return;
  const caps = (data.capabilities || []).filter(c => c.transitive_dependents > 0);
  if (!data.edge_count) {
    el.innerHTML = `<div style="font-size:11px;color:var(--text-faint);">No capability dependencies recorded for this run.`
      + ` Foundational detection needs analyses that emit capability <code>depends_on</code> edges`
      + ` — tune the engine prompt to elicit them, then re-ingest the run.</div>`;
    return;
  }
  if (!caps.length) {
    el.innerHTML = `<div style="font-size:11px;color:var(--text-faint);">${data.edge_count} dependency edge${data.edge_count===1?'':'s'} found, but no capability is depended on by another (no foundations to rank).</div>`;
    return;
  }
  const maxTd = caps[0].transitive_dependents || 1;   // sorted desc → first is max
  let html = `<div style="font-size:11px;color:var(--text-faint);margin-bottom:10px;">`
    + `${caps.length} foundational capabilit${caps.length===1?'y':'ies'} across ${data.edge_count} dependency edge${data.edge_count===1?'':'s'} — ranked by how many capabilities transitively depend on each. `
    + `<span title="transitive dependents ÷ direct UC demand — high means many capabilities rest on it but few UCs ask for it directly">Leverage</span> flags the boring-but-foundational ones.</div>`;
  caps.forEach((c, i) => {
    const barPct = Math.max(2, Math.round((c.transitive_dependents / maxTd) * 100));
    const demand = (c.demand_uc_count != null) ? `${c.demand_uc_count} UC${c.demand_uc_count===1?'':'s'} demand` : 'demand n/a';
    const lev = (c.leverage != null && c.leverage >= 1) ? `<span title="Leverage: ${esc(String(c.leverage))} (transitive dependents ÷ demand)" style="font-size:8px;text-transform:uppercase;letter-spacing:0.08em;color:var(--accent);border:1px solid var(--accent);padding:0 4px;border-radius:2px;flex-shrink:0;">lev ${esc(String(c.leverage))}</span>` : '';
    const deps = (c.depends_on || []).map(d => `<span class="tag">${esc(d)}</span>`).join(' ');
    html += `
      <div style="margin-bottom:9px;">
        <div style="display:flex;align-items:baseline;gap:8px;justify-content:space-between;">
          <span style="font-size:12px;color:var(--text);display:flex;align-items:center;gap:6px;">${esc(c.name || c.capability_id)} ${lev}</span>
          <span style="font-size:11px;color:var(--text-faint);white-space:nowrap;">${c.transitive_dependents} depend (${c.direct_dependents} direct) · ${esc(demand)}</span>
        </div>
        ${c.usage?`<div style="font-size:10px;color:var(--text-dim);margin-top:2px;">${esc(c.usage)}</div>`:''}
        <div style="height:6px;background:var(--bg-raised);border-radius:3px;margin-top:3px;overflow:hidden;">
          <div style="height:100%;width:${barPct}%;background:var(--accent);"></div>
        </div>
        ${deps ? `<div style="margin-top:3px;font-size:10px;color:var(--text-faint);">requires: ${deps}</div>` : ''}
      </div>`;
  });
  el.innerHTML = html;
}

document.getElementById('rpFoundBtn')?.addEventListener('click', () => { _engCapMode = 'foundational'; loadFoundational(); });
// Auto-load the Engineering cap map for the current scope (density by default; honors the last mode).
function _loadEngCapMap() { if (_engCapMode === 'foundational') loadFoundational(); else loadCapabilityMap(); }

// Engineering Roadmap tab (Track 2) — capability views scoped by the shared masthead
// Scoping Set (run-agnostic, latest eval per UC). The local run/Set picker is retired.
function loadEngineeringTab() {
  const sel = document.getElementById('engScopeRow');
  if (sel) sel.style.display = 'none';   // scope lives in the masthead now
  if (!(allSets || []).length) { try { loadSets(); } catch {} }
  populateScopeSel();
  try { _loadRoadmapProjection(); } catch (_) {}   // #141 synthesized roadmap (primary)
  try { _loadEngCapMap(); } catch (_) {}   // auto-load for the current scope on entry
}

// ── Roadmap projection (#141) — synthesized engineering roadmap from the gap analysis ──
let _roadmapData = null;
const _RM_SEVCOLOR = { critical: '#c02828', major: '#c8861a', moderate: '#5b8a5b', advisory: '#5a7184', minor: '#556' };
async function _loadRoadmapProjection() {
  const box = document.getElementById('rpRoadmap');
  if (!box) return;
  const gb = document.getElementById('rpRoadmapGroupBy');
  box.innerHTML = '<div style="font-size:11px;color:var(--text-faint);">Synthesizing roadmap from the gap analysis…</div>';
  try {
    // #239 / TODO1: the synthesized roadmap follows the masthead Scope (set_id), like every other
    // Roadmaps surface — not just group_by. The backend resolves the scope to its UCs' gaps.
    const params = [];
    if (gb && gb.value) params.push('group_by=' + encodeURIComponent(gb.value));
    if (typeof _activeScope !== 'undefined' && _activeScope) params.push('set_id=' + encodeURIComponent(_activeScope));
    const q = params.length ? ('?' + params.join('&')) : '';
    const d = await api('/api/analysis/roadmap' + q);
    _roadmapData = d;
    const sc = d.severity_counts || {};
    const meta = document.getElementById('rpRoadmapMeta');
    if (meta) meta.textContent = `${d.total_gaps} gaps · ${d.cluster_count} clusters · ${sc.critical || 0} critical`;
    if (!d.total_gaps) { box.innerHTML = '<div class="empty" style="font-size:11px;">No gaps in scope yet — run an ingestion to populate the roadmap.</div>'; return; }
    const chip = (s, n) => n ? `<span style="font-size:9px;padding:0 5px;border-radius:8px;margin-left:3px;background:${_RM_SEVCOLOR[s] || '#666'};color:#fff;">${n} ${s}</span>` : '';
    let h = '';
    if ((d.critical_gaps || []).length) {
      h += `<div style="border:1px solid var(--red);border-radius:6px;padding:8px 10px;margin-bottom:10px;background:rgba(200,40,40,.07);">
        <div style="font-size:11px;font-weight:600;color:var(--red);margin-bottom:4px;">⚠ ${d.critical_gaps.length} critical gap${d.critical_gaps.length === 1 ? '' : 's'} — decide first</div>
        ${d.critical_gaps.map(g => `<div style="font-size:11px;margin:2px 0;">${esc(g.title)} <span style="color:var(--text-faint);">— ${esc(g.uc_handle || '')}</span></div>`).join('')}
      </div>`;
    }
    for (const t of (d.tiers || [])) {
      h += `<div style="margin:12px 0 5px;font-size:11px;font-weight:600;color:var(--text-dim);">Tier ${t.tier} — ${esc(t.label)}</div>`;
      for (const c of (t.clusters || [])) {
        const cs = c.severity_counts || {};
        h += `<div style="border:1px solid var(--border);border-radius:6px;padding:7px 10px;margin-bottom:5px;">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <strong style="font-size:12px;">${esc(c.name)}</strong>
            ${c.foundational ? `<span title="foundational — ${c.transitive_dependents} capabilities depend on it" style="font-size:9px;color:var(--blue);">⚑ foundational</span>` : ''}
            ${c.disposition ? `<span style="font-size:9px;color:var(--text-faint);">${esc(c.disposition)}</span>` : ''}
            <span style="flex:1;"></span>
            <span style="font-size:10px;color:var(--text-faint);">${c.gap_count} gap${c.gap_count === 1 ? '' : 's'} · demand ${c.demand}</span>
            ${chip('critical', cs.critical)}${chip('major', cs.major)}${chip('moderate', cs.moderate)}
          </div>
          <div style="font-size:10px;color:var(--text-dim);margin-top:3px;">${(c.gaps || []).slice(0, 5).map(g => esc(g.title) + ` <span style="color:var(--text-faint);">[${g.severity}]</span>`).join(' · ')}${(c.gaps || []).length > 5 ? ` <span style="color:var(--text-faint);">+${c.gaps.length - 5} more</span>` : ''}</div>
        </div>`;
      }
    }
    if (d.unmapped_gap_count) h += `<div style="font-size:10px;color:var(--text-faint);margin-top:6px;">${d.unmapped_gap_count} gap(s) not mapped to a capability in this grouping.</div>`;
    box.innerHTML = h;
  } catch (e) {
    box.innerHTML = `<div class="empty" style="color:var(--red);font-size:11px;">${esc(e.message)}</div>`;
  }
}
function _roadmapMarkdown(d) {
  if (!d) return '';
  const sc = d.severity_counts || {};
  const L = ['# Engineering Roadmap (DAV gap synthesis)', '',
    `_${d.total_gaps} gaps · grouped by ${d.group_by} · ${d.cluster_count} clusters._`, '',
    'Severity: ' + ['critical', 'major', 'moderate', 'minor', 'advisory'].map(s => `**${sc[s] || 0} ${s}**`).join(' · '), ''];
  if ((d.critical_gaps || []).length) {
    L.push(`## Critical gaps (${d.critical_gaps.length}) — decide first`);
    d.critical_gaps.forEach(g => L.push(`1. **${g.title}** — _${g.uc_handle || ''}_`));
    L.push('');
  }
  (d.tiers || []).forEach(t => {
    L.push(`## Tier ${t.tier} — ${t.label}`);
    (t.clusters || []).forEach(c => {
      const cs = c.severity_counts || {};
      const sev = ['critical', 'major', 'moderate', 'minor', 'advisory'].filter(s => cs[s]).map(s => `${cs[s]} ${s}`).join(', ');
      L.push(`### ${c.name}  (${c.gap_count} gaps · demand ${c.demand}${c.foundational ? ' · foundational' : ''})`);
      if (sev) L.push(`_${sev}_`);
      (c.gaps || []).slice(0, 8).forEach(g => L.push(`- ${g.title} _[${g.severity}]_ — ${g.uc_handle || ''}`));
      if ((c.gaps || []).length > 8) L.push(`- …+${c.gaps.length - 8} more`);
      L.push('');
    });
  });
  return L.join('\n');
}
function _downloadRoadmapMd() {
  const md = _roadmapMarkdown(_roadmapData);
  if (!md) { try { toast('Nothing to export yet', true); } catch {} return; }
  const blob = new Blob([md], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'engineering-roadmap.md'; a.click();
  URL.revokeObjectURL(a.href);
}
document.getElementById('rpRoadmapExport')?.addEventListener('click', _downloadRoadmapMd);
document.getElementById('rpRoadmapRefresh')?.addEventListener('click', _loadRoadmapProjection);
document.getElementById('rpRoadmapGroupBy')?.addEventListener('change', _loadRoadmapProjection);

// ── Customers / Projects domain (customer-demand epic, Phase-2a) ─────────────
let _customersCache = [];
let _custSelId = null;
async function loadCustomers() {
  const box = document.getElementById('custList');
  if (!box) return;
  try {
    const r = await api('/api/customers');
    _customersCache = r.customers || [];
  } catch (e) { box.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`; return; }
  if (!_customersCache.length) { box.innerHTML = '<div class="empty">No customers yet. Click <strong>+ New</strong>.</div>'; }
  else box.innerHTML = _customersCache.map(c => `
    <div class="list-item${_custSelId === c.id ? ' active' : ''}" data-cid="${c.id}" style="cursor:pointer;padding:8px 10px;">
      <div class="li-main">
        <div class="li-title">${esc(c.name)}${c.is_universal ? ' <span class="tag" title="reserved internal/non-customer sentinel">universal</span>' : ''}${c.is_exclusive ? ' <span style="font-size:8px;background:var(--red);color:#fff;border-radius:2px;padding:0 4px;" title="sealed — explicit grant required">🔒 exclusive</span>' : ''}</div>
        <div class="li-sub" style="font-size:10px;color:var(--text-faint);">${c.project_count} project${c.project_count === 1 ? '' : 's'} · 👥 ${c.uc_count} UC${c.uc_count === 1 ? '' : 's'} · ${c.request_count} request${c.request_count === 1 ? '' : 's'}</div>
      </div>
    </div>`).join('');
  box.querySelectorAll('[data-cid]').forEach(el => el.addEventListener('click', () => selectCustomer(+el.dataset.cid)));
  if (_custSelId && _customersCache.some(c => c.id === _custSelId)) selectCustomer(_custSelId);
}

// ── (customer × project) association matrix (#130 2b-ii) — reuses the Cap-Map grid ──
let _custView = 'list';
function _setCustView(mode) {
  _custView = mode;
  document.getElementById('custViewListBtn')?.classList.toggle('active', mode === 'list');
  document.getElementById('custViewMatrixBtn')?.classList.toggle('active', mode === 'matrix');
  document.getElementById('custViewAccessBtn')?.classList.toggle('active', mode === 'access');
  const lv = document.getElementById('custListView'), mv = document.getElementById('custMatrixView'), av = document.getElementById('custAccessView');
  if (lv) lv.style.display = mode === 'list' ? 'flex' : 'none';
  if (mv) mv.style.display = mode === 'matrix' ? '' : 'none';
  if (av) av.style.display = mode === 'access' ? '' : 'none';
  const hint = document.getElementById('custViewHint');
  if (hint) hint.textContent = mode === 'access' ? 'who can access which customer / project → role' : 'customer × project associations';
  if (mode === 'matrix') renderCustProjMatrix();
  // #134: the access-administration matrix (subject × scope → role) — same grant matrix as
  // Config → Users & roles, surfaced here where you manage customers/projects.
  else if (mode === 'access') _renderBindingsMatrix('custAccessView');
}
async function renderCustProjMatrix() {
  const box = document.getElementById('custMatrixView');
  if (!box) return;
  box.innerHTML = '<div class="empty">loading…</div>';
  let customers = [], projects = [], pairs = [];
  try {
    customers = (await api('/api/customers')).customers || [];
    projects = (await api('/api/projects')).projects || [];
    pairs = (await api('/api/customer-projects')).pairs || [];
  } catch (e) { box.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`; return; }
  if (!customers.length || !projects.length) { box.innerHTML = '<div class="empty">Need at least one customer and one project to map.</div>'; return; }
  const assoc = new Set(pairs.map(p => p.customer_id + ':' + p.project_id));
  let h = `<div style="font-size:11px;color:var(--text-faint);margin-bottom:8px;">Click a cell to associate / dissociate · rows = customers, cols = projects · 🔒 = exclusive (sealed).</div>`;
  h += '<table class="capmap"><thead><tr><th class="cm-corner" title="customers × projects">Customer ＼ Project</th>';
  projects.forEach(p => { h += `<th class="cm-caphead" title="${esc(p.name)}"><div>${esc(p.name)}${p.is_exclusive ? ' 🔒' : ''}</div></th>`; });
  h += '</tr></thead><tbody>';
  customers.forEach(c => {
    h += `<tr><td class="cm-uc" title="${esc(c.name)}">${esc(c.name)}${c.is_exclusive ? ' 🔒' : ''}${c.is_universal ? ' <span style="font-size:8px;color:var(--text-faint);">internal</span>' : ''}</td>`;
    projects.forEach(p => {
      const on = assoc.has(c.id + ':' + p.id);
      h += `<td class="cm-cell custcell${on ? ' on' : ''}" data-cid="${c.id}" data-pid="${p.id}" data-on="${on ? 1 : 0}" title="${esc(c.name)} × ${esc(p.name)} — ${on ? 'associated (click to remove)' : 'click to associate'}" style="cursor:pointer;"></td>`;
    });
    h += '</tr>';
  });
  h += '</tbody></table>';
  box.innerHTML = h;
  box.querySelectorAll('.custcell').forEach(td => td.addEventListener('click', async () => {
    const cid = +td.dataset.cid, pid = +td.dataset.pid, on = td.dataset.on === '1';
    try {
      if (on) await api(`/api/customers/${cid}/projects/${pid}`, { method: 'DELETE' });
      else await api(`/api/customers/${cid}/projects`, { method: 'POST', body: JSON.stringify({ project_id: pid }) });
      renderCustProjMatrix();
    } catch (e) { toast(e.message, true); }
  }));
}

async function selectCustomer(cid) {
  _custSelId = cid;
  document.querySelectorAll('#custList [data-cid]').forEach(el =>
    el.classList.toggle('active', +el.dataset.cid === cid));
  const c = _customersCache.find(x => x.id === cid);
  const el = document.getElementById('custDetail');
  if (!c || !el) return;
  let assoc = { projects: [] };
  try { assoc = await api(`/api/customers/${cid}/projects`); } catch (_) {}
  el.innerHTML = `
    <div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:12px;">
      <div style="flex:1;">
        <div style="font-size:18px;font-weight:600;">${esc(c.name)}${c.is_universal ? ' <span class="tag">universal</span>' : ''}</div>
        <div style="font-family:var(--mono,monospace);font-size:11px;color:var(--text-faint);">${esc(c.slug)}</div>
        ${c.description ? `<div style="font-size:12px;color:var(--text-dim);margin-top:4px;">${esc(c.description)}</div>` : ''}
      </div>
      ${c.is_universal ? '' : `<button class="btn danger btn-sm" id="custDelBtn" type="button">Delete</button>`}
    </div>
    <div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px;font-size:12px;">
      <span><strong style="font-size:18px;color:var(--blue);">${c.uc_count}</strong> UC${c.uc_count === 1 ? '' : 's'} requested</span>
      <span><strong style="font-size:18px;">${c.request_count}</strong> total request${c.request_count === 1 ? '' : 's'}</span>
      <span><strong style="font-size:18px;">${c.project_count}</strong> project${c.project_count === 1 ? '' : 's'}</span>
    </div>
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:14px;cursor:pointer;">
      <input type="checkbox" id="custExclToggle" ${c.is_exclusive ? 'checked' : ''} ${c.is_universal ? 'disabled' : ''} style="width:auto;height:auto;" />
      🔒 Exclusive — sealed (explicit grant required for everyone, incl. platform-admin)
    </label>
    <div class="detail-section">
      <div class="detail-section-title">Projects (associations)</div>
      <div id="custProjChips" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px;">
        ${(assoc.projects || []).map(p => `<span class="set-chip" style="display:inline-flex;gap:4px;align-items:center;">⊞ ${esc(p.name)}${p.is_exclusive ? ' 🔒' : ''}<span style="cursor:pointer;color:var(--red);font-weight:600;" data-rmproj="${p.id}" title="Remove association">×</span></span>`).join('') || '<span style="font-size:11px;color:var(--text-faint);">No projects associated yet.</span>'}
      </div>
      <div style="position:relative;">
        <span class="set-chip" id="custAddProjBtn" style="cursor:pointer;background:var(--bg-input);border:1px dashed var(--border-bright);color:var(--text-faint);" title="Associate a project (toggle membership)">+ Add project</span>
        <div id="custProjPopover" style="display:none;position:absolute;top:100%;left:0;margin-top:6px;z-index:60;background:var(--bg-panel);border:1px solid var(--border-bright);border-radius:3px;box-shadow:0 4px 12px rgba(0,0,0,0.35);min-width:240px;max-height:280px;overflow-y:auto;"></div>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">Members — who can view / edit this customer</div>
      <div id="custMembersBody"><div style="font-size:11px;color:var(--text-faint);">loading…</div></div>
    </div>`;
  document.getElementById('custExclToggle')?.addEventListener('change', async function () {
    try { await api(`/api/customers/${cid}`, { method: 'PATCH', body: JSON.stringify({ is_exclusive: this.checked }) }); loadCustomers(); }
    catch (e) { toast(e.message, true); this.checked = !this.checked; }
  });
  document.getElementById('custDelBtn')?.addEventListener('click', async () => {
    if (!confirm(`Delete customer "${c.name}"?`)) return;
    try { await api(`/api/customers/${cid}`, { method: 'DELETE' }); _custSelId = null; document.getElementById('custDetail').innerHTML = '<div class="empty">Select a customer.</div>'; loadCustomers(); }
    catch (e) { toast(e.message, true); }
  });
  document.getElementById('custAddProjBtn')?.addEventListener('click', function (e) {
    e.stopPropagation(); _openCustProjPicker(cid, this);
  });
  el.querySelectorAll('[data-rmproj]').forEach(x => x.addEventListener('click', async () => {
    try { await api(`/api/customers/${cid}/projects/${x.dataset.rmproj}`, { method: 'DELETE' }); selectCustomer(cid); loadCustomers(); }
    catch (e) { toast(e.message, true); }
  }));
  _renderCustomerMembers(cid);
}

// ── Per-customer members (#131) — grant/revoke customer-viewer/customer-edit ─────────
async function _renderCustomerMembers(cid) {
  const box = document.getElementById('custMembersBody');
  if (!box) return;
  let members = [], approved = [], custRoles = [];
  try {
    members = (await api(`/api/customers/${cid}/members`)).members || [];
    approved = await _projApprovedUsers();
    custRoles = ((await api('/api/rbac/roles')).roles || []).filter(r => r.scope === 'customer');
  } catch (e) { box.innerHTML = `<div style="color:var(--red);font-size:11px;">${esc(e.message)}</div>`; return; }
  const roleOpts = custRoles.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
  box.innerHTML = (members.length ? members.map(m => `
      <div style="display:flex;gap:8px;align-items:center;padding:3px 0;">
        <span style="flex:1;">${esc(m.display_name || m.reviewer)} <span style="color:var(--text-faint);font-size:11px;">${esc(m.email || m.reviewer)}</span></span>
        <span style="font-size:10px;background:var(--bg-input);border:1px solid var(--border);border-radius:10px;padding:1px 8px;">${esc(m.role_name || m.role_key)}</span>
        <button class="btn ghost btn-sm cm-remove" data-rev="${esc(m.reviewer)}" data-role="${m.role_id}" style="color:var(--red);" title="Revoke this role">✕</button>
      </div>`).join('') : '<div style="font-size:11px;color:var(--text-faint);">No members yet (platform admins can always manage).</div>')
    + `<div style="display:flex;gap:6px;align-items:center;margin-top:6px;">
        ${userPickerHtml('cm-add-user', 'cm-add-user-dd', '+ add user…')}
        <select id="cm-add-role" style="font-size:11px;">${roleOpts}</select>
        <button class="btn ghost btn-sm" id="cm-add-btn">Add</button>
      </div>`;
  const _cmExclude = new Set(members.map(m => (m.reviewer || '').toLowerCase()));
  wireUserPicker('cm-add-user', 'cm-add-user-dd', approved, _cmExclude, null);
  box.querySelectorAll('.cm-remove').forEach(b => b.addEventListener('click', async function () {
    try { await api(`/api/customers/${cid}/members/${encodeURIComponent(this.dataset.rev)}?role_id=${this.dataset.role}`, { method: 'DELETE' }); _renderCustomerMembers(cid); }
    catch (e) { toast(e.message, true); }
  }));
  document.getElementById('cm-add-btn')?.addEventListener('click', async () => {
    const rev = document.getElementById('cm-add-user').dataset.reviewer || '';
    const role_id = parseInt(document.getElementById('cm-add-role').value, 10);
    if (!rev) { toast('Pick a user from the list', true); return; }
    try { await api(`/api/customers/${cid}/members`, { method: 'POST', body: JSON.stringify({ reviewer: rev, role_id }) }); _renderCustomerMembers(cid); }
    catch (e) { toast(e.message, true); }
  });
}

// Customer↔project association picker — the Scoping-Sets membership pattern (popover of
// all projects with a ✓ toggle), so management UIs share one membership control.
async function _openCustProjPicker(cid, anchorEl) {
  const pop = document.getElementById('custProjPopover');
  if (!pop) return;
  if (pop.style.display === 'block') { pop.style.display = 'none'; return; }
  pop.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-faint);">loading…</div>';
  pop.style.display = 'block';
  let projects = [], assocIds = new Set();
  try {
    projects = (await api('/api/projects')).projects || [];
    assocIds = new Set(((await api(`/api/customers/${cid}/projects`)).projects || []).map(p => p.id));
  } catch (e) { pop.innerHTML = `<div style="padding:8px 12px;color:var(--red);font-size:11px;">${esc(e.message)}</div>`; return; }
  if (!projects.length) { pop.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-faint);">No projects.</div>'; return; }
  pop.innerHTML = '<div style="padding:4px 0;">' + projects.map(p => {
    const a = assocIds.has(p.id);
    return `<div class="cust-proj-row" data-pid="${p.id}" data-assoc="${a ? 1 : 0}" data-name="${esc(p.name)}"
        style="padding:6px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;${a ? 'background:var(--accent-bg);' : ''}"
        onmouseover="this.style.background='var(--bg-raised)'" onmouseout="this.style.background='${a ? 'var(--accent-bg)' : ''}'">
        <span style="font-family:var(--mono,monospace);color:${a ? 'var(--green)' : 'var(--text-faint)'};min-width:14px;">${a ? '✓' : ''}</span>
        <span style="flex:1;">${esc(p.name)}${p.is_exclusive ? ' 🔒' : ''}</span>
      </div>`;
  }).join('') + '</div>';
  pop.querySelectorAll('.cust-proj-row').forEach(row => row.addEventListener('click', () =>
    _toggleCustProj(cid, +row.dataset.pid, row.dataset.assoc === '1', row.dataset.name)));
  setTimeout(() => {
    const close = e => { if (!pop.contains(e.target) && e.target !== anchorEl) { pop.style.display = 'none'; document.removeEventListener('click', close); } };
    document.addEventListener('click', close);
  }, 0);
}
async function _toggleCustProj(cid, pid, isAssoc, name) {
  try {
    if (isAssoc) { await api(`/api/customers/${cid}/projects/${pid}`, { method: 'DELETE' }); toast(`Removed ${name}`); }
    else { await api(`/api/customers/${cid}/projects`, { method: 'POST', body: JSON.stringify({ project_id: pid }) }); toast(`Associated ${name}`); }
    selectCustomer(cid); loadCustomers();
  } catch (e) { toast(e.message, true); }
}

document.getElementById('custNewBtn')?.addEventListener('click', () => {
  const f = document.getElementById('custNewForm');
  f.style.display = f.style.display === 'none' ? '' : 'none';
  if (f.style.display === '') document.getElementById('custName').focus();
});
document.getElementById('custCancelBtn')?.addEventListener('click', () => {
  document.getElementById('custNewForm').style.display = 'none';
});
document.getElementById('custCreateBtn')?.addEventListener('click', async () => {
  const name = (document.getElementById('custName').value || '').trim();
  const msg = document.getElementById('custMsg');
  if (!name) { msg.style.color = 'var(--red)'; msg.textContent = 'name required'; return; }
  try {
    await api('/api/customers', { method: 'POST', body: JSON.stringify({
      name, description: document.getElementById('custDesc').value || '',
      is_exclusive: document.getElementById('custExcl').checked }) });
    document.getElementById('custName').value = ''; document.getElementById('custDesc').value = '';
    document.getElementById('custExcl').checked = false;
    document.getElementById('custNewForm').style.display = 'none';
    loadCustomers();
  } catch (e) { msg.style.color = 'var(--red)'; msg.textContent = e.message; }
});

// ── Projects tab (lists projects + exclusivity toggle; members stay in Config) ─
// The Projects tab IS the relocated projects-admin panel (same ids), so it reuses the
// full management surface (create / members / UC-store / archive / move-data / delete).
async function loadProjectsTab() { return loadProjectsAdmin(); }

// ── Capability Catalog tab (manual-curated, LLM-suggested) ───────────────────
let _catalogCache = [];
let _catEditId = '';
// R4 disposition ↔ Gartner TIME (dual-labelled per the capability method, #132). The eye
// goes to the verdict first: color-coded, action word leading, the familiar TIME term in tow.
const DISPOSITIONS = {
  reuse:     {label:'Reuse',     time:'Tolerate',  color:'var(--green)'},
  refurbish: {label:'Refurbish', time:'Invest',    color:'var(--blue)'},
  replace:   {label:'Replace',   time:'Migrate',   color:'var(--amber,#d97706)'},
  retire:    {label:'Retire',    time:'Eliminate', color:'var(--red)'},
};
const CLASSIFICATIONS = {
  core:       {label:'Core',       color:'var(--accent)'},
  supporting: {label:'Supporting', color:'var(--blue)'},
  generic:    {label:'Generic',    color:'var(--text-faint)'},
};
// fit (high/low) × tech (aligned/constrained) → suggested R4 disposition (2×2 from the method).
function _suggestDisposition(fit, tech){
  if (!fit || !tech) return '';
  if (fit==='high'  && tech==='aligned')     return 'reuse';
  if (fit==='high'  && tech==='constrained') return 'refurbish';
  if (fit==='low'   && tech==='aligned')     return 'reuse';      // tolerate — keep, don't invest
  if (fit==='low'   && tech==='constrained') return 'retire';
  return '';
}
function _dispBadge(d){
  const m = DISPOSITIONS[d]; if (!m) return '';
  return `<span title="R4 disposition ≈ Gartner TIME: ${m.time}" style="font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;padding:1px 6px;border-radius:8px;border:1px solid ${m.color};color:${m.color};">${m.label} <span style="opacity:0.6;font-weight:400;">·${m.time}</span></span>`;
}
function _classBadge(c){
  const m = CLASSIFICATIONS[c]; if (!m) return '';
  return `<span title="DDD subdomain — aims investment" style="font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;padding:1px 6px;border-radius:8px;background:${m.color};color:var(--on-accent,#fff);opacity:0.92;">${m.label}</span>`;
}
async function loadCatalogTab(){ if (_catView==='board') _renderCatalogBoard(); else _renderCatalog(); _renderCatalogSuggestions(); }
let _catView = 'list';
function _setCatView(mode){
  _catView = mode;
  document.getElementById('catViewListBtn')?.classList.toggle('active', mode==='list');
  document.getElementById('catViewBoardBtn')?.classList.toggle('active', mode==='board');
  const lv = document.getElementById('catList'), bv = document.getElementById('catBoard');
  if (lv) lv.style.display = mode==='list' ? '' : 'none';
  if (bv) bv.style.display = mode==='board' ? '' : 'none';
  if (mode==='board') _renderCatalogBoard(); else _renderCatalog();
}
// The R4 disposition decision surface: capabilities grouped into the four verdict columns
// (Undecided first — it's the work queue). Lead with the action, per the signal-over-noise
// north-star. Click a chip to load it in the editor (same affordance as the list).
async function _renderCatalogBoard(){
  const el = document.getElementById('catBoard');
  if (!el) return;
  try {
    _catalogCache = (await api('/api/catalog')).capabilities || [];
  } catch(e){ el.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`; return; }
  if (!_catalogCache.length){ el.innerHTML = '<div class="empty">No capabilities yet. Add one to start dispositioning →</div>'; return; }
  const cols = [
    {key:'',          label:'Undecided', time:'',          color:'var(--text-faint)'},
    {key:'reuse',     label:'Reuse',     time:'Tolerate',  color:'var(--green)'},
    {key:'refurbish', label:'Refurbish', time:'Invest',    color:'var(--blue)'},
    {key:'replace',   label:'Replace',   time:'Migrate',   color:'var(--amber,#d97706)'},
    {key:'retire',    label:'Retire',    time:'Eliminate', color:'var(--red)'},
  ];
  const byDisp = {}; cols.forEach(c => byDisp[c.key] = []);
  _catalogCache.forEach(c => { const d = DISPOSITIONS[c.disposition] ? c.disposition : ''; byDisp[d].push(c); });
  el.innerHTML = `<div style="display:flex;gap:10px;align-items:flex-start;min-width:max-content;">` + cols.map(col => {
    const items = byDisp[col.key];
    return `<div class="catb-col" data-col="${col.key}" style="flex:0 0 200px;min-width:200px;border-radius:4px;padding:2px;transition:background .1s;">
      <div style="position:sticky;top:0;background:var(--bg-panel);padding:2px 0 6px;border-bottom:2px solid ${col.color};margin-bottom:6px;">
        <span style="font-size:11px;font-weight:600;color:${col.color};text-transform:uppercase;letter-spacing:0.06em;">${col.label}</span>
        ${col.time?`<span style="font-size:9px;color:var(--text-faint);"> ·${col.time}</span>`:''}
        <span style="font-size:10px;color:var(--text-faint);float:right;">${items.length}</span>
      </div>
      ${items.length ? items.map(c => `
        <div class="catb-chip" data-id="${c.id}" draggable="true" title="Drag to a column to set disposition · click to edit" style="border:1px solid var(--border);border-left:3px solid ${col.color};border-radius:3px;padding:5px 7px;margin-bottom:5px;cursor:grab;background:var(--bg-raised);">
          <div style="font-size:11px;font-weight:600;">${esc(c.name||c.cap_key)}</div>
          <div style="font-size:9px;color:var(--text-faint);font-family:var(--mono,monospace);">${esc(c.cap_key)}</div>
          <div style="margin-top:3px;display:flex;gap:4px;flex-wrap:wrap;align-items:center;">
            ${_classBadge(c.subdomain)}
            ${(c.strategic_fit||c.tech_fitness)?`<span style="font-size:9px;color:var(--text-faint);">${c.strategic_fit?`fit:${esc(c.strategic_fit)}`:''}${(c.strategic_fit&&c.tech_fitness)?' · ':''}${c.tech_fitness?`tech:${esc(c.tech_fitness)}`:''}</span>`:''}
          </div>
        </div>`).join('') : '<div style="font-size:10px;color:var(--text-faint);padding:4px 0;">—</div>'}
    </div>`;
  }).join('') + `</div>`;
  el.querySelectorAll('.catb-chip').forEach(chip => chip.addEventListener('click', () => {
    const c = _catalogCache.find(x => String(x.id) === chip.dataset.id);
    if (!c) return;
    _setCatView('list');
    // Mirror the list row-click loader so editing is identical from either view.
    document.getElementById('catKey').value = c.cap_key;
    document.getElementById('catName').value = c.name||'';
    document.getElementById('catDomain').value = c.domain||'';
    document.getElementById('catDef').value = c.definition||'';
    document.getElementById('catDeps').value = (c.depends_on||[]).join(', ');
    document.getElementById('catClass').value = c.subdomain||'';
    document.getElementById('catFit').value = c.strategic_fit||'';
    document.getElementById('catTech').value = c.tech_fitness||'';
    document.getElementById('catDisp').value = c.disposition||'';
    _catEditId = c.id;
    document.getElementById('catClearBtn').style.display='';
    document.getElementById('catSaveBtn').textContent='Save changes';
    document.getElementById('catKey').scrollIntoView({block:'nearest'});
  }));
  // Drag a capability chip onto a column to set its R4 disposition (Reuse/Refurbish/Replace/Retire,
  // or back to Undecided). The api() backstop blocks this in View mode; the server enforces project.catalog.
  el.querySelectorAll('.catb-chip').forEach(chip => {
    chip.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', chip.dataset.id);
      e.dataTransfer.effectAllowed = 'move';
      chip.style.opacity = '0.45';
    });
    chip.addEventListener('dragend', () => { chip.style.opacity = ''; });
  });
  el.querySelectorAll('.catb-col').forEach(colEl => {
    colEl.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; colEl.style.background = 'var(--accent-bg, rgba(127,127,127,0.10))'; });
    colEl.addEventListener('dragleave', () => { colEl.style.background = ''; });
    colEl.addEventListener('drop', async e => {
      e.preventDefault(); colEl.style.background = '';
      const id = e.dataTransfer.getData('text/plain');
      if (id) await _catSetDisposition(id, colEl.dataset.col);
    });
  });
}
// Persist a drag-drop disposition change. PUT carries the FULL current catalog row (the API's
// update replaces all columns), with only `disposition` changed.
async function _catSetDisposition(id, disp) {
  const c = _catalogCache.find(x => String(x.id) === String(id));
  if (!c) return;
  if ((c.disposition || '') === (disp || '')) return;   // dropped on its own column — no-op
  const body = {
    cap_key: c.cap_key, name: c.name || '', domain: c.domain || '', definition: c.definition || '',
    depends_on: c.depends_on || [],
    subdomain: c.subdomain || null, strategic_fit: c.strategic_fit || null, tech_fitness: c.tech_fitness || null,
    disposition: disp || null,
    bounded_context: c.bounded_context || null, strategic_provider: c.strategic_provider || null,
    status: c.status || 'confirmed',
  };
  try {
    await api(`/api/catalog/${id}`, { method: 'PUT', body: JSON.stringify(body) });
    c.disposition = disp || null;   // optimistic local update
    _renderCatalogBoard();
    toast(`${c.name || c.cap_key} → ${disp ? (DISPOSITIONS[disp]?.label || disp) : 'Undecided'}`);
  } catch (e) { toast(e.message, true); _renderCatalogBoard(); }
}
async function _renderCatalog(){
  const el = document.getElementById('catList');
  if (!el) return;
  try {
    const r = await api('/api/catalog');
    _catalogCache = r.capabilities || [];
    if (!_catalogCache.length){ el.innerHTML = '<div class="empty">No capabilities yet. Add one, or confirm a suggestion →</div>'; return; }
    el.innerHTML = _catalogCache.map(c => `
      <div class="cat-row" data-id="${c.id}" title="Click anywhere to edit" style="border:1px solid var(--border);border-radius:3px;padding:8px 10px;margin-bottom:8px;cursor:pointer;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;">
          <span style="font-weight:600;">${esc(c.name||c.cap_key)}</span>
          <span style="display:flex;gap:6px;align-items:center;flex-shrink:0;">
            ${_classBadge(c.subdomain)}${_dispBadge(c.disposition)}
            <span style="font-size:9px;text-transform:uppercase;letter-spacing:0.08em;color:${c.status==='confirmed'?'var(--green)':'var(--text-faint)'};">${esc(c.status)}</span>
            <button class="btn danger btn-sm cat-del" data-id="${c.id}" title="Delete">✕</button>
          </span>
        </div>
        <div style="font-family:var(--mono,monospace);font-size:10px;color:var(--text-faint);">${esc(c.cap_key)}${c.domain?` · <span style="color:var(--blue);">${esc(c.domain)}</span>`:''}</div>
        ${c.definition?`<div style="font-size:11px;color:var(--text-dim);margin-top:3px;">${esc(c.definition)}</div>`:''}
        ${(c.strategic_provider||c.bounded_context)?`<div style="font-size:10px;color:var(--text-faint);margin-top:3px;">${c.strategic_provider?`🏷 <span style="color:var(--text-dim);" title="single strategic provider">${esc(c.strategic_provider)}</span>`:''}${(c.strategic_provider&&c.bounded_context)?' · ':''}${c.bounded_context?`<span title="bounded context">⬡ ${esc(c.bounded_context)}</span>`:''}</div>`:''}
        ${(c.depends_on&&c.depends_on.length)?`<div style="font-size:10px;color:var(--text-faint);margin-top:3px;">requires: ${esc(c.depends_on.join(', '))}</div>`:''}
      </div>`).join('');
    el.querySelectorAll('.cat-del').forEach(b => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      try { await api(`/api/catalog/${b.dataset.id}`, {method:'DELETE'}); loadCatalogTab(); } catch(err){ toast(err.message,true); }
    }));
    el.querySelectorAll('.cat-row').forEach(row => row.addEventListener('click', () => {
      const c = _catalogCache.find(x => String(x.id) === row.dataset.id);
      if (!c) return;
      document.getElementById('catKey').value = c.cap_key;
      document.getElementById('catName').value = c.name||'';
      document.getElementById('catDomain').value = c.domain||'';
      document.getElementById('catDef').value = c.definition||'';
      document.getElementById('catDeps').value = (c.depends_on||[]).join(', ');
      document.getElementById('catClass').value = c.subdomain||'';
      document.getElementById('catFit').value = c.strategic_fit||'';
      document.getElementById('catTech').value = c.tech_fitness||'';
      document.getElementById('catDisp').value = c.disposition||'';
      document.getElementById('catBC').value = c.bounded_context||'';
      document.getElementById('catProv').value = c.strategic_provider||'';
      _catEditId = c.id;
      document.getElementById('catClearBtn').style.display='';
      document.getElementById('catSaveBtn').textContent='Save changes';
      document.getElementById('catKey').scrollIntoView({block:'nearest'});
    }));
  } catch(e){ el.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`; }
}
async function _renderCatalogSuggestions(){
  const el = document.getElementById('catSuggList');
  if (!el) return;
  try {
    const r = await api('/api/catalog/suggestions');
    const sugg = r.suggestions || [];
    if (!sugg.length){ el.innerHTML = '<div class="empty">No new suggestions. Re-ingest to populate analysis capabilities.</div>'; return; }
    el.innerHTML = sugg.map(s => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;border-bottom:1px solid var(--border);padding:5px 0;">
        <span style="min-width:0;flex:1;">
          <span style="font-family:var(--mono,monospace);font-size:11px;">${esc(s.capability_id)}</span>
          ${s.usage?`<div style="font-size:10px;color:var(--text-dim);">${esc(s.usage)}</div>`:''}
        </span>
        <span style="display:flex;gap:6px;align-items:center;">
          <span style="font-size:10px;color:var(--text-faint);">${s.uc_count} UC${s.uc_count===1?'':'s'}</span>
          <button class="btn ghost btn-sm cat-draft" data-key="${esc(s.capability_id)}" title="LLM: draft a readable name + description, then review and Add">✨ draft</button>
          <button class="btn ghost btn-sm cat-confirm" data-key="${esc(s.capability_id)}">+ Add</button>
        </span>
      </div>`).join('');
    el.querySelectorAll('.cat-confirm').forEach(b => b.addEventListener('click', async () => {
      try { await api('/api/catalog', {method:'POST', body: JSON.stringify({cap_key: b.dataset.key, name: b.dataset.key, status:'confirmed'})}); loadCatalogTab(); }
      catch(e){ toast(e.message, true); }
    }));
    el.querySelectorAll('.cat-draft').forEach(b => b.addEventListener('click', async () => {
      const key = b.dataset.key;
      // Populate the editor IMMEDIATELY (no waiting on the LLM) so the capability shows
      // up right away; the readable name/description/domain then stream in async.
      _catClearForm();
      document.getElementById('catKey').value = key;
      document.getElementById('catName').value = key;
      document.getElementById('catKey').scrollIntoView({block:'nearest'});
      const msg = document.getElementById('catMsg');
      if (msg) { msg.style.color = 'var(--text-faint)'; msg.textContent = '✨ drafting a readable name + description…'; }
      const old = b.textContent; b.textContent = '…'; b.disabled = true;
      try {
        const r = await api('/api/catalog/suggest-meta', {method:'POST', body: JSON.stringify({capability_id: key})});
        // Only apply if the user hasn't moved on to a different draft/edit meanwhile.
        if (document.getElementById('catKey').value === key) {
          if (r.name) document.getElementById('catName').value = r.name;
          if (r.domain) document.getElementById('catDomain').value = r.domain;
          if (r.description) document.getElementById('catDef').value = r.description;
          if (msg) { msg.style.color = 'var(--green)'; msg.textContent = '✓ drafted — review and Add';
            setTimeout(() => { if (msg && msg.textContent.startsWith('✓ drafted')) msg.textContent = ''; }, 2500); }
        }
      } catch(e){ if (msg) { msg.style.color = 'var(--red)'; msg.textContent = 'draft failed: ' + e.message; } }
      finally { b.textContent = old; b.disabled = false; }
    }));
  } catch(e){
    // A 5xx here is almost always transient (API rolling-restart) — show a friendly retry rather
    // than a raw "500: Internal Server Error", but still surface the detail on hover.
    el.innerHTML = `<div class="empty" title="${esc(e.message)}">Couldn’t load suggestions right now
      <button class="btn ghost btn-sm" style="margin-left:8px;" onclick="_renderCatalogSuggestions()">↻ Retry</button></div>`;
  }
}
function _catClearForm(){
  ['catKey','catName','catDomain','catDef','catDeps','catClass','catFit','catTech','catDisp','catBC','catProv'].forEach(id=>{ const el=document.getElementById(id); if(el) el.value=''; });
  _catEditId = '';
  const cb=document.getElementById('catClearBtn'); if(cb) cb.style.display='none';
  const sb=document.getElementById('catSaveBtn'); if(sb) sb.textContent='Add capability';
  const msg=document.getElementById('catMsg'); if(msg) msg.textContent='';
}
document.getElementById('catClearBtn')?.addEventListener('click', _catClearForm);
// Drivers suggest the disposition (method's 2×2). Only auto-fill an empty verdict so the
// architect's explicit choice (e.g. Replace over the suggested Refurbish) is never overwritten.
['catFit','catTech'].forEach(id => document.getElementById(id)?.addEventListener('change', () => {
  const disp = document.getElementById('catDisp');
  if (!disp || disp.value) return;
  const s = _suggestDisposition(document.getElementById('catFit').value, document.getElementById('catTech').value);
  if (s){ disp.value = s; const msg=document.getElementById('catMsg'); if(msg){ msg.style.color='var(--text-faint)'; msg.textContent=`suggested: ${DISPOSITIONS[s].label} (≈ ${DISPOSITIONS[s].time}) — change if needed`; } }
}));
document.getElementById('catSaveBtn')?.addEventListener('click', async () => {
  const key = (document.getElementById('catKey').value||'').trim();
  const msg = document.getElementById('catMsg');
  if (!key){ if(msg){ msg.textContent='key required'; msg.style.color='var(--red)'; } return; }
  const body = {
    cap_key: key,
    name: (document.getElementById('catName').value||'').trim(),
    domain: (document.getElementById('catDomain').value||'').trim(),
    definition: (document.getElementById('catDef').value||'').trim(),
    depends_on: (document.getElementById('catDeps').value||'').split(',').map(s=>s.trim()).filter(Boolean),
    subdomain: document.getElementById('catClass').value || null,
    strategic_fit: document.getElementById('catFit').value || null,
    tech_fitness: document.getElementById('catTech').value || null,
    disposition: document.getElementById('catDisp').value || null,
    bounded_context: (document.getElementById('catBC').value||'').trim() || null,
    strategic_provider: (document.getElementById('catProv').value||'').trim() || null,
    status: 'confirmed',
  };
  try {
    if (_catEditId) await api(`/api/catalog/${_catEditId}`, {method:'PUT', body: JSON.stringify(body)});
    else await api('/api/catalog', {method:'POST', body: JSON.stringify(body)});
    _catClearForm(); loadCatalogTab();
  } catch(e){ if(msg){ msg.textContent=e.message; msg.style.color='var(--red)'; } }
});

document.getElementById('rpRevCopyBtn')?.addEventListener('click', () => {
  const text = localStorage.getItem('rpRevShowReasoning') === '1' ? _rpRevRaw.text : _stripThink(_rpRevRaw.text);
  navigator.clipboard.writeText(text).then(() => toast('Review copied'));
});
document.getElementById('rpEnhCopyBtn')?.addEventListener('click', () => {
  const text = localStorage.getItem('rpEnhShowReasoning') === '1' ? _rpEnhRaw.text : _stripThink(_rpEnhRaw.text);
  navigator.clipboard.writeText(text).then(() => toast('Enhancement plan copied'));
});

document.getElementById('rpRevReasoningBtn')?.addEventListener('click', function() {
  const on = localStorage.getItem('rpRevShowReasoning') === '1';
  localStorage.setItem('rpRevShowReasoning', on ? '0' : '1');
  this.textContent = on ? '○ Reasoning' : '● Reasoning';
  const el = document.getElementById('rpRevStream');
  if (el) _applyStreamRender(el, (!on) ? _rpRevRaw.text : _stripThink(_rpRevRaw.text), el.dataset.renderMode);
});
document.getElementById('rpEnhReasoningBtn')?.addEventListener('click', function() {
  const on = localStorage.getItem('rpEnhShowReasoning') === '1';
  localStorage.setItem('rpEnhShowReasoning', on ? '0' : '1');
  this.textContent = on ? '○ Reasoning' : '● Reasoning';
  const el = document.getElementById('rpEnhStream');
  if (el) _applyStreamRender(el, (!on) ? _rpEnhRaw.text : _stripThink(_rpEnhRaw.text), el.dataset.renderMode);
});

document.getElementById('rpRevUseClaudeBtn')?.addEventListener('click', () => {
  const { runId, scope, ucUuid } = _rpGetContext();
  _useInClaude('/api/arch-review/prompt', scope, runId, ucUuid,
    document.getElementById('rpRevHint'), document.getElementById('rpRevUseClaudeBtn'));
});
document.getElementById('rpEnhUseClaudeBtn')?.addEventListener('click', () => {
  const { runId, scope, ucUuid } = _rpGetContext();
  _useInClaude('/api/enhancements/prompt', scope, runId, ucUuid,
    document.getElementById('rpEnhHint'), document.getElementById('rpEnhUseClaudeBtn'));
});

// "Route into PRs ↓" — hand the freshly generated plan straight to the workbench (Step 2)
// and route it. Replaces the old single-repo Create-PR form (now superseded by the workbench).
document.getElementById('rpEnhToPrBtn')?.addEventListener('click', () => {
  const ta = document.getElementById('ewPlanText');
  if (ta) ta.value = _stripThink(_rpEnhRaw.text || '');
  const details = document.getElementById('ewPlanDetails'); if (details) details.open = false;
  document.getElementById('ewRouteBtn')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  _ewRoute();
});
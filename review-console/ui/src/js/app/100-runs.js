// ══════════════════════════ RUNS ══════════════════════════

async function loadRunsStats() {
  try {
    const s = await api('/api/runs/stats');
    const chip = document.getElementById('kwhChip');
    if (!s || !s.total_runs) { chip.style.display = 'none'; return; }
    chip.style.display = '';
    const total = s.total_kwh || 0;
    const fmt = v => v < 0.1 ? `${(v*1000).toFixed(0)} Wh` : `${v.toFixed(2)} kWh`;
    document.getElementById('kwhValue').textContent = fmt(total) + ' total';
    document.getElementById('kwhBreakdown').textContent =
      `· last 24h ${fmt(s.last_24h_kwh||0)} · 7d ${fmt(s.last_7d_kwh||0)} · ${s.total_runs} runs`;
  } catch {}
}

async function loadRuns(opts) {
  const silent = !!(opts && opts.silent);
  const listEl = document.getElementById('runsList');
  // Show the "loading…" placeholder only on the initial (non-silent) load
  // when nothing is currently rendered. Background polls keep the existing
  // rows visible while the new data fetches — no wholesale-rebuild flash.
  if (!silent && !listEl.querySelector('.run-list-item')) {
    listEl.innerHTML = '<div class="empty">loading…</div>';
  }
  try {
    const resp = await api('/api/runs?limit=100' + (_showArchivedRuns ? '&show_archived=true' : ''));
    allRuns = resp.runs || [];
    // badgeRuns lives on the Execution sub-tab → may be absent under another domain (guard).
    const _bRuns = document.getElementById('badgeRuns'); if (_bRuns) _bRuns.textContent = allRuns.length;
    _populateGlobalRunSel();   // keep the masthead run-status label in sync with the Runs list
    renderRunsList();
    const noteEl = document.getElementById('runsNote');
    if (noteEl) {
      noteEl.textContent = resp.enabled ? '' : 'Pipeline trigger not available.';
      noteEl.style.display = noteEl.textContent ? '' : 'none';
    }
    loadRunsStats();
    _renderRunChipLive();   // live masthead run-progress chip (#112)
    _ensureRunChipPoll();
  } catch (e) {
    // On silent (poll-driven) failure, keep the current list visible and
    // just log — a transient API blip shouldn't blank the user's view.
    if (!silent) {
      listEl.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
    } else {
      console.warn('runs list poll failed:', e);
    }
  }
}

// Evaluation model for a run — sourced from the trigger params (same field the
// drawer Params dump renders, `inference-model`). Compact tail (strip the org/
// path prefix); '' when unknown so the row degrades to no badge.
function _runModelRaw(r) {
  const p = r.params || {};
  return p['inference-model'] || p['inference_model'] || '';
}
function _runModelLabel(r) {
  const m = _runModelRaw(r);
  return m ? String(m).split('/').pop() : '';
}

// Fingerprint of the salient display fields. If unchanged between renders,
// the row's innerHTML doesn't need to be rebuilt at all.
function _runItemFingerprint(r) {
  return [
    r.phase,
    r.uc_total, r.uc_succeeded, r.uc_failed,
    r.set_name, r.selection_mode,
    r.session_name,
    r.started_at, r.completed_at, r.created_at,
    r.archived ? 'a' : '',
    // #branch-targeting: re-render when the resolved ref/sha lands (sha fills in at ingest).
    r.spec_repo_branch, r.corpus_repo_branch, r.spec_repo_sha, r.corpus_repo_sha,
    // run_id fills in at ingest → drives the model badge + Compare actions appearing.
    r.run_id, _runModelLabel(r), r.quarantined, r.historical,
    _rdName === r.name ? '1' : '0',
  ].join('|');
}

function _renderRunItemHtml(r) {
  const modeLabels = {set:'Set', selection:'Selection', individual:'UC', corpus:'Full corpus'};
  const friendly = r.session_name || r.name || '?';
  const params = r.params || {};
  const scopeBits = [];
  if (r.set_name)        scopeBits.push(`⊞ ${esc(r.set_name)}`);
  if (r.selection_mode)  scopeBits.push(esc(modeLabels[r.selection_mode] || r.selection_mode));
  if (!scopeBits.length) scopeBits.push(esc(params.mode || 'verification'));
  let countsHtml = '';
  if (typeof r.uc_total === 'number') {
    const okColor = (r.uc_failed || 0) > 0 ? 'var(--accent)' : 'var(--green)';
    const rowDenom = _runScopeTotal(r);   // declared scope; falls back to uc_total
    countsHtml = `<span style="font-size:10px;color:${okColor};">${r.uc_succeeded}/${rowDenom} ok</span>` +
      ((r.uc_failed || 0) > 0 ? ` <span style="font-size:10px;color:var(--red);">${r.uc_failed} fail</span>` : '');
  }
  // #branch-targeting: evaluated git ref provenance — "⎇ branch@sha7" (spec ref preferred,
  // else corpus). The branch is known at trigger; the SHA fills in once repos are cloned at ingest.
  const _evalBranch = r.spec_repo_branch || r.corpus_repo_branch;
  const _evalSha    = r.spec_repo_sha || r.corpus_repo_sha;
  let refHtml = '';
  if (_evalBranch || _evalSha) {
    const shaShort = _evalSha ? '@' + esc(String(_evalSha).slice(0, 7)) : '';
    refHtml = `<span title="Evaluated git ref (branch@sha)" style="font-size:10px;color:var(--text-dim);">⎇ ${esc(_evalBranch || '?')}${shaShort}</span>`;
  }
  // Evaluation model badge — degrades to nothing when the param is absent.
  const _modelLabel = _runModelLabel(r);
  const modelHtml = _modelLabel
    ? `<span title="Evaluation model — ${esc(_runModelRaw(r))}" style="font-size:10px;color:var(--text-dim);">⬡ ${esc(_modelLabel)}</span>`
    : '';
  // Quarantine warning — UCs the engine dropped BEFORE analysis (failed to load or
  // failed profile validation, e.g. an off-vocabulary dimension value). Without this
  // the run reports a clean result while having silently analyzed less than asked.
  // Only rendered when > 0; null (run predates recording) shows nothing.
  // Reconstructed from the DB because Tekton GC'd the PipelineRun: results are
  // intact and comparable, but pipeline logs and an exact re-run are gone. Say so
  // rather than showing it as an ordinary run whose Logs button will disappoint.
  const histHtml = r.historical
    ? `<span title="Reconstructed from stored results — Tekton has garbage-collected this PipelineRun, so pipeline logs are no longer available" style="font-size:10px;color:var(--text-faint);">🗄 archived run</span>`
    : '';
  const _quar = Number(r.quarantined || 0);
  const quarHtml = _quar > 0
    ? `<span title="${_quar} use case${_quar === 1 ? '' : 's'} excluded before analysis (failed to load or failed profile validation) — this run covered less than its scope" style="font-size:10px;color:var(--warn,#d08770);">⚠ ${_quar} quarantined</span>`
    : '';
  return `
    <input type="checkbox" class="run-sel-cb" onclick="event.stopPropagation()" onchange="toggleRunSelect('${r.name}', this.checked)" ${_selectedRuns.has(r.name) ? 'checked' : ''} title="Select for batch archive/delete" style="margin:3px 6px 0 0;width:auto;height:auto;accent-color:var(--accent);flex-shrink:0;align-self:flex-start;cursor:pointer;" />
    <div class="rli-main" style="${r.archived ? 'opacity:0.55;' : ''}">
      <div class="rli-name">${esc(friendly)}${r.archived ? ' <span style="font-size:9px;text-transform:uppercase;letter-spacing:0.08em;color:var(--text-faint);border:1px solid var(--border);padding:0 3px;border-radius:2px;">archived</span>' : ''}</div>
      <div class="rli-sub">${scopeBits.join(' · ')}${countsHtml ? ' · ' + countsHtml : ''}${refHtml ? ' · ' + refHtml : ''}${modelHtml ? ' · ' + modelHtml : ''}${quarHtml ? ' · ' + quarHtml : ''}${histHtml ? ' · ' + histHtml : ''}</div>
      <div class="rli-sub" style="font-family:var(--mono,monospace);font-size:10px;opacity:0.6;">${esc(r.name||'')}</div>
    </div>
    <div class="rli-right">
      ${phaseHtml(r.phase)}<br>
      <span style="color:var(--text-faint)">${esc(fmtDuration(r.started_at||r.created_at, r.completed_at))}</span>
    </div>
    <div class="rli-actions">
        ${!['Succeeded','Failed','Cancelled','TimedOut'].includes(r.phase) ? `<button class="btn ghost btn-sm" title="Stop this analysis (cancel the pipeline)" onclick="event.stopPropagation();stopRunConfirm('${r.name}')">⏹ Stop</button>` : ''}
        ${r.run_id ? `<button class="btn ghost btn-sm" title="Compare this analysis against another" onclick="event.stopPropagation();compareRunFromRow('${r.name}')">⇄ Compare…</button><button class="btn ghost btn-sm" title="Compare against the previous ingested analysis of the same Scoping Set" onclick="event.stopPropagation();compareRunVsPrev('${r.name}')">vs previous</button>` : ''}
        <button class="btn ghost btn-sm" title="Re-analyze with the same Scoping Set + settings" onclick="event.stopPropagation();rerunRun('${r.name}')">↻ Rerun</button>
        <button class="btn ghost btn-sm" title="${r.archived ? 'Unarchive' : 'Archive (hide from list)'}" onclick="event.stopPropagation();archiveRun('${r.name}',${r.archived ? 'false' : 'true'})">${r.archived ? 'Unarchive' : 'Archive'}</button>
        <button class="btn danger btn-sm" title="Delete completely — irreversible" onclick="event.stopPropagation();deleteRunConfirm('${r.name}')">Delete</button>
    </div>`;
}

// Diff-based render: reuses existing row elements keyed by run name,
// rewrites innerHTML only when a row's fingerprint actually changed, and
// reorders by detach-into-fragment + single re-append (one DOM op, no flash).
// Replaces the previous wholesale-rebuild that blanked the list every poll.
function renderRunsList() {
  const el = document.getElementById('runsList');
  if (!allRuns.length) {
    el.innerHTML = '<div class="empty">No analyses found. Use + New Analysis to trigger one.</div>';
    return;
  }
  const visible = allRuns.filter(_matchRunFilter);
  if (!visible.length) {
    el.innerHTML = '<div class="empty">No analyses match the filter.</div>';
    return;
  }
  // Recover from a previous empty/loading/error placeholder cleanly
  if (!el.querySelector('.run-list-item')) {
    el.innerHTML = '';
  }

  // Index existing rows by run name
  const existing = new Map();
  for (const child of Array.from(el.querySelectorAll('.run-list-item'))) {
    existing.set(child.dataset.runName, child);
  }

  // Build the new ordered set into a fragment, reusing rows where the
  // fingerprint matches. Detaching into the fragment + a single appendChild
  // at the end keeps the user's scroll position and avoids visible flash.
  const fragment = document.createDocumentFragment();
  for (const r of visible) {
    const fp = _runItemFingerprint(r);
    let item = existing.get(r.name);
    if (item) {
      existing.delete(r.name);
      if (item.dataset.fingerprint !== fp) {
        item.className = 'run-list-item' + (_rdName === r.name ? ' active' : '');
        item.innerHTML = _renderRunItemHtml(r);
        item.dataset.fingerprint = fp;
      }
    } else {
      item = document.createElement('div');
      item.className = 'run-list-item' + (_rdName === r.name ? ' active' : '');
      item.dataset.runName = r.name;
      item.dataset.fingerprint = fp;
      item.innerHTML = _renderRunItemHtml(r);
      item.addEventListener('click', () => selectRun(r.name));
    }
    fragment.appendChild(item);  // detaches from el if already attached
  }

  // Any rows still in `existing` are runs that disappeared — remove them
  for (const orphan of existing.values()) orphan.remove();

  // Single DOM op: re-attach the (possibly-reordered) rows
  el.appendChild(fragment);
  _renderRunSelectionBar();
}

// ── Run management (archive / complete delete) ──────────────────
let _showArchivedRuns = false;
document.getElementById('toggleArchivedRuns')?.addEventListener('click', function(){
  _showArchivedRuns = !_showArchivedRuns;
  this.style.color = _showArchivedRuns ? 'var(--accent)' : '';
  this.title = _showArchivedRuns ? 'Hide archived runs' : 'Show archived runs';
  loadRuns();
});
async function archiveRun(name, archived){
  try {
    await api(`/api/runs/${encodeURIComponent(name)}/archive`, {method:'POST', body: JSON.stringify({archived})});
    loadRuns();
  } catch(e){ toast(e.message, true); }
}
async function stopRunConfirm(name){
  const r = allRuns.find(x => x.name === name);
  const label = (r && r.session_name) || name;
  if (!confirm(`Stop run "${label}"?\n\nThis cancels the pipeline — in-flight UC analyses are interrupted and won't complete. Any UCs already finished keep their results. This cannot be resumed (use Re-run to start over).`)) return;
  try {
    await api(`/api/runs/${encodeURIComponent(name)}/cancel`, {method:'POST'});
    toast(`Stopping "${label}"…`);
    loadRuns();
  } catch(e){ toast(e.message, true); }
}
async function deleteRunConfirm(name){
  const r = allRuns.find(x => x.name === name);
  const label = (r && r.session_name) || name;
  if (!confirm(`Delete analysis "${label}" COMPLETELY?\n\nThis removes its analysis output, workspace result files, and the Tekton PipelineRun. It cannot be undone.\n\nUse Archive instead to just hide it.`)) return;
  try {
    const resp = await api(`/api/runs/${encodeURIComponent(name)}`, {method:'DELETE'});
    const rm = resp.removed || {};
    toast(`Deleted "${label}" — ${rm.analysis_runs||0} analysis, ${rm.workspace_dirs||0} result dir(s), PipelineRun ${rm.pipelinerun?'removed':'—'}`);
    if (_rdName === name) _rdName = null;
    loadRuns();
  } catch(e){ toast(e.message, true); }
}

// Multi-select for batch archive/delete (mirrors the Use Cases selection pattern).
function toggleRunSelect(name, checked){
  if (checked) _selectedRuns.add(name); else _selectedRuns.delete(name);
  _renderRunSelectionBar();
}
function _renderRunSelectionBar(){
  const bar = document.getElementById('runSelectionToolbar');
  const count = document.getElementById('runSelectionCount');
  if (!bar || !count) return;
  const present = new Set(allRuns.map(r => r.name));
  for (const n of [..._selectedRuns]) if (!present.has(n)) _selectedRuns.delete(n);
  const n = _selectedRuns.size;
  if (!n){ bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  count.textContent = `${n} run${n===1?'':'s'} selected`;
}
async function archiveSelectedRuns(){
  const names = [..._selectedRuns];
  if (!names.length) return;
  let ok=0, fail=0;
  for (const nm of names){ try { await api(`/api/runs/${encodeURIComponent(nm)}/archive`, {method:'POST', body: JSON.stringify({archived:true})}); ok++; } catch(e){ fail++; } }
  _selectedRuns.clear();
  toast(`Archived ${ok} analys${ok===1?'is':'es'}${fail?`, ${fail} failed`:''}`, fail>0);
  loadRuns();
}
async function deleteSelectedRuns(){
  const names = [..._selectedRuns];
  if (!names.length) return;
  if (!confirm(`Delete ${names.length} analysis${names.length===1?'':'es'} COMPLETELY?\n\nRemoves each analysis's output, workspace result files, and Tekton PipelineRun. This cannot be undone.\n\nUse Archive to just hide them.`)) return;
  let ok=0, fail=0;
  for (const nm of names){ try { await api(`/api/runs/${encodeURIComponent(nm)}`, {method:'DELETE'}); ok++; } catch(e){ fail++; } }
  _selectedRuns.clear();
  toast(`Deleted ${ok} analys${ok===1?'is':'es'}${fail?`, ${fail} failed`:''}`, fail>0);
  loadRuns();
}
document.getElementById('runSelArchiveBtn')?.addEventListener('click', archiveSelectedRuns);
document.getElementById('runSelDeleteBtn')?.addEventListener('click', deleteSelectedRuns);
document.getElementById('runSelClearBtn')?.addEventListener('click', () => {
  _selectedRuns.clear();
  document.querySelectorAll('#runsList .run-sel-cb').forEach(cb => { cb.checked = false; });
  _renderRunSelectionBar();
});

// Runs list filtering + select/deselect-all.
let _runFilter = '';
let _runPhaseFilter = '';
function _matchRunFilter(r){
  if (_runPhaseFilter && r.phase !== _runPhaseFilter) return false;
  if (!_runFilter) return true;
  const f = _runFilter.toLowerCase();
  return (r.name||'').toLowerCase().includes(f)
      || (r.session_name||'').toLowerCase().includes(f)
      || (r.category||'').toLowerCase().includes(f)
      || (r.phase||'').toLowerCase().includes(f)
      || _runModelRaw(r).toLowerCase().includes(f);
}
document.getElementById('runFilter')?.addEventListener('input', function(){ _runFilter = this.value; renderRunsList(); });
document.getElementById('runPhaseFilter')?.addEventListener('change', function(){ _runPhaseFilter = this.value; renderRunsList(); });
document.getElementById('runSelectAllBtn')?.addEventListener('click', () => {
  const visible = allRuns.filter(_matchRunFilter);
  const allSel = visible.length && visible.every(r => _selectedRuns.has(r.name));
  visible.forEach(r => { if (allSel) _selectedRuns.delete(r.name); else _selectedRuns.add(r.name); });
  document.querySelectorAll('#runsList .run-sel-cb').forEach(cb => {
    const nm = cb.closest('.run-list-item')?.dataset.runName;
    if (nm) cb.checked = _selectedRuns.has(nm);
  });
  _renderRunSelectionBar();
});

// ── Run detail panel ──────────────────────────────────────────
let _rdName = null;
let _rdPollTimer = null;
// Prompts/responses tail state — keyed by file (each UC × sample has its own)
let _rdPromptsState = {
  filesKnown: [],                  // list of turns files we've seen
  perFile: {},                     // {file: {next_offset, records: []}}
  autoScroll: true,
  pollTimer: null,
  // Per-record expand toggle (Set of record indices that are expanded).
  // Re-derived from sorted order so cleared on each clear-buffer.
  expanded: new Set(),
  // Global default for new records: 'collapsed' (default) or 'expanded'.
  // Persisted across drawer-opens via localStorage.
  defaultMode: (() => { try { return localStorage.getItem('davPromptsDefaultMode') || 'collapsed'; } catch(e) { return 'collapsed'; } })(),
};
// Per-record collapsed display cap (characters before "show full" appears)
const RD_PROMPTS_COLLAPSED_CHARS = 600;
// Track metric value history so we can show "Xs since last change" and
// flash cells when their value changes between polls.
let _rdLastGpuValues  = {};   // {gpu_id: {metric: value}}
let _rdLastVllmValues = {};   // {key: value}
// Consecutive null-poll counter per vLLM metric key. A backend that doesn't emit
// vLLM stats (e.g. llama.cpp) reports null forever; after a few polls we collapse
// those cells to one "not reported by this backend" note instead of a wall of '—'.
let _rdVllmNullStreak = {};   // {key: count}
const RD_VLLM_NULL_HIDE_AFTER = 5;
let _rdGpuLastChange  = 0;    // epoch seconds when any GPU metric last changed
let _rdVllmLastChange = 0;    // epoch seconds when any vLLM metric last changed
let _rdFreshTimer     = null; // separate timer that updates the "Xs ago" label every second
// Session token baseline — captured on drawer open (first non-null counter
// receipt). Used to compute "tokens this session" so the UI doesn't show
// the entire cumulative-since-vLLM-start total. Reset if the underlying
// counter regresses (process restart).
let _rdTokenBaseline  = {gen: null, prompt: null};
// Timeseries cache for sparklines. Loaded once per drawer session (or re-fetched
// every 60s for in-flight runs). Null = not yet loaded.
let _rdSparklines     = null;
let _rdSparkTimer     = null;  // periodic re-fetch for in-flight runs
let _rdSampleCount    = 1;     // ensemble samples/iterations per UC (run param)
let _rdUcTotal        = null;  // run's total UC count — for "UC N of M" turn labels
let _rdLastSnap       = null;  // most recent live snapshot — its values are
                               // appended as each sparkline's latest point so
                               // the graph tip always matches the live number.


function rdRenderCompactStats(snap, vlive) {
  // Single dense row for stacked/side/prompts layouts. snap is the metrics
  // snapshot; vlive optionally has server-supplied session token deltas.
  const el = document.getElementById('rdCompactStats');
  if (!snap || !snap.available) { el.innerHTML = '<span style="color:var(--text-faint)">metrics unavailable</span>'; return; }
  const gpus = snap.gpus || [];
  const v = snap.vllm || {};
  const gpuParts = gpus.map((g,i) => {
    const gfx = g.gpu_gfx_activity, vp = g.used_vram_pct, pw = g.gpu_power_watts, t = g.gpu_edge_temp_c;
    const tCls = t == null ? '' : (t > 90 ? 'hot' : t > 80 ? 'warn' : '');
    return `<span><span class="label">G${g.gpu_id ?? i}</span><span class="v">${_fmtN(gfx,0)}%</span>` +
           `<span class="sep">·</span><span class="v">${_fmtN(vp,0)}% vram</span>` +
           `<span class="sep">·</span><span class="v">${_fmtN(pw,0)}W</span>` +
           `<span class="sep">·</span><span class="v ${tCls}">${_fmtN(t,0)}°C</span></span>`;
  }).join('<span class="sep">|</span>');
  const sgen = (vlive && vlive.live_session_gen_tokens != null) ? vlive.live_session_gen_tokens : null;
  const spr  = (vlive && vlive.live_session_prompt_tokens != null) ? vlive.live_session_prompt_tokens : null;
  el.innerHTML = gpuParts + '<span class="sep">||</span>' +
    `<span><span class="label">vLLM</span>` +
    `<span class="v">${_fmtN(v.running_requests,0)} run</span>` +
    `<span class="sep">·</span><span class="v">${_fmtN(v.waiting_requests,0)} q</span>` +
    `<span class="sep">·</span><span class="v">${_fmtN(v.kv_cache_pct,0)}% KV</span>` +
    `<span class="sep">·</span><span class="v">${_fmtN(v.generation_tps,1)} t/s gen</span>` +
    `<span class="sep">·</span><span class="v">${_fmtN(v.prompt_tps,0)} t/s prompt</span>` +
    (sgen != null ? `<span class="sep">·</span><span class="v">${(sgen||0).toLocaleString()} gen / ${(spr||0).toLocaleString()} prompt session</span>` : '') +
    `</span>`;
}

async function rdRefreshPrompts() {
  // Discover turns files every poll (cheap GET; the engine adds one new
  // file when each UC starts, and we used to discover only once per
  // drawer-open — so the second + third UC in a multi-UC run never
  // showed up). Merge the discovered set into perFile without losing
  // already-accumulated records.
  if (!_rdName) return;
  try {
    const ls = await api(`/api/runs/${encodeURIComponent(_rdName)}/turns`).catch(() => ({}));
    const discovered = ls.files || [];
    for (const f of discovered) {
      if (!_rdPromptsState.perFile[f]) _rdPromptsState.perFile[f] = {next_offset:0, records:[], terminal:false};
    }
    _rdPromptsState.filesKnown = discovered.length ? discovered : _rdPromptsState.filesKnown;
    // Poll all non-terminal files' deltas in PARALLEL (was serial — one GET per file
    // per 5s tick, ~150 serial round-trips late in a big run). A file is terminal once
    // its sample loop emitted a 'summary'/'final' record; those never grow again, so we
    // stop refetching them and the per-tick fan-out shrinks to only the live samples.
    const activeFiles = _rdPromptsState.filesKnown.filter(f => !_rdPromptsState.perFile[f]?.terminal);
    await Promise.all(activeFiles.map(async f => {
      const st = _rdPromptsState.perFile[f];
      const r = await api(`/api/runs/${encodeURIComponent(_rdName)}/turns?file=${encodeURIComponent(f)}&since=${st.next_offset}`).catch(() => null);
      if (!r || !r.records) return;
      if (r.records.length) {
        st.records.push(...r.records.map(rec => ({...rec, _file: f})));
        if (r.records.some(rec => rec.kind === 'summary' || rec.kind === 'final')) st.terminal = true;
      }
      st.next_offset = r.next_offset ?? st.next_offset;
    }));
    rdRenderPrompts();
  } catch (e) {
    // silent
  }
}

// Render one section of body text with optional collapse + show-full toggle.
// Returns HTML. `idx` is the record index in `visible` (used as the toggle key).
function _renderTurnBody(idx, label, text, originalLength) {
  if (text == null || text === '') return '';
  const cap = RD_PROMPTS_COLLAPSED_CHARS;
  const len = (originalLength != null) ? originalLength : text.length;
  // The toggle handler stores `a.dataset.rdToggle` as a string in the Scoping Set;
  // matching here on a number meant `expanded.has(3)` after `add('3')`
  // was always false, so "show full" never expanded. Force string both
  // ways. Same fix applies to the `idx + ':args'` tool-arg variant — it's
  // already a string so just match formats.
  const key = String(idx);
  const wantsExpanded = _rdPromptsState.expanded.has(key) || _rdPromptsState.defaultMode === 'expanded';
  const truncatedByEngine = len > text.length;          // engine hit MAX_FIELD_BYTES
  const truncatedByUI     = !wantsExpanded && text.length > cap;
  const display = truncatedByUI ? text.slice(0, cap) : text;
  const labelPrefix = label ? `<span class="sub">${esc(label)}:</span> ` : '';
  let suffix = '';
  if (truncatedByUI || truncatedByEngine) {
    const showing = display.length;
    const note = truncatedByEngine
      ? `<span class="sub">… ${(len-text.length).toLocaleString()} chars not stored (engine cap)</span>`
      : `<span class="sub">… ${(len - cap).toLocaleString()} more</span>`;
    const action = truncatedByUI
      ? `<a href="javascript:void(0)" data-rd-toggle="${esc(key)}" style="color:var(--accent);font-size:10px;margin-left:6px;">show full</a>`
      : '';
    suffix = `<div style="margin-top:2px">${note}${action}</div>`;
  } else if (wantsExpanded && text.length > cap) {
    suffix = `<div style="margin-top:2px"><a href="javascript:void(0)" data-rd-toggle="${esc(key)}" style="color:var(--text-faint);font-size:10px;">collapse</a></div>`;
  }
  return `<div>${labelPrefix}<span style="white-space:pre-wrap">${esc(display)}</span></div>${suffix}`;
}

function rdRenderPrompts() {
  const all = [];
  for (const f of Object.keys(_rdPromptsState.perFile)) {
    all.push(..._rdPromptsState.perFile[f].records);
  }
  const el = document.getElementById('rdPrompts');
  document.getElementById('rdPromptsCount').textContent = all.length
    ? `· ${all.length} turn records · default ${_rdPromptsState.defaultMode}` : '';
  if (!all.length) {
    el.innerHTML = '<div class="empty" style="font-size:11px;color:var(--text-faint)">no per-turn records yet · written by the engine as it analyzes each UC</div>';
    return;
  }
  // Group turns by their sample (one agent loop = one UC+seed = one JSONL file)
  // and keep each sample's turns contiguous, ordered by turn number. Previously
  // every turn was sorted by a single global timestamp, which INTERLEAVED the
  // turns of concurrently-running samples — the same iteration's "turn 0,1,2…"
  // got scattered across the list and read as duplicated turns. Groups are
  // ordered by when each iteration began (earliest ts in the group).
  const _groupKey = r => r._file || `${r.uc_uuid || '?'}::${r.sample_seed ?? ''}`;
  const _groups = new Map();
  for (const r of all) {
    const k = _groupKey(r);
    if (!_groups.has(k)) _groups.set(k, []);
    _groups.get(k).push(r);
  }
  for (const g of _groups.values())
    g.sort((a,b) => (a.turn ?? 0) - (b.turn ?? 0) || (a.ts||'').localeCompare(b.ts||''));
  const _groupList = [..._groups.entries()]
    .sort((a,b) => ((a[1][0]||{}).ts||'').localeCompare((b[1][0]||{}).ts||''));
  // Iteration number = 1-based index of this sample among its UC's samples in
  // start order. Computed ONCE over the ordered groups, so it's stable and
  // correct even under concurrency (the old per-flip increment double-counted).
  const _ucCount = {};
  const _groupIter = new Map();   // groupKey → iteration number within its UC
  for (const [k, g] of _groupList) {
    const uc = (g[0] && g[0].uc_uuid) || k;
    _ucCount[uc] = (_ucCount[uc] || 0) + 1;
    _groupIter.set(k, _ucCount[uc]);
  }
  const ordered = _groupList.flatMap(([, g]) => g);
  // UC ordinal (1-based, first-seen order) → for "UC N of M" labels on the UC
  // boundary row and every turn row. Total is the run's planned UC count when
  // known, else the distinct UCs present in the records so far.
  // Only the 'start' record of each sample carries uc_uuid; response/tool rows
  // have uc_uuid=null. So derive the UC identity per GROUP (every record has a
  // _file-based groupKey) and map groupKey → ucNum, so the label resolves on
  // EVERY row, not just the start row.
  const _ucOrder = new Map();      // uc identity → 1-based ordinal
  const _groupUcNum = new Map();   // groupKey → ucNum
  for (const [k, g] of _groupList) {
    let ucId = null;
    for (const rec of g) if (rec.uc_uuid) { ucId = rec.uc_uuid; break; }
    // Fall back to the file name with the per-sample seed suffix stripped, so
    // multiple seeds of one UC collapse to the same UC number.
    if (!ucId) ucId = String(k).replace(/\.seed-\d+\.jsonl$/i, '').replace(/\.jsonl$/i, '');
    if (!_ucOrder.has(ucId)) _ucOrder.set(ucId, _ucOrder.size + 1);
    _groupUcNum.set(k, _ucOrder.get(ucId));
  }
  const _ucTotal = _rdUcTotal || _ucOrder.size || null;
  // Cap to most recent 400 to keep DOM bounded
  const visible = ordered.slice(-400);
  let lastBoundaryKey = null;
  const html = visible.map((r, idx) => {
    const kind = r.kind || 'turn';
    let head = `<span class="kind ${esc(kind)}">${esc(kind)}</span>`;
    head += `turn ${r.turn ?? '?'}`;
    // Per-turn UC tag so every row shows which UC (of how many) it belongs to.
    const _ucNum = _groupUcNum.get(_groupKey(r));
    if (_ucNum) head += ` · <span style="color:var(--text-faint)">UC ${_ucNum}${_ucTotal ? ' of '+_ucTotal : ''}</span>`;
    // Per-turn iteration tag so every turn is tied to the ensemble sample it
    // belongs to (not just the boundary banner) — disambiguates interleaved
    // samples at a glance.
    const _iterTag = _groupIter.get(_groupKey(r));
    if (_iterTag) head += ` · <span style="color:var(--text-faint)">iter ${_iterTag}${_rdSampleCount ? '/'+_rdSampleCount : ''}</span>`;
    if (r.tool_name) head += ` · ${esc(r.tool_name)}`;
    if (r.tokens_total != null) head += ` · ${r.tokens_total.toLocaleString()} tok`;
    if (r.content_length != null && kind === 'response') head += ` · ${r.content_length.toLocaleString()} chars`;
    if (r.result_length != null && kind === 'tool')     head += ` · ${r.result_length.toLocaleString()} chars`;
    let body = '';
    if (kind === 'start') {
      body  = `<div class="sub">UC ${esc(r.uc_uuid || '')}${r.sample_seed != null ? ' · seed '+r.sample_seed : ''} · max_tool_calls=${r.max_tool_calls ?? '?'}</div>`;
      // System prompt: prefer the new full key, fall back to legacy preview key
      const sys = r.system_prompt ?? r.system_prompt_preview;
      if (sys) body += _renderTurnBody(idx, 'system', sys, r.system_prompt_length);
      const usr = r.user_prompt ?? r.user_prompt_preview;
      if (usr) body += _renderTurnBody(idx, 'user', usr, r.user_prompt_length);
    } else if (kind === 'response') {
      const content = r.content ?? r.content_preview;
      if (content) body = _renderTurnBody(idx, null, content, r.content_length);
      else {
        const n = r.tool_call_count || 0;
        body = `<div class="sub">↳ model went straight to ${n} tool call${n===1?'':'s'} — no narration this turn (normal for tool-using models like Qwen3)</div>`;
      }
    } else if (kind === 'tool') {
      const args = r.args ? JSON.stringify(r.args, null, 2) : '';
      body = _renderTurnBody(idx + ':args', 'args', args, args.length);
      const result = r.result ?? r.result_preview;
      if (result) body += _renderTurnBody(idx, null, result, r.result_length);
    } else {
      body = `<div>${esc(JSON.stringify(r).slice(0,300))}</div>`;
    }
    // Boundary banner whenever the UC *or* the ensemble sample (seed) changes.
    // Each UC, and each ensemble sample within it, is a SEPARATE agent loop, so
    // the turn counter restarts at 0 every time — without this the repeated
    // "turn 0…N" blocks read like a glitch. One JSONL file = one UC+seed
    // sequence, so the file path is the reliable boundary key.
    let boundary = '';
    const recUuid = r.uc_uuid || null;
    const boundaryKey = r._file || (recUuid != null ? `${recUuid}::${r.sample_seed ?? ''}` : null);
    if (boundaryKey && boundaryKey !== lastBoundaryKey) {
      const ts = r.ts ? new Date(r.ts).toLocaleTimeString() : '';
      let seed = (r.sample_seed != null) ? r.sample_seed : null;
      if (seed == null && r._file) { const m = r._file.match(/seed-(\d+)/); if (m) seed = m[1]; }
      const ucShort = recUuid || (r._file ? r._file.replace(/^.*\//, '').replace(/\.jsonl$/, '') : '?');
      // Which ensemble iteration of THIS UC — count distinct seeds seen for the
      // UC in order; total = the run's sample count.
      const iter = _groupIter.get(boundaryKey) || 1;
      const total = _rdSampleCount || null;
      const ucNum = _groupUcNum.get(boundaryKey);
      boundary = `<div class="uc-boundary">▶ Use case <span class="uc-uuid">${esc(ucShort)}</span>`
        + (ucNum ? `<span class="uc-meta">· UC <b style="color:var(--accent)">${ucNum}</b>${_ucTotal ? ' of ' + _ucTotal : ''}</span>` : '')
        + `<span class="uc-meta">· iteration <b style="color:var(--accent)">${iter}</b>${total ? ' of ' + total : ''}</span>`
        + (seed != null ? `<span class="uc-meta">· seed ${esc(String(seed))}</span>` : '')
        + (ts ? `<span class="uc-meta">· ${esc(ts)}</span>` : '')
        + `<span class="uc-meta">· turns restart at 0</span></div>`;
      lastBoundaryKey = boundaryKey;
    }
    return `${boundary}<div class="turn-rec kind-${esc(kind)}"><div class="head">${head}</div><div class="body">${body}</div></div>`;
  }).join('');
  el.innerHTML = html;
  // Wire up per-record toggle links
  el.querySelectorAll('[data-rd-toggle]').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      const key = a.dataset.rdToggle;
      if (_rdPromptsState.expanded.has(key)) _rdPromptsState.expanded.delete(key);
      else _rdPromptsState.expanded.add(key);
      rdRenderPrompts();
    });
  });
  if (_rdPromptsState.autoScroll) el.scrollTop = el.scrollHeight;
}

// Kick off a run scoped to the UCs that have no current evaluation (un-evaluated or stale) —
// the "evaluate what's missing" action. Opens the New Ingestion modal pre-filled with those UCs.
let _auditNeedsEval = [];
function _evaluateNeedsEval() {
  if (!_auditNeedsEval.length) return;
  // Open New Ingestion pre-selected to the Stale / un-ingested scope (UCs needing evaluation).
  openNewRun(undefined, undefined, undefined, undefined, { set_id: '__stale__', selection_mode: 'selection' });
}

// 3c: the Runs view's default content is a UC-INGESTION AUDIT (decision 4b) — runs are the
// ingestion event; this shows, per UC, what's been evaluated, when, by which run, and freshness.
let _auditFilter = 'all';   // 'all' | 'failed' | 'stale'
let _auditUCs = [];
function _phaseBadge(phase) {
  if (!phase) return '';
  const map = { engine:['var(--red)','engine'], analysis:['var(--red)','analysis'],
                ingest:['var(--red)','ingest'], not_emitted:['var(--amber,#d79a2b)','dropped'],
                unreliable:['var(--amber,#d79a2b)','unreliable'] };
  const [c, l] = map[phase] || ['var(--text-faint)', phase];
  return `<span title="failure stage" style="font-size:8px;color:${c};border:1px solid ${c}55;border-radius:2px;padding:0 4px;margin-left:4px;text-transform:uppercase;letter-spacing:.04em;">${esc(l)}</span>`;
}
function _ucStateCell(u) {
  if (u.failed) return `<span style="color:var(--red);font-size:10px;">✗ failed</span>${_phaseBadge(u.error_phase)}`;
  if (u.stale) {
    // #114: distinguish content-edit staleness from code-drift (repo HEAD moved since eval).
    const tag = (u.stale_drifted && !u.stale_edited) ? ' · code'
              : (u.stale_drifted ? ' · edited+code' : '');
    const why = u.stale_drifted ? (u.stale_edited ? 'edited AND the code drifted since eval' : 'the code drifted since eval')
                                : 'edited since eval';
    return `<span title="Stale — ${why}" style="color:var(--amber,#d79a2b);font-size:10px;">● stale${tag}</span>`;
  }
  if (u.evaluated) return (u.error_phase === 'unreliable')
    ? `<span style="color:var(--amber,#d79a2b);font-size:10px;">⚠ unreliable</span>`
    : '<span style="color:var(--green);font-size:10px;">fresh</span>';
  return '<span style="color:var(--text-faint);font-size:10px;">never ingested</span>';
}
function _reanalyzeUC(uuid) {
  if (!uuid) return;
  openNewRun(undefined, undefined, { handles: [], uuids: [uuid], managed: [uuid] },
    undefined, { selection_mode: 'selection' });
}
// _auditTargetId lets the audit paint into either the default Runs-view body
// ('rdRunBody', shown when no run is open) OR an appended section inside a
// selected terminal run's detail ('rdAuditBody'). setAuditFilter reuses it.
let _auditTargetId = 'rdRunBody';
function setAuditFilter(f) { _auditFilter = f; _paintAnalysisAudit(_auditTargetId); }
async function _renderAnalysisAudit(targetId) {
  targetId = targetId || 'rdRunBody';
  _auditTargetId = targetId;
  const isDefault = (targetId === 'rdRunBody');
  const el = document.getElementById(targetId);
  if (!el) return;
  if (isDefault && _rdName) return;   // default (no run open) state only
  el.innerHTML = '<div class="rd-empty">loading analysis audit…</div>';
  let r;
  try { r = await api('/api/results/uc-latest'); }
  catch (e) { el.innerHTML = `<div class="rd-empty" style="color:var(--red)">${esc(e.message)}</div>`; return; }
  if (isDefault && _rdName) return;   // a run got opened while loading
  _auditUCs = r.ucs || [];
  _auditMeta = { total: r.total || 0, evaluated: r.evaluated || 0, failed: r.failed || 0 };
  _auditNeedsEval = _auditUCs.filter(u => !u.evaluated || u.stale).map(u => u.uc_uuid);
  _paintAnalysisAudit(targetId);
}
let _auditMeta = { total: 0, evaluated: 0, failed: 0 };
function _paintAnalysisAudit(targetId) {
  targetId = targetId || _auditTargetId || 'rdRunBody';
  const isDefault = (targetId === 'rdRunBody');
  const el = document.getElementById(targetId);
  if (!el || (isDefault && _rdName)) return;
  const ucs = _auditUCs || [];
  const failedN = ucs.filter(u => u.failed).length;
  const staleN  = ucs.filter(u => u.stale).length;
  const needsN  = ucs.filter(u => !u.evaluated || u.stale).length;
  const shown = ucs.filter(u =>
    _auditFilter === 'failed' ? u.failed : _auditFilter === 'stale' ? u.stale : true);
  const chip = (key, label, n, color) =>
    `<button class="btn ${_auditFilter === key ? 'primary' : 'ghost'} btn-sm" style="font-size:10px;"
             onclick="setAuditFilter('${key}')">${label}${n != null ? ` (${n})` : ''}</button>`;
  const rows = shown.map(u => {
    const needsReingest = u.failed || u.stale || !u.evaluated;
    return `
    <tr style="border-bottom:1px solid var(--border);${u.failed ? 'background:rgba(220,80,80,0.05);' : ''}">
      <td style="padding:5px 8px;max-width:260px;">
        <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(u.title || u.uc_handle || u.uc_uuid)}</div>
        ${u.error_reason ? `<div title="${esc(u.error_reason)}" style="font-size:9px;color:var(--text-faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px;">↳ ${esc(u.error_reason)}</div>` : ''}
      </td>
      <td style="padding:5px 8px;">${u.failed ? '<span style="color:var(--text-faint);font-size:10px;">—</span>' : (u.evaluated ? _verdictBadge(u.verdict) : '<span style="color:var(--text-faint);font-size:10px;">not evaluated</span>')}</td>
      <td style="padding:5px 8px;color:var(--text-dim);font-size:10px;white-space:nowrap;">${u.analyzed_at ? _ago(u.analyzed_at) : '—'}</td>
      <td style="padding:5px 8px;font-family:var(--mono,monospace);font-size:9px;color:var(--text-faint);">${u.run_id ? esc(u.run_id.slice(0, 18)) : '—'}</td>
      <td style="padding:5px 8px;white-space:nowrap;">${_ucStateCell(u)}</td>
      <td style="padding:5px 8px;white-space:nowrap;">${needsReingest ? `<button class="btn ghost btn-icon" title="Re-analyze just this use case" onclick="_reanalyzeUC('${esc(u.uc_uuid)}')" style="font-size:11px;">↻</button>` : ''}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `
    <div style="padding:10px 14px;">
      <div style="font-size:13px;font-weight:600;margin-bottom:2px;">UC Analysis Audit</div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
        <div style="font-size:11px;color:var(--text-faint);flex:1;min-width:160px;">
          <strong>${_auditMeta.evaluated}/${_auditMeta.total}</strong> evaluated${failedN ? ` · <span style="color:var(--red);">${failedN} failed</span>` : ''} · latest analysis per use case.</div>
        ${needsN
          ? `<button class="btn primary btn-sm" onclick="_evaluateNeedsEval()" title="Open a new ingestion scoped to the use cases needing evaluation (failed / stale / never ingested)">▶ Analyze ${needsN} needing evaluation</button>`
          : '<span style="font-size:11px;color:var(--green);">all evaluated &amp; fresh</span>'}
      </div>
      <div style="display:flex;gap:5px;margin-bottom:8px;">
        ${chip('all', 'All', ucs.length)}${chip('failed', 'Failed', failedN)}${chip('stale', 'Stale', staleN)}
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead><tr style="text-align:left;color:var(--text-faint);font-size:10px;text-transform:uppercase;letter-spacing:.05em;">
          <th style="padding:4px 8px;">Use case</th><th style="padding:4px 8px;">Verdict</th><th style="padding:4px 8px;">Last eval</th><th style="padding:4px 8px;">Analysis</th><th style="padding:4px 8px;">State</th><th style="padding:4px 8px;"></th>
        </tr></thead>
        <tbody>${rows || `<tr><td colspan="6" style="padding:12px;color:var(--text-faint);">${ucs.length ? 'No use cases match this filter.' : 'No use cases in this project.'}</td></tr>`}</tbody>
      </table>
    </div>`;
}

// Ingest a terminal-but-uningested run's partial results. The ingest endpoint is
// keyed by the WORKSPACE run_id, which we don't have for an uningested run — the
// turns endpoint correlates the PipelineRun name → its workspace run-dir, so we
// resolve the run_id there, then POST the existing idempotent ingest.
async function ingestPartialResults(name) {
  const btn = document.getElementById('rdIngestPartialBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Ingesting…'; }
  try {
    const t = await api(`/api/runs/${encodeURIComponent(name)}/turns`);
    const runId = t && t.run_id;
    if (!runId) { toast('No workspace results found for this run yet', true); return; }
    const resp = await api(`/api/analysis/ingest/${encodeURIComponent(runId)}`, { method: 'POST' });
    toast(`Ingested ${resp.ingested_ucs || 0} UCs, ${resp.ingested_gaps || 0} gaps from partial results`);
    await loadRuns();
    try { await loadResults(); } catch (_) {}
    selectRunResult(runId);
  } catch (e) {
    toast('Ingest failed: ' + e.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↓ Ingest partial results'; }
  }
}

// Append a per-UC analysis-audit section to the (terminal) run's detail body so a
// finished/partial run shows exactly which UCs were evaluated + a one-click
// re-analyze for the missing ones — the same audit that fronts the empty Runs view.
function _ensureRunAuditSection() {
  const body = document.getElementById('rdRunBody');
  if (!body || document.getElementById('rdAuditSection')) return;
  const sec = document.createElement('div');
  sec.className = 'run-section';
  sec.id = 'rdAuditSection';
  sec.innerHTML = `<div class="run-section-title">UC analysis audit <span style="font-weight:400;color:var(--text-faint);font-size:10px;">— latest eval per use case; re-analyze what's missing</span></div>
    <div id="rdAuditBody"><div class="empty" style="font-size:11px">loading…</div></div>`;
  body.appendChild(sec);
  _renderAnalysisAudit('rdAuditBody');
}

function selectRun(name) {
  _rdName = name;
  // This run is now the system's current run: if it has ingested analysis, make
  // it the current analysis run (drives Results/Architecture/Engineering + masthead).
  const _r = allRuns.find(x => x.name === name);
  if (_r && _r.run_id && _r.run_id !== activeRunResultId) selectRunResult(_r.run_id);
  // Highlight in list — by the STABLE run name the rows already carry, never
  // display text: matching on session_name text highlighted every retry of an
  // identically-named run at once (screenshot 2026-07-28: two MULTI-LENS
  // attempts both "selected"), and the && / || precedence made the fallback
  // fire on unrelated rows.
  document.querySelectorAll('.run-list-item').forEach(el =>
    el.classList.toggle('active', el.dataset.runName === name));
  renderRunsList(); // re-render to show active state
  // Show detail panel header + tabs
  document.getElementById('rdPanelHeader').style.display = '';
  // The run-detail "Review & Plan" tab is retired — review/enhancement/PR generation has
  // ONE home (Roadmaps), reached via the "Review this analysis →" header launcher. The
  // embedded generator body + its listeners + openRunDetailReview were removed in the
  // dead-code purge; rdSwitchTab now just keeps the Analysis body shown.
  document.getElementById('rdTabStrip').style.display = 'none';
  try { rdSwitchTab('run'); } catch (_) {}   // keep the Analysis body visible
  document.getElementById('rdTitle').textContent = name;
  document.getElementById('rdSub').textContent = 'loading…';
  // Reset run body
  const runBody = document.getElementById('rdRunBody');
  runBody.innerHTML = `
    <div class="run-section" id="rdSessionSection" style="display:none;">
      <div class="run-section-title">Session</div>
      <div id="rdSession" class="kv-grid" style="font-size:11px"></div>
    </div>
    <div class="run-section" id="rdProgressSection" style="display:none;">
      <div class="run-section-title">UC progress (live)</div>
      <div id="rdProgress"></div>
    </div>
    <!-- Operational stats: GPU + Inference side-by-side on wide screens (≥1100px) -->
    <div class="rd-stats-grid">
      <div class="run-section" id="rdGpusSection">
        <div class="run-section-title">GPUs <span id="rdGpuMode">(live)</span><span id="rdGpuFresh" class="freshness-chip"></span></div>
        <div id="rdGpus"><div class="empty" style="font-size:11px">loading metrics…</div></div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:6px;line-height:1.6;">
          <b>VRAM</b> stays near 100% (vLLM pre-allocates). AMD <b>GFX activity</b> on RDNA4 latches at 100% under light load — use <b>power</b> and <b>KV cache %</b> as the real busy signal.
        </div>
      </div>
      <div class="run-section" id="rdVllmSection">
        <div class="run-section-title">Inference (vLLM, <span id="rdVllmMode">live</span>)<span id="rdVllmFresh" class="freshness-chip"></span></div>
        <div id="rdVllm" class="vllm-grid"></div>
      </div>
    </div>
    <!-- Pipeline output + prompt stream below the operational stats -->
    <div class="rd-tail-section" id="rdTasksSection">
      <div class="rd-tail-header" data-toggle="rdTasksSection" style="cursor:pointer;user-select:none;">
        <span class="lbl">Pipeline tasks <span id="rdTasksCount" style="text-transform:none;letter-spacing:0;color:var(--text-faint)"></span></span>
        <span class="ctl">click to collapse</span>
      </div>
      <div class="rd-tail-body" id="rdTasks"><div class="empty" style="font-size:11px">loading…</div></div>
    </div>
    <div class="rd-tail-section" id="rdPromptsSection">
      <div class="rd-tail-header" data-toggle="rdPromptsSection" style="cursor:pointer;user-select:none;">
        <span class="lbl">Prompts &amp; responses (live) <span id="rdPromptsCount" style="text-transform:none;letter-spacing:0;color:var(--text-faint)"></span></span>
        <span class="ctl">
          <button id="rdPromptsExpandBtn" title="default mode for new + reset records">expand all</button>
          <button id="rdPromptsAutoBtn" title="toggle auto-scroll">⤓ auto</button>
          <button id="rdPromptsClearBtn" title="clear visible buffer">clear</button>
        </span>
      </div>
      <div class="rd-tail-body" id="rdPrompts"><div class="empty" style="font-size:11px;color:var(--text-faint)">no per-turn records yet</div></div>
    </div>
    <div class="run-section">
      <div class="run-section-title">Params</div>
      <div id="rdParams" class="kv-grid" style="font-size:11px"></div>
    </div>`;
  // Wire up re-created tail section toggles + prompt buttons
  runBody.querySelectorAll('.rd-tail-header[data-toggle]').forEach(h => {
    h.addEventListener('click', () => document.getElementById(h.dataset.toggle).classList.toggle('collapsed'));
  });
  _wirePromptButtons();

  // Switch to run tab
  rdSwitchTab('run');

  // Reset state
  _rdLastGpuValues = {}; _rdLastVllmValues = {}; _rdVllmNullStreak = {};
  _rdGpuLastChange = 0;  _rdVllmLastChange = 0;
  _rdTokenBaseline = {gen: null, prompt: null};
  _rdSparklines    = null;
  if (_rdSparkTimer) { clearInterval(_rdSparkTimer); _rdSparkTimer = null; }
  _rdPromptsState = {
    filesKnown:[], perFile:{}, autoScroll:true, pollTimer:null,
    expanded: new Set(),
    defaultMode: (() => { try { return localStorage.getItem('davPromptsDefaultMode') || 'collapsed'; } catch(e) { return 'collapsed'; } })(),
  };
  // Initialize expand btn label
  const expBtn = document.getElementById('rdPromptsExpandBtn');
  if (expBtn) expBtn.textContent = _rdPromptsState.defaultMode === 'expanded' ? 'collapse all' : 'expand all';

  // Update masthead chip
  const run = allRuns.find(r => r.name === name);
  updateRunChip(run?.session_name || name, run?.phase);

  // Show "View Results" if results exist for this run. NB: `name` is the Tekton
  // PipelineRun name, NOT the workspace analysis run_id — matching r.run_id===name
  // never hit. Results rows carry run_name (the Tekton name) joined from run_sessions,
  // so match on that; also accept the run's own ingested run_id (_r.run_id).
  const resultBtn = document.getElementById('rdViewResultsBtn');
  const hasResult = allResults.find(r => r.run_name === name || (_r && _r.run_id && r.run_id === _r.run_id));
  resultBtn.style.display = hasResult ? '' : 'none';

  // Partial-results affordance: a run that reached a terminal phase but was never
  // ingested (no run_id / no results row) offers a one-click ingest of whatever
  // completed. Reuses the existing ingest endpoint (run name → workspace run_id
  // via the turns correlation).
  const ingestBtn = document.getElementById('rdIngestPartialBtn');
  if (ingestBtn) {
    const terminal = ['Succeeded','Failed','Cancelled','TimedOut'].includes(_r?.phase);
    ingestBtn.style.display = (terminal && !hasResult) ? '' : 'none';
    ingestBtn.onclick = () => ingestPartialResults(name);
  }

  // Show "Diagnose" — available for any run (the diagnoser resolves the run
  // name to its workspace results dir; a clean run simply yields 0 proposals).
  const diagBtn = document.getElementById('rdDiagnoseBtn');
  if (diagBtn) diagBtn.style.display = '';

  // Show "Review this analysis →" once results exist (review/enhancement/PRs need an
  // analyzed corpus). The single generation home is Roadmaps; this just opens it scoped
  // to the analysis (IA slice 3).
  const reviewBtn = document.getElementById('rdReviewBtn');
  if (reviewBtn) reviewBtn.style.display = hasResult ? '' : 'none';

  // Start polling
  stopRunPolling();
  refreshRunDrawer();
  _rdPollTimer = setInterval(refreshRunDrawer, 3000);
  rdRefreshPrompts();
  _rdPromptsState.pollTimer = setInterval(rdRefreshPrompts, 5000);
  if (_rdFreshTimer) clearInterval(_rdFreshTimer);
  _rdFreshTimer = setInterval(updateFreshnessLabels, 1000);
}

function stopRunPolling() {
  if (_rdPollTimer)  { clearInterval(_rdPollTimer);  _rdPollTimer  = null; }
  if (_rdFreshTimer) { clearInterval(_rdFreshTimer); _rdFreshTimer = null; }
  if (_rdSparkTimer) { clearInterval(_rdSparkTimer); _rdSparkTimer = null; }
  if (_rdPromptsState?.pollTimer) { clearInterval(_rdPromptsState.pollTimer); _rdPromptsState.pollTimer = null; }
}

function _wirePromptButtons() {
  const autoBtn = document.getElementById('rdPromptsAutoBtn');
  if (autoBtn) {
    _setupAutoFollow(
      document.getElementById('rdPrompts'),
      autoBtn,
      // Get/set are wired against the existing _rdPromptsState global so the
      // rest of the prompt-rendering code is unchanged.
      () => _rdPromptsState.autoScroll,
      v => { _rdPromptsState.autoScroll = v; },
    );
  }
  const clearBtn = document.getElementById('rdPromptsClearBtn');
  if (clearBtn) clearBtn.addEventListener('click', e => {
    e.stopPropagation();
    for (const f of Object.keys(_rdPromptsState.perFile)) _rdPromptsState.perFile[f].records = [];
    _rdPromptsState.expanded = new Set();
    rdRenderPrompts();
  });
  const expBtn = document.getElementById('rdPromptsExpandBtn');
  if (expBtn) expBtn.addEventListener('click', e => {
    e.stopPropagation();
    _rdPromptsState.defaultMode = (_rdPromptsState.defaultMode === 'expanded') ? 'collapsed' : 'expanded';
    _rdPromptsState.expanded = new Set();
    try { localStorage.setItem('davPromptsDefaultMode', _rdPromptsState.defaultMode); } catch(err) {}
    e.target.textContent = _rdPromptsState.defaultMode === 'expanded' ? 'collapse all' : 'expand all';
    rdRenderPrompts();
  });
}

// Keep openRunDrawer as an alias used by openReviewPane / other callers
function openRunDrawer(name) { selectRun(name); }
function closeRunDrawer() { stopRunPolling(); }

function _freshnessLabel(lastChangeEpoch) {
  if (!lastChangeEpoch) return {text: '', cls: ''};
  const ago = Math.max(0, Math.floor(Date.now()/1000 - lastChangeEpoch));
  const cls = ago > 90 ? 'very-stale' : ago > 30 ? 'stale' : '';
  // Show seconds up to 119, then "Xm Ys"
  const text = ago < 120 ? `· no change ${ago}s` : `· no change ${Math.floor(ago/60)}m ${ago%60}s`;
  return {text, cls};
}

function updateFreshnessLabels() {
  for (const [el, last] of [
    ['rdGpuFresh',  _rdGpuLastChange],
    ['rdVllmFresh', _rdVllmLastChange],
  ]) {
    const e = document.getElementById(el); if (!e) continue;
    const f = _freshnessLabel(last);
    e.textContent = f.text;
    e.className = 'freshness-chip ' + f.cls;
  }
}

function _flashChanged(domId) {
  const el = document.getElementById(domId); if (!el) return;
  el.classList.add('metric-flash');
  setTimeout(() => el.classList.remove('metric-flash'), 800);
}

// Build a small inline SVG sparkline from an array of [ts, val] pairs.
// Returns an SVG element string (or empty string if no data).
// `domain` (optional [lo,hi]) fixes the y-scale — pass [0,100] for percentage
// metrics (GFX, KV) so a pinned-high value renders near the top instead of the
// auto-scaled bottom. Without a domain, a perfectly flat series is drawn through
// the vertical middle (a constant 100% GFX otherwise collapses to a misleading
// bottom line — the "flat line at low utilization" artifact).
function _sparklineSVG(pts, cls, w = 110, h = 22, domain = null) {
  if (!pts || pts.length < 2) return '';
  const vals = pts.map(p => p[1]);
  const times = pts.map(p => p[0]);
  const minV = domain ? domain[0] : Math.min(...vals);
  const maxV = domain ? domain[1] : Math.max(...vals);
  const flat = maxV === minV;
  const rangeV = flat ? 1 : (maxV - minV);
  const minT = times[0], maxT = times[times.length - 1];
  const rangeT = maxT - minT || 1;
  const pad = 2;
  const points = pts.map(([t, v]) => {
    const x = pad + ((t - minT) / rangeT) * (w - 2 * pad);
    const frac = (flat && !domain) ? 0.5 : (v - minV) / rangeV;
    const y = (h - pad) - Math.max(0, Math.min(1, frac)) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline class="${cls}" points="${points}"/>
  </svg>`;
}

async function _fetchSparklines(startedAt, completedAt) {
  if (!startedAt) return;
  try {
    const qp = `start=${encodeURIComponent(startedAt)}` +
               (completedAt ? `&end=${encodeURIComponent(completedAt)}` : '');
    const data = await api(`/api/metrics/timeseries?${qp}`);
    if (data && data.available) {
      _rdSparklines = data;
      _renderSparklines();
    }
  } catch(e) {
    // Metrics unavailable — silently skip sparklines
  }
}

// Inject sparkline SVGs into the GPU tiles and vLLM cells that are
// currently rendered in the drawer. Safe to call multiple times.
// Append the current live snapshot value as the latest point of a series so the
// graph's right edge always matches the live headline number (the timeseries
// itself lags by its refresh interval). Synthetic +30s x so it sits just past
// the last historical sample; the sparkline x-axis is relative so the value is
// cosmetic. Returns a fresh values array (never mutates the cached series).
function _liveTip(series, liveVal) {
  const vals = (series && series.values) ? series.values.slice() : [];
  // For a completed run the panel is historical — the series IS the run's data,
  // so don't graft a "live" tip onto it.
  if (_rdLastSnap && _rdLastSnap._historical) return vals;
  if (vals.length && typeof liveVal === 'number' && isFinite(liveVal)) {
    vals.push([vals[vals.length - 1][0] + 30, liveVal]);
  }
  return vals;
}

// Build a synthetic "snapshot" from the run's historical timeseries (per-GPU +
// vLLM window averages / peaks) so a COMPLETED run's tiles show what it actually
// used during its window — not the live cluster state. Mirrors the live
// snapshot shape so renderRunDrawerMetrics consumes it unchanged.
function _histSnapFromSparklines() {
  const sp = _rdSparklines;
  if (!sp) return null;
  const avg  = (s) => (s && s.values && s.values.length) ? s.values.reduce((a,p)=>a+p[1],0)/s.values.length : null;
  const peak = (s) => (s && s.values && s.values.length) ? Math.max(...s.values.map(p=>p[1])) : null;
  const gpus = (sp.gpu_gfx_activity || []).map((s, i) => ({
    gpu_id: (s.metric && s.metric.gpu_id) ?? i,
    gpu_gfx_activity: avg(s),
    used_vram_pct:    avg((sp.gpu_vram_pct  || [])[i]),
    gpu_power_watts:  avg((sp.gpu_power_watts|| [])[i]),
    gpu_edge_temp_c:  avg((sp.gpu_temp       || [])[i]),
  }));
  const v0 = (k) => (sp[k] || [])[0];
  return {
    available: true, _historical: true, gpus,
    vllm: {
      running_requests: avg(v0('vllm_running')),
      waiting_requests: avg(v0('vllm_waiting')),
      kv_cache_pct:     peak(v0('vllm_kv_pct')),     // peak is the meaningful one
      generation_tps:   avg(v0('vllm_gen_tps')),
      prompt_tps:       avg(v0('vllm_prompt_tps')),
      ttft_p95_seconds: avg(v0('vllm_ttft_p95')),
    },
  };
}

function _renderSparklines() {
  if (!_rdSparklines) return;
  const sp = _rdSparklines;
  const snapV = (_rdLastSnap && _rdLastSnap.vllm) || {};
  const snapG = (_rdLastSnap && _rdLastSnap.gpus) || [];
  // GPU: one sparkline per stat (GFX, VRAM, Power, Temp) under its value.
  // [data-spk, timeseriesKey, liveValue, strokeClass, domain]. gpu_* timeseries
  // return one series PER GPU (index by tile); % metrics get a fixed [0,100].
  document.querySelectorAll('#rdGpus .gpu-tile').forEach((tile, idx) => {
    const g = snapG[idx] || {};
    const gpuSpk = [
      ['gfx',   'gpu_gfx_activity', g.gpu_gfx_activity,                          'spk-gfx',   [0,100]],
      ['vram',  'gpu_vram_pct',     g.used_vram_pct,                             'spk-vram',  [0,100]],
      ['power', 'gpu_power_watts',  g.gpu_power_watts,                           'spk-power', null],
      ['temp',  'gpu_temp',         (g.gpu_edge_temp_c ?? g.gpu_junction_temp_c),'spk-temp',  null],
    ];
    for (const [spk, tsKey, liveVal, cls, domain] of gpuSpk) {
      const slot = tile.querySelector(`.gpu-spk-slot[data-spk="${spk}"]`);
      if (!slot) continue;
      const vals = _liveTip((sp[tsKey] || [])[idx], liveVal);
      slot.innerHTML = vals.length >= 2
        ? `<div class="sparkline-wrap">${_sparklineSVG(vals, cls, 110, 18, domain)}</div>` : '';
    }
  });
  // vLLM: one sparkline per cell — [cellId, timeseriesKey, liveKey, cls, domain].
  // Percentage metrics get a fixed [0,100] domain so a pinned value reads true.
  for (const [cellId, tsKey, liveKey, cls, domain] of [
    ['rdv-running',   'vllm_running',    'running_requests',  'spk-run',  null],
    ['rdv-waiting',   'vllm_waiting',    'waiting_requests',  'spk-wait', null],
    ['rdv-kv',        'vllm_kv_pct',     'kv_cache_pct',      'spk-kv',   [0,100]],
    ['rdv-gentps',    'vllm_gen_tps',    'generation_tps',    'spk-tps',  null],
    ['rdv-prompttps', 'vllm_prompt_tps', 'prompt_tps',        'spk-ptps', null],
    ['rdv-ttft',      'vllm_ttft_p95',   'ttft_p95_seconds',  'spk-ttft', null],
  ]) {
    const cell = document.getElementById(cellId);
    if (!cell) continue;
    const parent = cell.closest('.vllm-cell');
    if (!parent) continue;
    const vals = _liveTip((sp[tsKey] || [])[0], snapV[liveKey]);
    let spkWrap = parent.querySelector('.sparkline-wrap');
    if (vals.length < 2) { if (spkWrap) spkWrap.remove(); continue; }
    if (!spkWrap) {
      spkWrap = document.createElement('div');
      spkWrap.className = 'sparkline-wrap';
      parent.appendChild(spkWrap);
    }
    spkWrap.innerHTML = _sparklineSVG(vals, cls, 110, 22, domain);
  }
}

async function rdToggleLogs(runName, step, logsId, link) {
  const el = document.getElementById(logsId);
  if (!el) return;
  if (el.style.display !== 'none') {
    el.style.display = 'none'; link.textContent = 'view logs'; return;
  }
  el.style.display = ''; link.textContent = 'hide logs';
  if (el.dataset.loaded) return;
  el.innerHTML = '<div style="color:var(--text-faint);font-size:10px;margin-top:6px">loading logs…</div>';
  try {
    const resp = await api(`/api/runs/${encodeURIComponent(runName)}/logs?task=${encodeURIComponent(step)}&tail=200`);
    el.innerHTML = `<pre>${esc(resp.logs || '(empty)')}</pre>`
                 + `<div style="color:var(--text-faint);font-size:10px;margin-top:4px">${resp.lines||0} lines · pod ${esc(resp.pod||'')}</div>`;
    el.dataset.loaded = '1';
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);font-size:11px">log fetch failed: ${esc(e.message)}</div>`;
  }
}

async function refreshRunDrawer() {
  if (!_rdName) return;
  // Two requests in parallel: run detail (Tekton) + live metrics snapshot
  const [detail, snap] = await Promise.all([
    api(`/api/runs/${encodeURIComponent(_rdName)}`).catch(e => ({_err: e.message})),
    api('/api/metrics/snapshot').catch(e => ({_err: e.message})),
  ]);
  if (detail._err) {
    document.getElementById('rdSub').innerHTML = `<span style="color:var(--red)">${esc(detail._err)}</span>`;
  } else {
    renderRunDrawerDetail(detail);
    const isTerminal = ['Succeeded','Failed','Cancelled','TimedOut'].includes(detail.phase);
    // Stop polling once the run is in a terminal state
    if (isTerminal) {
      if (_rdPollTimer) { clearInterval(_rdPollTimer); _rdPollTimer = null; }
    }
    // Sparklines (historical timeseries). In-flight: fetch + refresh every 20s.
    // Terminal: fetched + awaited in the metrics block below.
    const startedAt = detail.started_at || detail.created_at;
    if (!isTerminal) {
      if (startedAt && _rdSparklines === null) {
        _fetchSparklines(startedAt, null);
        if (_rdSparkTimer) clearInterval(_rdSparkTimer);
        _rdSparkTimer = setInterval(() => _fetchSparklines(startedAt, null), 20000);
      } else if (_rdSparklines) {
        _renderSparklines();
      }
    }
  }
  // Metrics panel: live snapshot for in-flight runs; the run's OWN historical
  // window (per-GPU + vLLM averages from its timeseries) for completed runs —
  // the live snapshot would show current cluster state, not this run.
  const _isTerminal = detail && !detail._err && ['Succeeded','Failed','Cancelled','TimedOut'].includes(detail.phase);
  let metricsSnap = snap, metricsOpts = {};
  if (_isTerminal) {
    const startedAt = detail.started_at || detail.created_at;
    if (_rdSparklines === null && startedAt) await _fetchSparklines(startedAt, detail.completed_at || null);
    const hist = _histSnapFromSparklines();
    if (hist) { metricsSnap = hist; metricsOpts = { historical: true }; }
  }
  // Session token totals + GPU energy (finalized for completed runs, else the
  // live in-flight deltas) — injected into whichever snap we render.
  if (metricsSnap && !metricsSnap._err && detail && !detail._err) {
    metricsSnap.vllm = metricsSnap.vllm || {};
    const sess = detail.session || {};
    const gen = (sess.total_gen_tokens != null) ? sess.total_gen_tokens : detail.live_session_gen_tokens;
    const prm = (sess.total_prompt_tokens != null) ? sess.total_prompt_tokens : detail.live_session_prompt_tokens;
    if (gen !== undefined) metricsSnap.vllm._live_session_gen = gen;
    if (prm !== undefined) metricsSnap.vllm._live_session_prompt = prm;
    const energyJ = (sess.gpu_energy_joules ?? sess.live_gpu_energy_joules);
    if (energyJ !== undefined && energyJ !== null) metricsSnap.vllm._gpu_energy_j = energyJ;
  }
  renderRunDrawerMetrics(metricsSnap, metricsOpts);
  // Compact one-row stats — only visible in the non-detailed layouts (CSS-gated).
  rdRenderCompactStats(metricsSnap, detail && !detail._err ? detail : null);
}

function _fmtEnergy(j) {
  if (j === null || j === undefined) return '—';
  // Joules to Wh: 1 Wh = 3600 J
  const wh = j / 3600;
  if (wh < 1) return `${j.toFixed(0)} J`;
  if (wh < 1000) return `${wh.toFixed(1)} Wh`;
  return `${(wh/1000).toFixed(2)} kWh`;
}

function renderRunDrawerDetail(d) {
  const s = d.session || null;
  const titleEl = document.getElementById('rdTitle');
  if (titleEl) {
    if (s && s.name) {
      titleEl.innerHTML = `${esc(s.name)} <span style="font-family:var(--mono);font-size:10px;color:var(--text-faint);margin-left:8px">${esc(d.name)}</span>`;
    } else {
      titleEl.textContent = d.name;
    }
  }
  // Update masthead chip with current phase
  updateRunChip(s?.name || d.name, d.phase);

  // ── Header strips: row 2 = time, row 3 = scope/estimate (folds in the old
  //    session section; fills the wide-screen header width). ──
  const _start = d.started_at || d.created_at;
  const _startMs = _start ? Date.parse(_start) : null;
  const _terminal = ['Succeeded','Failed','Cancelled','TimedOut'].includes(d.phase);
  // Denominator preference: live log-derived total > scope declared at trigger >
  // ingested total. The last one counts what has FINISHED, so using it mid-run
  // makes the header read "4/4 ok" on a 6-UC run — see _runScopeTotal.
  const ucCount = (d.progress && d.progress.total_ucs) || d.uc_scope_total
    || (s && s.uc_scope_total) || d.uc_total || (s && s.uc_total) || null;
  const p = d.progress || {};
  // Sample count (ensemble iterations per UC) — for the turn-record "iteration
  // X of N" labels. From the run's param, else the mode default.
  _rdSampleCount = parseInt((d.params || {})['sample-count'])
    || ({verification:3, explore:10, reproduce:1}[(d.params || {}).mode] || 1);
  _rdUcTotal = ucCount || null;   // for "UC N of M" labels in the prompts pane
  // Live pace: actual seconds/UC observed so far THIS run (elapsed ÷ completed).
  // It overrides the history/default estimate the moment ≥1 UC finishes — the
  // only signal that's accurate for the run actually in flight.
  const liveDone = p.completed || 0;
  const livePerUc = (!_terminal && liveDone > 0 && p.elapsed_seconds)
    ? (p.elapsed_seconds / liveDone) : null;
  const perUc = livePerUc || d.est_per_uc_seconds || 1800;
  const perUcLive = livePerUc != null;
  const estTotal = ucCount ? ucCount * perUc : null;
  // ETA: live = elapsed + remaining × pace; else started + total × per-UC.
  let etaMs = null;
  if (!_terminal && _startMs && estTotal) {
    etaMs = (perUcLive && ucCount != null)
      ? _startMs + (p.elapsed_seconds + (ucCount - liveDone) * perUc) * 1000
      : _startMs + estTotal * 1000;
  }
  const toSec = d.timeout_seconds || null;
  const killMs = (!_terminal && _startMs && toSec) ? _startMs + toSec * 1000 : null;
  const willExceed = !!(etaMs && killMs && etaMs > killMs);
  // Row 2 — time
  let timeBits = `${phaseHtml(d.phase)} · started ${esc(fmtTs(_start))} · ${_terminal?'':'elapsed '}${esc(fmtDuration(_start, d.completed_at))}`;
  if (!_terminal && etaMs) timeBits += ` · ETA ${esc(_fmtClock(etaMs))}${perUcLive?' <span style="color:var(--blue);font-size:9px">live</span>':''}`;
  if (!_terminal && toSec) timeBits += ` · time allowed <b style="color:var(--text);font-family:var(--mono)">${esc(_fmtDurShort(toSec))}</b><span class="rd-hstat-edit" title="Edit time allowed (failsafe — don't go past this long; never auto-extended)" onclick="editRunTimeout('${esc(d.name)}',${toSec})">✎</span>`;
  if (willExceed) timeBits += ` · <span style="color:var(--red);font-weight:500" title="At the current pace the analysis won't finish before the time allowed — extend it (✎) or it will be stopped">⚠ exceeds time allowed</span>`;
  if (_terminal && d.status_reason) timeBits += ` · <span style="color:var(--text-dim)">${esc(d.status_reason)}</span>`;
  document.getElementById('rdSub').innerHTML = timeBits;
  // Row 3 — scope / estimate
  const hs = [];
  // Which model produced this analysis — first-class in the header, not buried
  // in the Params dump. Chris: "I would like to see what model we are using for
  // the analysis." Verdicts are meaningless without it (the same corpus scored
  // by qwen3-32b and gpt-oss-120b are different measurements), and the fixture
  // work runs on a different model than the platform default, so guessing from
  // context is exactly what this removes. Topology rides the tooltip.
  {
    const _mdl = _runModelLabel(d) || ((s && s.trigger_payload && (s.trigger_payload.inference_model || '')) || '');
    if (_mdl) {
      const _topo = (d.params || {})['inference-topology'] || '';
      hs.push(`<span class="rd-hstat" title="${esc(_topo ? 'topology: ' + _topo : 'evaluation model')}">` +
        `<span class="l" style="text-transform:none">model</span><b>${esc(_mdl)}</b></span>`);
    }
  }
  if (ucCount != null) {
    // Outcome split from the ingested analysis — a partial failure (e.g. 31/32)
    // must read as exactly that, not as a blanket "Failed".
    if (typeof d.uc_succeeded === 'number' && typeof d.uc_total === 'number') {
      const failed = d.uc_failed || 0;
      hs.push(`<span class="rd-hstat"><span class="l" style="text-transform:none">UCs</span>` +
        `<b style="color:${failed > 0 ? 'var(--accent)' : 'var(--green)'}">${d.uc_succeeded}/${ucCount} ok</b>` +
        (failed > 0 ? ` <b style="color:var(--red)">${failed} fail</b>` : '') + `</span>`);
    } else {
      hs.push(`<span class="rd-hstat"><span class="l" style="text-transform:none">UCs</span><b>${ucCount}</b></span>`);
    }
  }
  if (!_terminal && ucCount != null) {
    const tag = perUcLive
      ? `<span style="color:var(--blue);font-size:9px;margin-left:3px" title="Live — actual pace observed this analysis (${liveDone}/${ucCount} done)">live</span>`
      : (d.est_per_uc_is_default ? `<span style="color:var(--text-faint);font-size:9px;margin-left:3px" title="No analysis history yet — adjusts as analyses complete + live once UCs finish">≈ default</span>` : '');
    hs.push(`<span class="rd-hstat"><span class="l">est/UC</span><b>~${esc(_fmtDurShort(perUc))}</b>${tag}</span>`);
    if (estTotal) hs.push(`<span class="rd-hstat"><span class="l">est total</span><b>~${esc(_fmtDurShort(estTotal))}</b></span>`);
  }
  if (_terminal && s && s.uc_total != null) {
    // Declared scope, not the ingested count. analysis_runs.total_ucs is written
    // per ingest BATCH, so a run whose results land in pieces briefly reports the
    // batch size as the run total: a finished 6-UC run read "3/3 done" — which
    // says "complete, 3 use cases" rather than "3 of 6 ingested so far". Same bug
    // #75 fixed for the pill and header; this site was missed because it only
    // renders for terminal runs, and the partial-ingest window is short.
    const doneDenom = _runScopeTotal(s) || s.uc_total;
    hs.push(`<span class="rd-hstat"><span class="l">done</span><b style="color:${(s.uc_failed||0)>0?'var(--accent)':'var(--green)'}">${s.uc_succeeded ?? '?'}/${doneDenom}</b>${(s.uc_failed||0)>0?` <span style="color:var(--red)">${s.uc_failed} fail</span>`:''}</span>`);
  }
  if (s && s.set_name) hs.push(`<span class="rd-hstat"><span class="l">set</span><b style="cursor:pointer;color:var(--accent)" onclick="switchView('usecases');setTimeout(()=>selectSet(${s.set_id}),100)">⊞ ${esc(s.set_name)}</b></span>`);
  else if (s && s.selection_mode) { const _ml={set:'Set',selection:'Selection',individual:'Individual UC',corpus:'Full corpus'}; hs.push(`<span class="rd-hstat"><span class="l">scope</span><b>${esc(_ml[s.selection_mode]||s.selection_mode)}</b></span>`); }
  if (s && s.category) hs.push(`<span class="rd-hstat"><span class="l">category</span><b>${esc(s.category)}</b></span>`);
  if (_terminal && s && s.wall_time_seconds) hs.push(`<span class="rd-hstat"><span class="l">wall</span><b>${esc(_fmtDurShort(s.wall_time_seconds))}</b></span>`);
  if (s && s.tags && s.tags.length) hs.push(`<span class="rd-hstat">${s.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}</span>`);
  document.getElementById('rdHeaderStats').innerHTML = hs.join('');

  // Per-UC progress (in-flight only). Server computes from
  // <run_dir>/run-progress.yaml the engine writes after each UC.
  const progEl = document.getElementById('rdProgressSection');
  if (d.progress && d.progress.total_ucs) {
    progEl.style.display = '';
    const p = d.progress;
    const total = p.total_ucs || 0;
    const succ = p.succeeded || 0;
    const fail = p.failed || 0;
    const completed = p.completed || 0;
    const active = (p.phase === 'running' && p.current_index > completed) ? 1 : 0;
    const succPct = total ? (succ/total*100) : 0;
    const failPct = total ? (fail/total*100) : 0;
    const actPct  = total ? (active/total*100) : 0;
    const ucName = p.current_uc_path ? p.current_uc_path.split('/').slice(-1)[0].replace(/\.yaml$/,'') : null;
    document.getElementById('rdProgress').innerHTML = `
      <div class="uc-progress-counter">
        <span class="big">${completed} <span style="color:var(--text-faint);font-size:13px">/ ${total}</span></span>
        <span class="sub"><span class="succ">${succ} ok</span> · <span class="fail">${fail} failed</span> · ${Math.round(succPct + failPct)}% done</span>
      </div>
      <div class="uc-progress-bar" title="${succ} succeeded / ${fail} failed / ${active ? '1 active' : '0 active'} of ${total}">
        <span class="seg-success" style="width:${succPct}%"></span>
        <span class="seg-failed"  style="width:${failPct}%"></span>
        <span class="seg-active"  style="width:${actPct}%"></span>
      </div>
      ${ucName && p.phase === 'running'
        ? `<div class="uc-progress-current">running <b>${esc(ucName)}</b> · UC ${p.current_index} of ${total} · ${Math.round(p.elapsed_seconds||0)}s elapsed</div>`
        : `<div class="uc-progress-current" style="color:var(--text-faint)">${esc(p.phase || 'unknown')} · ${Math.round(p.elapsed_seconds||0)}s elapsed</div>`}
    `;
  } else {
    progEl.style.display = 'none';
  }

  // Session block — only render if we have a session row
  // Session section folded into the header strips above — keep it hidden.
  document.getElementById('rdSessionSection').style.display = 'none';
  if (s && s.description) {
    // Description is the one free-text field with no header home — show it as a
    // subtle full-width line under the header stats.
    const hsEl = document.getElementById('rdHeaderStats');
    hsEl.innerHTML += `<span class="rd-hstat" style="flex-basis:100%;color:var(--text-faint);font-size:10px">${esc(s.description)}</span>`;
  }

  // Task ladder
  const tasksEl = document.getElementById('rdTasks');
  const tasks = d.tasks || [];
  if (!tasks.length) {
    tasksEl.innerHTML = '<div class="empty" style="font-size:11px">No tasks reported yet.</div>';
  } else {
    tasksEl.innerHTML = '';
    tasks.forEach((t, idx) => {
      const phaseCls = (t.phase||'pending').toLowerCase();
      const marker = phaseCls === 'succeeded' ? '✓'
                   : phaseCls === 'failed'    ? '✗'
                   : phaseCls === 'running' || phaseCls === 'started' || phaseCls === 'pending' ? '●'
                   : '○';
      const dur = t.started_at ? fmtDuration(t.started_at, t.completed_at) : '—';
      const isFailed = phaseCls === 'failed';
      const stepName = t.step || t.name;
      const logsId = `rd-logs-${idx}`;
      const row = document.createElement('div');
      row.className = 'task-row';
      row.style.gridTemplateColumns = '18px 1fr 90px 80px';
      row.innerHTML = `
        <div class="task-marker ${phaseCls}">${marker}</div>
        <div>
          <div class="task-name">${esc(stepName)}</div>
          ${t.message && !isFailed ? `<div class="task-step-sub">${esc(t.message.slice(0,140))}</div>` : ''}
        </div>
        <div class="task-time">${esc(t.phase || 'pending')}</div>
        <div class="task-time">${esc(dur)}</div>
        ${isFailed ? `
          <div class="task-failure-block">
            <div><b>Failure:</b> ${esc(t.message || 'no message')}
              <span class="task-failure-toggle" onclick="rdToggleLogs('${esc(_rdName)}','${esc(stepName)}','${logsId}', this)">view logs</span>
            </div>
            <div id="${logsId}" style="display:none"></div>
          </div>` : ''}`;
      tasksEl.appendChild(row);
    });
  }

  // Params
  const pe = document.getElementById('rdParams');
  pe.innerHTML = '';
  Object.entries(d.params || {}).forEach(([k,v]) => {
    pe.innerHTML += `<div class="kv-label">${esc(k)}</div><div class="kv-val" style="word-break:break-all">${esc(v)}</div>`;
  });

  // Terminal run → append the per-UC analysis audit (built once). Shows a partial
  // run's evaluated/missing UCs in situ, not only on the empty Runs view.
  if (_terminal) _ensureRunAuditSection();
}

function _fmtN(v, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString(undefined, {maximumFractionDigits: digits ?? 1});
}

function renderRunDrawerMetrics(snap, opts) {
  _rdLastSnap = snap;   // cache so _renderSparklines can append live tips
  // Label the sections: live cluster state (in-flight) vs the run's own window
  // averages (completed). Historical values come from the timeseries.
  const _hist = !!(opts && opts.historical) || !!(snap && snap._historical);
  const _gm = document.getElementById('rdGpuMode'); if (_gm) _gm.textContent = _hist ? '(during analysis)' : '(live)';
  const _vm = document.getElementById('rdVllmMode'); if (_vm) _vm.textContent = _hist ? 'during analysis' : 'live';
  const gpusEl = document.getElementById('rdGpus');
  const vllmEl = document.getElementById('rdVllm');
  if (snap._err) {
    gpusEl.innerHTML = `<div class="empty" style="font-size:11px;color:var(--red)">metrics: ${esc(snap._err)}</div>`;
    vllmEl.innerHTML = '';
    return;
  }
  if (snap && !snap.available) {
    gpusEl.innerHTML = `<div class="empty" style="font-size:11px;color:var(--text-faint)">metrics unavailable: ${esc(snap.reason||'')}</div>`;
    vllmEl.innerHTML = '';
    return;
  }

  // ── GPU tiles ───────────────────────────────────────────────
  const gpus = (snap && snap.gpus) || [];
  const newGpuValues = {};
  let gpuChanged = false;
  if (!gpus.length) {
    gpusEl.innerHTML = '<div class="empty" style="font-size:11px;color:var(--text-faint)">No GPU metrics yet (exporter scrape pending).</div>';
  } else {
    gpusEl.innerHTML = '';
    gpus.forEach((g, idx) => {
      const gid = g.gpu_id ?? idx;
      const gfx = g.gpu_gfx_activity ?? null;
      const vramPct = g.used_vram_pct ?? null;
      const power = g.gpu_power_watts ?? null;
      const temp = g.gpu_edge_temp_c ?? g.gpu_junction_temp_c ?? null;
      newGpuValues[gid] = {gfx, vramPct, power, temp};
      const prev = _rdLastGpuValues[gid] || {};
      ['gfx','vramPct','power','temp'].forEach(k => {
        if (prev[k] !== undefined && prev[k] !== newGpuValues[gid][k]) gpuChanged = true;
      });
      const gfxCls = gfx === null ? '' : (gfx > 90 ? 'crit' : gfx > 70 ? 'high' : '');
      const vCls   = vramPct === null ? '' : (vramPct > 95 ? 'crit' : vramPct > 80 ? 'high' : '');
      const tCls   = temp === null ? '' : (temp > 90 ? 'hot' : temp > 80 ? 'warn' : '');
      const idBase = `gpu${idx}`;
      const tile = document.createElement('div');
      tile.className = 'gpu-tile';
      // 2×2 grid: GFX | VRAM on top, Power | Temp below. Each cell = value then
      // its own sparkline graph, sharing half the row.
      tile.innerHTML = `
        <div class="gpu-tile-header">
          <span class="gpu-tile-id">GPU ${esc(g.gpu_id ?? '?')}${g.model ? ' · '+esc(g.model) : ''}</span>
          <span class="gpu-tile-node">${esc(g.node || '')}</span>
        </div>
        <div class="gpu-tile-stats2">
          <div class="gpu-stat">
            <div class="gpu-stat-label">GFX</div>
            <div class="gpu-stat-value" id="${idBase}-gfx">${_fmtN(gfx, 0)}<span class="vllm-cell-unit">%</span></div>
            <div class="gpu-spk-slot" data-spk="gfx"></div>
          </div>
          <div class="gpu-stat">
            <div class="gpu-stat-label">VRAM</div>
            <div class="gpu-stat-value" id="${idBase}-vram">${_fmtN(vramPct, 0)}<span class="vllm-cell-unit">%</span></div>
            <div class="gpu-spk-slot" data-spk="vram"></div>
          </div>
          <div class="gpu-stat">
            <div class="gpu-stat-label">Power</div>
            <div class="gpu-stat-value" id="${idBase}-pwr">${_fmtN(power, 0)}<span class="vllm-cell-unit">W</span></div>
            <div class="gpu-spk-slot" data-spk="power"></div>
          </div>
          <div class="gpu-stat">
            <div class="gpu-stat-label">Temp</div>
            <div class="gpu-stat-value ${tCls}" id="${idBase}-tmp">${_fmtN(temp, 0)}<span class="vllm-cell-unit">°C</span></div>
            <div class="gpu-spk-slot" data-spk="temp"></div>
          </div>
        </div>`;
      gpusEl.appendChild(tile);
      // Flash any value that changed from the previous poll
      if (prev.gfx     !== undefined && prev.gfx     !== gfx)     _flashChanged(`${idBase}-gfx`);
      if (prev.vramPct !== undefined && prev.vramPct !== vramPct) _flashChanged(`${idBase}-vram`);
      if (prev.power   !== undefined && prev.power   !== power)   _flashChanged(`${idBase}-pwr`);
      if (prev.temp    !== undefined && prev.temp    !== temp)    _flashChanged(`${idBase}-tmp`);
    });
  }
  // First render: don't claim "no change" — start the clock now
  if (Object.keys(_rdLastGpuValues).length === 0 || gpuChanged) {
    _rdGpuLastChange = Math.floor(Date.now()/1000);
  }
  _rdLastGpuValues = newGpuValues;

  // ── vLLM aggregates ─────────────────────────────────────────
  const v = (snap && snap.vllm) || {};

  // Session token deltas: prefer server-supplied values (persisted baseline
  // captured in run_sessions at trigger time — survives page reload). Fall
  // back to client-side baseline when the server didn't include them
  // (e.g. legacy run with no run_sessions row).
  const sessGenTokens = (v._live_session_gen !== undefined && v._live_session_gen !== null)
    ? v._live_session_gen
    : (function(){
        const b = _rdTokenBaseline.gen;
        const c = v.gen_tokens_total;
        if (c === null || c === undefined) return null;
        if (b === null) { _rdTokenBaseline.gen = c; return 0; }
        if (c < b)      { _rdTokenBaseline.gen = c; return 0; }
        return c - b;
      })();
  const sessPromptTokens = (v._live_session_prompt !== undefined && v._live_session_prompt !== null)
    ? v._live_session_prompt
    : (function(){
        const b = _rdTokenBaseline.prompt;
        const c = v.prompt_tokens_total;
        if (c === null || c === undefined) return null;
        if (b === null) { _rdTokenBaseline.prompt = c; return 0; }
        if (c < b)      { _rdTokenBaseline.prompt = c; return 0; }
        return c - b;
      })();
  v.session_gen_tokens    = sessGenTokens;
  v.session_prompt_tokens = sessPromptTokens;

  const vKeys = [
    ['running_requests',       'Running',          'rdv-running',  0, ''],
    ['waiting_requests',       'Waiting',          'rdv-waiting',  0, ''],
    ['kv_cache_pct',           'KV cache',         'rdv-kv',       0, '%'],
    ['generation_tps',         'Gen tokens/s',     'rdv-gentps',   1, ''],
    ['prompt_tps',             'Prompt tokens/s',  'rdv-prompttps',1, ''],
    ['ttft_p95_seconds',       'TTFT p95',         'rdv-ttft',     2, 's'],
    ['session_gen_tokens',     'Gen tokens (session)',    'rdv-sgen',  0, ''],
    ['session_prompt_tokens',  'Prompt tokens (session)', 'rdv-sprm',  0, ''],
    // Bottom-right: total GPU energy used by the run (custom formatter).
    ['_gpu_energy_j',          'GPU energy',       'rdv-energy',   0, '', (j)=> (j==null ? '–' : _fmtEnergy(j))],
  ];
  // Degradation: track consecutive null polls per key. Once a metric has been
  // null for RD_VLLM_NULL_HIDE_AFTER polls running (a backend that never reports
  // it) drop its cell; collapse all the dropped ones into a single note — mirrors
  // how the sparklines already hide when there's nothing to plot (:978). Historical
  // (completed-run) snapshots carry real values, so they don't trip this.
  let _vllmHidden = 0;
  const _visVKeys = vKeys.filter(([k]) => {
    const val = v[k];
    const isNull = (val === null || val === undefined || Number.isNaN(val));
    _rdVllmNullStreak[k] = isNull ? (_rdVllmNullStreak[k] || 0) + 1 : 0;
    if (_rdVllmNullStreak[k] >= RD_VLLM_NULL_HIDE_AFTER) { _vllmHidden++; return false; }
    return true;
  });
  vllmEl.innerHTML = _visVKeys.map(([k,label,id,prec,unit,fmt]) => `
    <div class="vllm-cell"><div class="vllm-cell-label">${label}</div>
      <div class="vllm-cell-value" id="${id}">${fmt ? fmt(v[k]) : _fmtN(v[k], prec)}${unit ? `<span class="vllm-cell-unit">${unit}</span>` : ''}</div></div>`).join('')
    + (_vllmHidden ? `<div class="vllm-cell" style="opacity:0.65;"><div class="vllm-cell-label">—</div><div class="vllm-cell-value" style="font-size:10px;line-height:1.3;">${_vllmHidden} metric${_vllmHidden===1?'':'s'} not reported by this backend</div></div>` : '');
  let vllmChanged = false;
  vKeys.forEach(([k,_l,id]) => {
    const prev = _rdLastVllmValues[k];
    if (prev !== undefined && prev !== v[k]) { _flashChanged(id); vllmChanged = true; }
    _rdLastVllmValues[k] = v[k];
  });
  if (Object.keys(_rdLastVllmValues).length === 0 || vllmChanged) {
    _rdVllmLastChange = Math.floor(Date.now()/1000);
  }
  updateFreshnessLabels();

  // Re-inject sparklines: this function wipes #rdGpus / vLLM cell
  // innerHTML on every poll (every 3s for an in-flight run), which
  // destroys the SVGs injected by _renderSparklines(). The timeseries
  // is only re-fetched every 60s, so without this the sparklines flash
  // in for one poll after each fetch then vanish for ~57s ("comes and
  // goes while running"). _rdSparklines is cached between fetches, so
  // re-rendering it here keeps them visible continuously. No-op when no
  // data is cached yet (completed runs render once and already persist).
  _renderSparklines();
}

async function loadNewRunDefaults(forcedSubpath) {
  // Fetch current spec/corpus/inference state + detect UC subpath, populate fields.
  // forcedSubpath: if set (e.g. when called from "Run set"), use that and skip auto-detect.
  const detectedEl = document.getElementById('nrCorpusDetected');
  detectedEl.textContent = '';
  try {
    const sourcesResp = await api('/api/sources').catch(() => ({sources:{}}));
    const sources = sourcesResp.sources || {};
    const spec    = sources.spec      || {};
    const corpus  = sources.corpus    || {};
    const inf     = sources.inference || {};
    // Engine default for the new-run override picker comes from the Inference
    // source (the path the pipeline actually reads), not model_defaults.
    _engineDefaultLabel = inf.model || inf.endpoint || 'project inference default';
    _populateOverrideSel('nrModelSel', '__engine__');
    // Spec side: legacy single-source shows repo URL/branch inputs; multi-source
    // mode (post-M11a) hides those and renders a read-only source list instead —
    // editing happens in Config → Managed repos.
    const specLegacy = document.getElementById('nrSpecLegacyWrap');
    const specMulti  = document.getElementById('nrSpecSourcesWrap');
    if (spec.multi_source && Array.isArray(spec.sources) && spec.sources.length) {
      specLegacy.style.display = 'none';
      specMulti.style.display = '';
      const grid = document.getElementById('nrSpecSources');
      grid.innerHTML = '';
      spec.sources.forEach(s => {
        const ns = s.namespace || '?';
        const label = document.createElement('label');
        label.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;min-width:0;';
        label.innerHTML = `<input type="checkbox" class="nr-spec-ns" value="${esc(ns)}" checked style="width:auto;height:auto;accent-color:var(--accent);flex-shrink:0;"> <span style="font-weight:500;flex-shrink:0;">${esc(ns)}</span> <span style="color:var(--text-faint);font-size:10px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.repo_url||'')}</span>`;
        grid.appendChild(label);
      });
    } else {
      specLegacy.style.display = '';
      specMulti.style.display = 'none';
      document.getElementById('nrSpecRepo').value    = spec.repo_url      || '';
      document.getElementById('nrSpecBranch').value  = spec.repo_branch   || '';
    }
    // Corpus legacy URL/branch (the multi-source picker is handled separately
    // by _populateCorpusSourcesPicker — that toggles nrCorpusLegacyWrap)
    document.getElementById('nrCorpusRepo').value  = corpus.repo_url    || '';
    document.getElementById('nrCorpusBranch').value= corpus.repo_branch || '';
    const nrSel = document.getElementById('nrModelSel');
    if (nrSel && !nrSel.value) {
      // No user override — fall back to project default evaluation model
      try {
        const defaults = await api('/api/model-defaults');
        if (defaults.evaluation) nrSel.value = String(defaults.evaluation);
      } catch(_) { /* ignore */ }
    }

    if (forcedSubpath !== undefined) {
      document.getElementById('nrCorpusSubpath').value = forcedSubpath || '';
      document.getElementById('nrCorpusSubpath').placeholder = '';
    } else if (corpus.multi_source) {
      // Multi-source corpus: per-source root_path is baked into the projected
      // ConfigMap; the legacy uc-subpath param is ignored. Leave the field
      // editable for the rare advanced override but make the placeholder
      // honest so nothing looks "stuck loading".
      document.getElementById('nrCorpusSubpath').value = '';
      document.getElementById('nrCorpusSubpath').placeholder = '(multi-source — per-source root_path applies; leave blank)';
      detectedEl.innerHTML = `· <span style="color:var(--text-faint)">multi-source corpus (${(corpus.sources||[]).length} source(s))</span>`;
    } else {
      const detect = await api('/api/sources/corpus/uc-subpath').catch(() => ({}));
      document.getElementById('nrCorpusSubpath').value = detect.detected || '';
      document.getElementById('nrCorpusSubpath').placeholder = '';
      if (detect.detected) {
        detectedEl.textContent = `· auto-detected ${detect.detected}/`;
      } else if (detect.corpus_dir_exists === false) {
        detectedEl.innerHTML = `· <span style="color:var(--red)">corpus not cloned yet</span>`;
      } else {
        detectedEl.innerHTML = `· <span style="color:var(--text-faint)">no dav/ or use-cases/ found</span>`;
      }
    }
    await _populateCorpusSourcesPicker();
  } catch (e) {
    toast('Could not load analysis defaults: ' + e.message, true);
  }
}

// ADR-007 / M11b: populate the per-run corpus source multi-select from
// managed_repos. Hidden in legacy single-source mode (zero rows).
async function _populateCorpusSourcesPicker() {
  const wrap = document.getElementById('nrCorpusSourcesWrap');
  const legacy = document.getElementById('nrCorpusLegacyWrap');
  const grid = document.getElementById('nrCorpusSources');
  if (!wrap || !grid) return;
  try {
    const resp = await api('/api/repos?role=corpus').catch(() => ({repos:[]}));
    const rows = (resp.repos || []).filter(r => (r.roles || []).includes('corpus'));
    if (!rows.length) {
      wrap.style.display = 'none';
      if (legacy) legacy.style.display = '';
      grid.innerHTML = '';
      return;
    }
    wrap.style.display = '';
    if (legacy) legacy.style.display = 'none';   // multi-source supersedes legacy fields
    grid.innerHTML = '';
    rows.forEach(r => {
      const ns = r.namespace || r.handle || '?';
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;min-width:0;';
      label.innerHTML = `<input type="checkbox" class="nr-corpus-ns" value="${esc(ns)}" checked style="width:auto;height:auto;accent-color:var(--accent);flex-shrink:0;"> <span style="font-weight:500;flex-shrink:0;">${esc(ns)}</span> <span style="color:var(--text-faint);font-size:10px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(r.repo_url||'')}</span>`;
      grid.appendChild(label);
    });
  } catch (e) {
    wrap.style.display = 'none';
    if (legacy) legacy.style.display = '';
  }
}

// Returns null when all sources are selected (server default = include all),
// otherwise a list of namespaces. Also returns null in legacy single-source
// mode (picker hidden).
function _collectSelectedCorpusNamespaces() {
  const wrap = document.getElementById('nrCorpusSourcesWrap');
  if (!wrap || wrap.style.display === 'none') return null;
  const boxes = Array.from(document.querySelectorAll('#nrCorpusSources .nr-corpus-ns'));
  if (!boxes.length) return null;
  const selected = boxes.filter(b => b.checked).map(b => b.value);
  if (selected.length === boxes.length) return null;
  return selected;
}

// Spec analog. Same null-means-all contract.
function _collectSelectedSpecNamespaces() {
  const wrap = document.getElementById('nrSpecSourcesWrap');
  if (!wrap || wrap.style.display === 'none') return null;
  const boxes = Array.from(document.querySelectorAll('#nrSpecSources .nr-spec-ns'));
  if (!boxes.length) return null;
  const selected = boxes.filter(b => b.checked).map(b => b.value);
  if (selected.length === boxes.length) return null;
  return selected;
}

// When the New Ingestion modal is opened with an explicit UC selection (Run a Scoping Set,
// Test eval from a single UC, etc.), submitNewRun reads this to pass uc_handles
// / uc_uuids through to the engine — engine-side filter scopes the run to
// exactly these UCs instead of the whole directory. Cleared on close.
let _pendingRunFilter = null;   // { handles: string[], uuids: string[], managed: string[] }
let _pendingRunLineage = null;  // R2: { set_id?, set_name?, selection_mode: 'set'|'selection'|'individual'|'corpus' }
const _selectedUCs = new Set(); // UUIDs of UCs checked in the list for batch ops
let _lastVisibleUUIDs = [];      // UUIDs currently visible in the list (post-filter) — for Select-all
// Select-all toggles the whole visible/filtered set: select all if any are unselected, else clear.
function _toggleSelectAllUCs() {
  const vis = _lastVisibleUUIDs;
  if (!vis.length) { try { toast('No use cases to select', true); } catch {} return; }
  const allSelected = vis.every(id => _selectedUCs.has(id));
  if (allSelected) vis.forEach(id => _selectedUCs.delete(id));
  else vis.forEach(id => _selectedUCs.add(id));
  renderUCList();
}

// Apply a Scoping Set to the New Ingestion modal: sets _pendingRunFilter/_pendingRunLineage and
// returns the {subpath, banner} to drive the corpus field + banner. Empty setId
// = full corpus (no filter). Shared by the inline selector + openNewRun.
let _runEstimate = null;   // cached {est_per_uc_seconds, is_default, buffer}
async function _applySetToNewRun(setId) {
  if (!setId) {
    _pendingRunFilter = null;
    _pendingRunLineage = { selection_mode: 'corpus' };
    return { subpath: undefined, banner: null };
  }
  if (setId === '__stale__') {
    // Synthetic "Stale / un-ingested" scope = UCs with no current evaluation (un-evaluated
    // OR stale). Resolved from the latest-eval-per-UC read; engine-filtered to exactly these.
    let needs = [];
    try {
      const r = await api('/api/results/uc-latest');
      needs = (r.ucs || []).filter(u => !u.evaluated || u.stale).map(u => u.uc_uuid);
    } catch (_) {}
    _pendingRunFilter = { handles: [], uuids: needs.slice(), managed: needs.slice() };
    _pendingRunLineage = { selection_mode: 'selection' };
    return { subpath: undefined,
             banner: `Stale / un-ingested — ${needs.length} use case${needs.length === 1 ? '' : 's'} needing evaluation · engine-filtered to exactly these.` };
  }
  if (setId === '__unassigned__') {
    // Synthetic "Unassigned" scope = managed UCs in no Scoping Set. Resolve to a UC
    // selection (engine-filtered to exactly these).
    if (!(allUCs || []).length) { try { const r = await api('/api/use-cases'); allUCs = r.use_cases || []; } catch (_) {} }
    const un = (allUCs || []).filter(u => !u.set_ids || !u.set_ids.length);
    const uuids = un.map(u => u.uuid);
    _pendingRunFilter = { handles: [], uuids: uuids.slice(), managed: uuids.slice() };
    _pendingRunLineage = { selection_mode: 'selection' };
    return { subpath: undefined,
             banner: `Unassigned — ${uuids.length} use case${uuids.length === 1 ? '' : 's'} in no Scoping Set · engine-filtered to exactly these.` };
  }
  const set = (allSets || []).find(s => String(s.id) === String(setId));
  try {
    const setData = await api(`/api/sets/${setId}`);
    const subpathInfo = await api(`/api/sets/${setId}/corpus-subpath`);
    _pendingRunFilter = _filterFromSetMembers(setData.members || []);
    _pendingRunLineage = { set_id: setId, set_name: set?.name, selection_mode: 'set' };  // raw id — Number('__all__') would be NaN
    // Both kinds run: corpus UCs are engine-filtered in the corpus; managed UCs
    // are fetched from the console API at run start (no push/promote needed).
    const c = subpathInfo.corpus_count || 0, m = subpathInfo.managed_count || 0;
    const ucCount = c + m;
    // Suggest a "time allowed" (failsafe) from the data-driven per-UC estimate.
    let estNote = '';
    try {
      if (!_runEstimate) _runEstimate = await api('/api/runs/estimate');
      if (_runEstimate && ucCount > 0) {
        const perUc = _runEstimate.est_per_uc_seconds || 1800;
        const sec = ucCount * perUc + (_runEstimate.failsafe_buffer_seconds || 7200);
        const taEl = document.getElementById('nrTimeAllowed');
        if (taEl) taEl.value = (sec/3600).toFixed(1);
        estNote = ` · est ~${(ucCount*perUc/3600).toFixed(1)}h (~${Math.round(perUc/60)}m/UC${_runEstimate.est_per_uc_is_default ? ', adjusts as runs complete' : ''})`;
      }
    } catch(_) {}
    const bits = [];
    if (m) bits.push(`${m} managed (fetched from API)`);
    if (c) bits.push(`${c} corpus`);
    const banner = `Running Set "${esc(set?.name || setId)}" — ${bits.join(' + ') || 'no UCs'} · engine-filtered to exactly these.${estNote}`;
    return { subpath: subpathInfo.subpath || undefined, banner };
  } catch(e) {
    _pendingRunFilter = null; _pendingRunLineage = { selection_mode: 'corpus' };
    return { subpath: undefined, banner: null };
  }
}
function _populateNrSetSel(selectedId) {
  const sel = document.getElementById('nrSetSel');
  if (!sel) return;
  sel.innerHTML = '<option value="">Full corpus (all corpus UCs, unfiltered)</option>' +
    `<option value="__stale__"${String(selectedId)==='__stale__'?' selected':''}>Stale / un-ingested (use cases needing evaluation)</option>` +
    `<option value="__unassigned__"${String(selectedId)==='__unassigned__'?' selected':''}>Unassigned (use cases in no Scoping Set)</option>` +
    (allSets || []).map(s =>
      `<option value="${s.id}"${String(s.id)===String(selectedId)?' selected':''}>${esc(s.name)}${s.is_default?' (default)':''}${s.member_count!=null?` — ${s.member_count} UC${s.member_count===1?'':'s'}`:''}</option>`
    ).join('');
  // Null-safe ('' = Full corpus); the synthetic set's id is the truthy
  // '__all__' sentinel, so no falsy-0 special-casing is needed anymore.
  sel.value = (selectedId !== null && selectedId !== undefined && selectedId !== '') ? String(selectedId) : '';
}
document.getElementById('nrSetSel')?.addEventListener('change', async function() {
  const { subpath, banner } = await _applySetToNewRun(this.value);
  const bannerEl = document.getElementById('newRunBanner');
  if (banner) { bannerEl.innerHTML = banner; bannerEl.style.display = ''; } else { bannerEl.style.display = 'none'; }
  await loadNewRunDefaults(subpath);
});

// Re-run an existing run: open New Ingestion pre-filled with the same Set (UC
// selection) + category + a "Rerun:" name, for the operator to review + trigger.
async function rerunRun(name) {
  // Rerun must reproduce the original run regardless of UI state, list
  // hydration, or Tekton PipelineRun pruning. Source of truth is the
  // server-stored trigger payload (rerun-config); the modal does not open
  // until it has loaded — never "defaults with a Rerun name".
  let rc = null;
  try { rc = await api(`/api/runs/${encodeURIComponent(name)}/rerun-config`); }
  catch(e) { toast('Could not load the original analysis configuration — not opening Re-analyze', true); return; }
  const cfg = rc.config || null;            // exact RunTriggerIn payload (durable)
  const P   = rc.params || (allRuns || []).find(x => x.name === name)?.params || {};
  const sess = rc.session || {};
  if (!cfg && !Object.keys(P).length) {
    toast('Original configuration unavailable (pre-upgrade analysis, PipelineRun pruned) — opening defaults', true);
  }
  let det = null;   // legacy fallback only: time-allowed lives in the payload now
  if (!cfg) { try { det = await api(`/api/runs/${encodeURIComponent(name)}`); } catch(_) {} }

  // UC scope: EXACT replay — handles/uuids/managed + subpath verbatim
  // (re-deriving from the live Set diverged: normalized handles, '.' subpath,
  // recomputed timeout). Set resolved for display/provenance only, including
  // the synthetic "__all__" set whose lineage persists as set_id NULL.
  const selMode = cfg?.selection_mode ?? sess.selection_mode ?? null;
  const setName = cfg?.set_name ?? sess.set_name ?? null;
  let rerunSetId = (selMode === 'set') ? (cfg?.set_id ?? sess.set_id ?? null) : null;
  if (selMode === 'set' && rerunSetId == null && setName) {
    if (!allSets || !allSets.length) { try { await loadSets(); } catch(_) {} }
    const byName = (allSets || []).find(s => s.name === setName);
    if (byName) rerunSetId = byName.id;
  }
  let filter = null;
  if (cfg) {
    const f = { handles: cfg.uc_handles || [], uuids: cfg.uc_uuids || [],
                managed: cfg.managed_uc_uuids || [] };
    if (f.handles.length || f.uuids.length || f.managed.length) filter = f;
  } else {
    const f = {
      handles: P['uc-handles']       ? P['uc-handles'].split(',')       : [],
      uuids:   P['uc-uuids']         ? P['uc-uuids'].split(',')         : [],
      managed: P['managed-uc-uuids'] ? P['managed-uc-uuids'].split(',') : [],
    };
    if (f.handles.length || f.uuids.length || f.managed.length) filter = f;
  }
  const lineage = { set_id: rerunSetId, set_name: setName,
                    selection_mode: selMode || (filter ? 'selection' : 'corpus') };
  const subpath = cfg ? (cfg.corpus_subpath || undefined) : (P['corpus-uc-subpath'] || undefined);
  // Explicit subpath suppresses openNewRun's set re-application (the
  // divergence source); _populateNrSetSel still shows the Scoping Set for context.
  await openNewRun(undefined, subpath, filter, undefined, lineage);

  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el && v !== undefined && v !== null && v !== '') el.value = v;
  };
  const mode = cfg?.mode || P.mode;
  if (mode) {
    const m = document.getElementById('nrMode');
    if (m) { m.value = mode; m.dispatchEvent(new Event('change')); }
  }
  setVal('nrSampleCount',   cfg ? cfg.sample_count        : P['sample-count']);
  setVal('nrCorpusSubpath', cfg ? cfg.corpus_subpath      : P['corpus-uc-subpath']);
  setVal('nrCorpusRepo',    cfg ? cfg.corpus_repo_url     : P['consumer-corpus-repo-url']);
  setVal('nrCorpusBranch',  cfg ? cfg.corpus_repo_branch  : P['consumer-corpus-repo-branch']);
  setVal('nrSpecRepo',      cfg ? cfg.spec_repo_url       : P['consumer-spec-repo-url']);
  setVal('nrSpecBranch',    cfg ? cfg.spec_repo_branch    : P['consumer-spec-repo-branch']);
  const halt = document.getElementById('nrHaltOnError');
  if (halt) halt.checked = cfg ? !!cfg.halt_on_error : (P['halt-on-error'] === 'true');
  // Model: select the registered model matching what actually ran.
  const ep = cfg?.inference_endpoint || P['inference-endpoint'];
  const mid = cfg?.inference_model || P['inference-model'];
  if (ep && mid) {
    const m = (_reviewModels || []).find(x => x.endpoint_url === ep && x.model_id === mid);
    const sel = document.getElementById('nrModelSel');
    if (m && sel && [...sel.options].some(o => o.value === String(m.id))) sel.value = String(m.id);
  }
  // Source-namespace narrowing (absent/null = all sources, leave defaults).
  const restoreNs = (list, selector) => {
    if (!list || !list.length) return;
    const want = new Set(list);
    document.querySelectorAll(selector).forEach(b => { b.checked = want.has(b.value); });
  };
  restoreNs(cfg ? cfg.corpus_namespaces : (P['corpus-namespaces'] ? P['corpus-namespaces'].split(',') : null),
            '#nrCorpusSources .nr-corpus-ns');
  restoreNs(cfg ? cfg.spec_namespaces : (P['spec-namespaces'] ? P['spec-namespaces'].split(',') : null),
            '#nrSpecSources .nr-spec-ns');
  // Failsafe time-allowed + session metadata.
  const toSec = cfg?.time_allowed_seconds ?? det?.timeout_seconds;
  if (toSec) setVal('nrTimeAllowed', String(+(toSec / 3600).toFixed(2)));
  const nameEl = document.getElementById('nrSessionName');
  if (nameEl) nameEl.value = `Rerun: ${sess.name || name}`;
  setVal('nrDescription', cfg?.description ?? sess.description);
  const cat = document.getElementById('nrCategory');
  const catV = cfg?.category || sess.category;
  if (cat && catV && [...cat.options].some(o => o.value === catV)) cat.value = catV;
  const title = document.getElementById('newRunTitle');
  if (title) title.textContent = 'Rerun';
}

async function openNewRun(banner, subpath, ucFilter, branchOverride, lineage) {
  document.getElementById('newRunModal').classList.add('open');
  document.getElementById('nrStatus').textContent = '';
  document.getElementById('submitNewRun').disabled = false;
  document.getElementById('newRunTitle').textContent = 'New analysis';
  document.getElementById('nrSessionName').value = '';
  document.getElementById('nrDescription').value = '';
  _pendingRunFilter = ucFilter || null;
  _pendingRunLineage = lineage || null;
  const bannerEl = document.getElementById('newRunBanner');

  // Populate the inline Set selector. Reflect an explicit lineage Set; else,
  // when opened plainly (no subpath/banner/filter), the project default Scoping Set;
  // else none (custom UC filter, e.g. re-analyze a single UC).
  if (!allSets || !allSets.length) { try { await loadSets(); } catch(_) {} }
  let effectiveSubpath = subpath;
  let effectiveBanner = banner;
  // ?? / == null (not ||): only true absence means "no set selected" — the
  // synthetic set's id is the '__all__' string sentinel (always truthy).
  let setForSel = lineage?.set_id ?? null;
  if (subpath === undefined && !banner && !ucFilter && setForSel == null) {
    const def = _getDefaultSet();
    if (def) setForSel = def.id;
  }
  _populateNrSetSel(setForSel);
  // Apply the Set (only when there's no explicit subpath/banner override) so the
  // corpus field + filter + banner reflect it. Callers that pass their own
  // subpath/banner (Run Set / re-analyze) keep those.
  if (setForSel != null && subpath === undefined && !banner) {
    const r = await _applySetToNewRun(setForSel);
    if (r.subpath !== undefined) effectiveSubpath = r.subpath;
    if (r.banner) effectiveBanner = r.banner;
  }

  if (effectiveBanner) { bannerEl.innerHTML = effectiveBanner; bannerEl.style.display = ''; }
  else bannerEl.style.display = 'none';
  await loadNewRunDefaults(effectiveSubpath);
  if (branchOverride) {
    const br = document.getElementById('nrCorpusBranch');
    if (br) br.value = branchOverride;
  }
  loadRunCategories();
  // Phase C of the infrastructure-confidence work: if the operator is about
  // to run a known Set and the last few runs of that Scoping Set had UCs flagged with
  // low or compromised infrastructure confidence, surface a pre-flight hint
  // banner so they can switch to a long-context model BEFORE triggering.
  try {
    const sid = _pendingRunLineage?.set_id;
    if (sid != null) {
      const r = await api(`/api/runs/preflight-hint?set_id=${encodeURIComponent(sid)}`);
      if (r && r.hint) {
        const hintEl = document.getElementById('newRunPreflightHint') || (() => {
          const h = document.createElement('div');
          h.id = 'newRunPreflightHint';
          h.style.cssText = 'margin:8px 0;padding:8px 12px;background:var(--accent-bg);border-left:3px solid var(--accent);font-size:11px;color:var(--text);border-radius:2px;';
          document.querySelector('#newRunModal .modal-body').prepend(h);
          return h;
        })();
        hintEl.innerHTML = `<strong>⚠ ${esc(r.hint.headline)}</strong><br>
          <span style="color:var(--text-dim);font-size:11px;">${esc(r.hint.detail)}</span>`;
        hintEl.style.display = '';
      }
    }
  } catch(_) { /* hint is advisory; failures shouldn't block opening */ }
}

// Build {handles, uuids, managed} arrays from a Scoping Set's members[]. Corpus
// members go to handles/uuids for the engine's corpus filter; managed
// members go to `managed` and are fetched from the console API at run start.
function _filterFromSetMembers(members) {
  const handles = [], uuids = [], managed = [];
  for (const m of members) {
    if (m.uc_source === 'managed') {
      managed.push(m.uc_uuid);
    } else {
      if (m.uc_handle) handles.push(m.uc_handle);
      else if (m.uc_uuid) uuids.push(m.uc_uuid);
    }
  }
  return (handles.length || uuids.length || managed.length)
    ? {handles, uuids, managed} : null;
}

async function loadRunCategories() {
  try {
    const resp = await api('/api/runs/categories');
    const sel = document.getElementById('nrCategory');
    const prev = sel.value || 'ad-hoc';
    sel.innerHTML = '';
    (resp.categories || ['ad-hoc']).forEach(c => {
      const o = document.createElement('option');
      o.value = c; o.textContent = c;
      sel.appendChild(o);
    });
    sel.value = prev;
  } catch (e) {
    // Endpoint might be unavailable; keep the hardcoded fallback option
  }
}

function closeNewRun() {
  document.getElementById('newRunModal').classList.remove('open');
  _pendingRunFilter = null;
  _pendingRunLineage = null;  // don't leak across modal openings
}

async function submitNewRun() {
  const btn = document.getElementById('submitNewRun'), status = document.getElementById('nrStatus');
  btn.disabled = true; status.textContent = 'submitting…';
  const sc = document.getElementById('nrSampleCount').value.trim();
  const nrResolved = _resolveEndpointModel('nrModelSel', 'nrLastModel');
  let nrEndpoint = null, nrModelId = null;
  if (nrResolved) {
    if (nrResolved.model_config_id) {
      const nrM = _reviewModels.find(r => r.id === nrResolved.model_config_id);
      if (nrM) { nrEndpoint = nrM.endpoint_url; nrModelId = nrM.model_id; }
    } else {
      nrEndpoint = nrResolved.endpoint_url;
      nrModelId = nrResolved.model_id;
    }
  }
  const payload = {
    mode:               document.getElementById('nrMode').value,
    sample_count:       sc ? parseInt(sc) : null,
    corpus_subpath:     document.getElementById('nrCorpusSubpath').value.trim() || null,
    corpus_repo_url:    document.getElementById('nrCorpusRepo').value.trim() || null,
    corpus_repo_branch: document.getElementById('nrCorpusBranch').value.trim() || null,
    spec_repo_url:      document.getElementById('nrSpecRepo').value.trim() || null,
    spec_repo_branch:   document.getElementById('nrSpecBranch').value.trim() || null,
    inference_endpoint: nrEndpoint,
    inference_model:    nrModelId,
    halt_on_error:      document.getElementById('nrHaltOnError').checked,
    name:               document.getElementById('nrSessionName').value.trim(),
    description:        document.getElementById('nrDescription').value.trim(),
    category:           document.getElementById('nrCategory').value || 'ad-hoc',
    // Failsafe "time allowed" (hours → seconds). Blank = auto (server computes
    // ETA + buffer from the data-driven per-UC estimate).
    time_allowed_seconds: (function(){ const h = parseFloat(document.getElementById('nrTimeAllowed').value); return (h > 0) ? Math.round(h*3600) : null; })(),
    // Optional engine-side UC filter — set by runSet / testRunUC. When
    // present, engine processes only these UCs from within corpus_subpath.
    uc_handles:         (_pendingRunFilter?.handles?.length ? _pendingRunFilter.handles : null),
    uc_uuids:           (_pendingRunFilter?.uuids?.length   ? _pendingRunFilter.uuids   : null),
    // Managed UCs are fetched from the console API by the engine at run
    // start; lets reviewers test pre-promotion without pushing first.
    managed_uc_uuids:   (_pendingRunFilter?.managed?.length ? _pendingRunFilter.managed : null),
    // R2: lineage — which Set + selection mode produced this run.
    set_id:             _pendingRunLineage?.set_id ?? null,
    set_name:           _pendingRunLineage?.set_name || null,
    selection_mode:     _pendingRunLineage?.selection_mode || (
      _pendingRunFilter ? 'selection' : 'corpus'
    ),
    // ADR-007 / M11b: per-run corpus source filter. Null = all sources;
    // a partial selection narrows the multi-source clone step in Tekton.
    corpus_namespaces:  _collectSelectedCorpusNamespaces(),
    // Spec analog. Soft enforcement: passed through to the engine as a
    // focus hint; MCP itself still holds every spec namespace.
    spec_namespaces:    _collectSelectedSpecNamespaces(),
  };
  try {
    const resp = await api('/api/runs', {method:'POST', body:JSON.stringify(payload)});
    const name = resp.run?.name || '?';
    status.innerHTML = `<span style="color:var(--green)">triggered: ${esc(name)}</span>`;
    toast(`Analysis started: ${name}`);
    // Navigate to the Runs tab and open this run's detail so the user can
    // watch progress immediately. loadRuns() needs to finish first so the
    // run shows up in allRuns before selectRun tries to find it.
    setTimeout(async () => {
      closeNewRun();
      switchView('runs');
      try { await loadRuns(); } catch(_) {}
      if (name && name !== '?') selectRun(name);
    }, 600);
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    toast('Trigger failed: ' + e.message, true); btn.disabled = false;
  }
}

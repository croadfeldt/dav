// ══════════════════════════ RESULTS ══════════════════════════

async function loadResults() {
  document.getElementById('resultList').innerHTML = '<div class="empty">loading…</div>';
  try {
    const resp = await api('/api/results');
    allResults = resp.results || [];
    document.getElementById('badgeResults').textContent = allResults.length;
    _populateGlobalRunSel();
    renderResultList();
    if (!resp.available)
      document.getElementById('resultList').innerHTML =
        `<div class="empty">Workspace PVC not mounted.<br><small style="color:var(--text-faint)">${esc(resp.workspace_path||'')}</small></div>`;
  } catch (e) {
    document.getElementById('resultList').innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
  }
}

function renderResultList() {
  const el = document.getElementById('resultList');
  const filter = (document.getElementById('resultFilter').value||'').toLowerCase();
  // Filter matches run_id, session name, description, or category
  const filtered = allResults.filter(r => !filter
    || (r.run_id||'').toLowerCase().includes(filter)
    || (r.session_name||'').toLowerCase().includes(filter)
    || (r.session_description||'').toLowerCase().includes(filter)
    || (r.session_category||'').toLowerCase().includes(filter)
  );
  if (!filtered.length) { el.innerHTML = '<div class="empty">No results found.</div>'; return; }
  el.innerHTML = '';
  filtered.forEach(r => {
    const item = document.createElement('div');
    item.className = 'list-item' + (activeRunResultId === r.run_id ? ' active' : '');
    const statusColor = r.failed > 0 ? 'var(--red)' : 'var(--green)';
    const titleText = r.session_name || r.run_id;
    item.innerHTML = `<div class="li-main">
      <div class="li-title" title="${esc(r.run_id)}">${esc(titleText)}</div>
      <div class="li-sub" style="font-family:var(--mono,monospace);font-size:10px;opacity:0.7;">${esc(r.run_id)}</div>
      <div class="li-sub">
        <span style="color:var(--text-dim)">${esc(r.mode||'?')}</span>
        · <span style="color:${statusColor}">${r.successful}/${r.total_ucs} passed</span>
        · ${esc(fmtTs(r.started_at))}
      </div></div>`;
    item.addEventListener('click', () => selectRunResult(r.run_id));
    el.appendChild(item);
  });
}

// The Results tab no longer has its own run list — it shows the current run's
// analysis (run is picked once, in the masthead / Runs tab).
function _showCurrentRunResults(){
  // 3b: Results is scoped by UC/Set, not by run — show the latest eval per UC across the scope.
  loadResultsScopeSel();
  _showScopedResults();
}

// ── Shared "current Scoping Set" scope (uc-scoped-evaluation-design.md: Scope = a Scoping Set) ──
// One app-wide scope drives Results, Cap Map, and Engineering. Selected in the masthead
// (#globalScopeSel) next to Project; '' = all use cases. The per-view pickers are retired
// in favour of this single shared context.
let _activeScope = '';
try { _activeScope = localStorage.getItem('davScope') || ''; } catch (_) {}
let _curView = '';   // the active view name (set by switchView) — lets setScope refresh the right surface
function scopeQuery() { return _activeScope ? '?set_id=' + encodeURIComponent(_activeScope) : ''; }
function populateScopeSel() {
  // NB: never call loadSets() here — loadSets() calls us back, which would recurse.
  // Callers that need fresh data load sets first, then invoke this.
  const sel = document.getElementById('globalScopeSel');
  if (!sel) return;
  sel.innerHTML = '<option value="">All use cases</option>' +
    '<option value="__unassigned__">Unassigned (no Scoping Set)</option>' +
    (allSets || []).filter(s => typeof s.id === 'number')
      .map(s => `<option value="${s.id}">⊞ ${esc(s.name)}</option>`).join('');
  sel.value = _activeScope;
}
function setScope(v) {
  _activeScope = v || '';
  try { localStorage.setItem('davScope', _activeScope); } catch (_) {}
  const sel = document.getElementById('globalScopeSel'); if (sel) sel.value = _activeScope;
  // Refresh whichever scoped view is active + the freshness chip.
  if (_curView === 'results') _showScopedResults();
  else if (_curView === 'capmap') renderCapMap();
  else if (_curView === 'review') { try { _rpUpdateScopeName(); _rpLoadCached(); } catch (_) {} }
  else if (_curView === 'enhancement') { try { loadEnhancementWorkbench(); } catch (_) {} }
  else if (_curView === 'engineering') { try { _loadEngCapMap(); _loadRoadmapProjection(); } catch (_) {} }
  try { loadFreshness(); } catch (_) {}
  try { _persistUserSettings(); } catch (_) {}   // #129/sync: working context follows the user
}
document.getElementById('globalScopeSel')?.addEventListener('change', function () { setScope(this.value); });

// ── Active CUSTOMER axis (matrix UI #130, slice 2b-i) — peer to Project/Scope ─────────
// The other axis of the (customer × project) matrix. Filters customer-attributed surfaces
// (today: the Use Cases list → UCs this customer requested) to the selected customer. Other
// surfaces opt in via customerQuery() in later slices. Persists per-browser now; per-user (#129) later.
let _activeCustomer = '';
try { _activeCustomer = localStorage.getItem('davCustomer') || ''; } catch (_) {}
function customerQuery() { return _activeCustomer ? 'customer_id=' + encodeURIComponent(_activeCustomer) : ''; }
async function populateCustomerSel() {
  const sel = document.getElementById('globalCustomerSel');
  if (!sel) return;
  let customers = [];
  try { customers = (await api('/api/customers')).customers || []; } catch (_) {}
  sel.innerHTML = '<option value="">All customers</option>' +
    customers.map(c => `<option value="${c.id}">${esc(c.name)}${c.is_universal ? ' · internal' : ''}</option>`).join('');
  sel.value = _activeCustomer;
}
function setCustomer(v) {
  _activeCustomer = v || '';
  try { localStorage.setItem('davCustomer', _activeCustomer); } catch (_) {}
  const sel = document.getElementById('globalCustomerSel'); if (sel) sel.value = _activeCustomer;
  if (_curView === 'usecases') { try { loadUCs(); } catch (_) {} }
  try { _persistUserSettings(); } catch (_) {}   // #129/sync: working context follows the user
}
document.getElementById('globalCustomerSel')?.addEventListener('change', function () { setCustomer(this.value); });

// UI lean slice 2: contextual masthead chrome. Scope (Scoping Set) and Customer
// are FILTERS, not global context — they only do something on the views that
// consume them. Show each chip only where it applies (standard adaptive-toolbar
// pattern); hide it everywhere else so the masthead is Project + status + account
// on the ~13 views that ignore them. This is contextual (filters for the current
// surface), NOT nav reshuffling — the rail is unchanged.
//   Scope  → set_id= on Results / Cap Map / Roadmap surfaces (scopeQuery()).
//   Customer → customer_id= on the Use Cases list only (customerQuery(), 1 consumer).
const _SCOPE_VIEWS    = new Set(['results', 'capmap', 'review', 'enhancement', 'engineering']);
const _CUSTOMER_VIEWS = new Set(['usecases']);
function _updateContextChrome(name) {
  const sc = document.getElementById('scopeChip');
  const cu = document.getElementById('customerChip');
  if (sc) sc.style.display = _SCOPE_VIEWS.has(name) ? '' : 'none';
  if (cu) cu.style.display = _CUSTOMER_VIEWS.has(name) ? '' : 'none';
}

// ── UC/Set-scoped results (uc-scoped-evaluation-design.md 3b) ──────────────────
let _scopedUCs = [];
function loadResultsScopeSel() {
  // The local picker is retired — scope now lives in the masthead. Keep the masthead
  // selector in sync (this is invoked when Results opens) and hide the old in-panel bar.
  if (!(allSets || []).length) { try { loadSets(); } catch (_) {} }   // safe: loadSets→populateScopeSel no longer recurses
  populateScopeSel();
  const bar = document.getElementById('resultsScopeBar');
  if (bar) bar.style.display = 'none';
}
function _verdictBadge(v) {
  const map = { supported:['var(--green)','supported'], partially_supported:['var(--accent)','partial'],
                not_supported:['var(--red)','not supported'], failed:['var(--red)','failed'] };
  const [c, l] = map[v] || ['var(--text-faint)', v || '—'];
  return `<span style="font-size:9px;color:${c};border:1px solid ${c}55;padding:0 5px;border-radius:2px;white-space:nowrap;">${esc(l)}</span>`;
}
function _renderScopedUCList() {
  const el = document.getElementById('ucResultList');
  if (!el) return;
  if (!_scopedUCs.length) { el.innerHTML = '<div class="empty">No use cases in this scope.</div>'; return; }
  // Honor the verdict filter (the scoped Results list — previously the filter was a no-op here
  // because its handler only re-rendered the run-summary list).
  const vf = (document.getElementById('ucVerdictFilter')?.value) || '';
  const list = vf
    ? _scopedUCs.filter(u => vf === 'failed' ? u.failed : (u.evaluated && u.verdict === vf))
    : _scopedUCs;
  if (!list.length) { el.innerHTML = '<div class="empty">No use cases match this filter.</div>'; return; }
  el.innerHTML = list.map(u => `
    <div class="list-item${activeUCResult === u.uc_uuid ? ' active' : ''}" style="cursor:pointer;"
         onclick="selectScopedUC('${u.uc_uuid}', ${u.run_id ? `'${esc(u.run_id)}'` : 'null'})">
      <div style="display:flex;align-items:center;gap:6px;">
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(u.title || u.uc_handle || u.uc_uuid)}</span>
        ${u.failed
          ? `<span style="font-size:9px;color:var(--red);">✗ failed</span>${_phaseBadge(u.error_phase)}`
          : (u.evaluated ? _verdictBadge(u.verdict) : '<span style="font-size:9px;color:var(--text-faint);">not evaluated</span>')}
        ${u.stale ? '<span title="Edited since its last evaluation" style="font-size:9px;color:var(--amber,#d79a2b);">● stale</span>' : ''}
      </div>
    </div>`).join('');
}
async function _showScopedResults() {
  const el = document.getElementById('ucResultList');
  if (!el) return;
  el.innerHTML = '<div class="empty">loading…</div>';
  try {
    const r = await api('/api/results/uc-latest' + scopeQuery());
    _scopedUCs = r.ucs || [];
    const hdr = document.getElementById('runResultsHeader');
    if (hdr) {
      hdr.style.display = '';
      hdr.innerHTML = `<div style="font-size:12px;"><strong>${r.evaluated}/${r.total}</strong> use cases evaluated <span style="color:var(--text-faint);">· latest eval per UC (may span ingestions)</span></div>`;
    }
    _renderScopedUCList();
    const p = document.getElementById('ucListPanel'); if (p) p.style.display = '';
    _clearAnalysis('Select a use case.');
  } catch (e) {
    el.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
  }
}
async function selectScopedUC(ucUuid, runId) {
  activeUCResult = ucUuid;
  activeRunResultId = runId || null;   // the UC's latest-eval run, for the per-UC detail fetch
  _renderScopedUCList();
  _clearAnalysis('loading…');
  // #121 mirror: a failed UC has no useful analysis to fetch — show WHY it failed + re-ingest.
  const _m = (_scopedUCs || []).find(u => u.uc_uuid === ucUuid);
  if (_m && _m.failed) {
    document.getElementById('analysisDetail').innerHTML =
      `<div class="detail-pane"><div style="padding:20px;max-width:640px;">
        <div style="font-size:14px;font-weight:600;color:var(--red);margin-bottom:6px;">✗ Ingestion failed ${_phaseBadge(_m.error_phase)}</div>
        <div style="font-size:12px;color:var(--text-dim);line-height:1.5;margin-bottom:14px;">${esc(_m.error_reason || 'No failure detail was recorded for this use case.')}</div>
        <button class="btn primary btn-sm" onclick="_reingestUC('${esc(ucUuid)}')">↻ Re-ingest this use case</button>
      </div></div>`;
    return;
  }
  if (!runId) {
    document.getElementById('analysisDetail').innerHTML = '<div class="detail-pane"><div class="detail-empty">This use case hasn’t been evaluated yet — trigger an ingestion in the Ingestions tab.</div></div>';
    return;
  }
  try {
    const data = await api(`/api/results/${encodeURIComponent(runId)}/uc/${encodeURIComponent(ucUuid)}`);
    renderAnalysis(data, ucUuid);
  } catch (e) {
    _clearAnalysis();
    document.getElementById('analysisDetail').innerHTML = `<div class="detail-pane"><div style="color:var(--red);padding:20px">${esc(e.message)}</div></div>`;
  }
}
async function selectRunResult(runId) {
  activeRunResultId = runId; activeUCResult = null;
  // Keep the masthead run-status label in sync (whichever surface picked the run).
  _populateGlobalRunSel();
  renderResultList();
  document.getElementById('ucResultList').innerHTML = '<div class="empty">loading…</div>';
  document.getElementById('ucListPanel').style.display = '';
  _clearAnalysis('Select a use case.');
  // Show ingest button when a run is selected
  document.getElementById('ingestResultBtn').style.display = '';
  // Auto-ingest silently so arch review is available without a manual step.
  // Ingest is idempotent (delete+reinsert), so safe to fire on every selection.
  api(`/api/analysis/ingest/${encodeURIComponent(runId)}`, { method: 'POST' }).catch(() => {});
  try {
    const summary = await api(`/api/results/${encodeURIComponent(runId)}`);
    activeRunSummary = summary;
    renderRunSummaryHeader(summary);
    renderUCResultList(summary);
    _loadShallowness(runId);   // advisory grounding flags (#45a) — async, re-renders badges
  } catch (e) {
    document.getElementById('ucResultList').innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
  }
}

document.getElementById('ingestResultBtn').addEventListener('click', async () => {
  if (!activeRunResultId) return;
  const btn = document.getElementById('ingestResultBtn');
  btn.disabled = true; btn.textContent = 'Ingesting…';
  try {
    const resp = await api(`/api/analysis/ingest/${encodeURIComponent(activeRunResultId)}`, { method: 'POST' });
    toast(`Ingested ${resp.ingested_ucs} UCs, ${resp.ingested_gaps} gaps`);
  } catch(e) {
    toast('Ingest failed: ' + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = '↓ Ingest';
  }
});

function renderRunSummaryHeader(s) {
  // Renders into the persistent runResultsHeader strip — stays visible while
  // the user drills into per-UC analysis. Compact one-row layout: title,
  // mode, key counts, pass rate, wall time. The analysisDetail pane below
  // gets the placeholder "← pick a use case" hint.
  const pct = s.total_ucs > 0 ? Math.round((s.successful/s.total_ucs)*100) : 0;
  const passColor = s.failed > 0 ? 'var(--red)' : 'var(--green)';
  // Prefer the session name when available (joined onto allResults at /api/results)
  const meta = (allResults || []).find(r => r.run_id === s.run_id) || {};
  const title = meta.session_name || s.run_id;
  const header = document.getElementById('runResultsHeader');
  // R2: lineage line — Set + selection mode + (if any) the session name.
  // Renders when at least one is populated; suppressed for unannotated runs.
  const modeLabels = {set:'Set', selection:'Selection', individual:'Individual UC', corpus:'Full corpus'};
  const lineageBits = [];
  if (s.set_name) lineageBits.push(`<span style="cursor:pointer;color:var(--accent);" title="Filter UC tab to this Scoping Set" onclick="switchView('usecases');setTimeout(()=>selectSet(${s.set_id}),100);">⊞ ${esc(s.set_name)}</span>`);
  if (s.selection_mode) lineageBits.push(`<span style="color:var(--text-faint);">${esc(modeLabels[s.selection_mode] || s.selection_mode)}</span>`);
  const lineageRow = lineageBits.length
    ? `<div style="font-size:11px;margin-top:6px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;"><span style="color:var(--text-faint);">Lineage:</span>${lineageBits.join('<span style="color:var(--border-bright);">·</span>')}</div>`
    : '';
  header.innerHTML = `
    <div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <div style="min-width:0;flex:1;">
        <div style="font-family:var(--serif);font-size:14px;line-height:1.25;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(s.run_id)}">${esc(title)}</div>
        <div style="font-family:var(--mono,monospace);font-size:10px;color:var(--text-faint);margin-top:2px;">${esc(s.run_id)} · ${esc(s.mode || '?')}</div>
      </div>
      <div style="display:flex;gap:14px;align-items:baseline;font-size:11px;flex-wrap:wrap;">
        <span><strong style="color:${passColor};font-size:13px;">${s.successful}/${s.total_ucs}</strong> <span style="color:var(--text-faint);">UCs (${pct}%)</span></span>
        ${s.failed > 0 ? `<span style="color:var(--red);">${s.failed} failed</span>` : ''}
        <span style="color:var(--text-faint);">${s.total_samples || 0} samples</span>
        <span style="color:var(--text-faint);">⏱ ${s.runner_total_seconds ? s.runner_total_seconds+'s' : '—'}</span>
        <span style="color:var(--text-faint);">${esc(fmtTs(s.finished_at || s.started_at))}</span>
      </div>
    </div>
    ${lineageRow}`;
  header.style.display = '';
  // Clear the per-UC area to its empty state — header stays
  const bar = document.getElementById('analysisActionBar'); bar.innerHTML=''; bar.style.display='none';
  document.getElementById('analysisDetail').innerHTML =
    '<div class="detail-pane"><div class="detail-empty">← Select a use case to view its analysis.</div></div>';
}

// Order in which verdict groups render (top → bottom). 'failed' = stage-2
// crash; 'unknown' = no verdict reported.
const UC_VERDICT_GROUPS = [
  ['not_supported',       'Not supported'],
  ['partially_supported', 'Partial'],
  ['supported',           'Supported'],
  ['failed',              'Failed (errors)'],
  ['unknown',             'Unknown'],
];

function _ucBucketKey(u) {
  if (u.status === 'failed') return 'failed';
  return u.verdict || 'unknown';
}

// Extract the category and display-label from a UC handle.
// Handle convention: <prefix>/<category>/<descriptor>  (e.g. test/standard/vm-provision-happy)
// When grouped by category the category segment is surfaced as the group header
// and stripped from the displayed label so the row stays compact.
function _ucHandleParts(handle) {
  if (!handle) return { category: null, display: handle || '' };
  const parts = handle.split('/');
  if (parts.length >= 3) {
    // Skip the first segment (conventional prefix like "test"), take next as category.
    return { category: parts[1], display: parts.slice(2).join('/') };
  }
  if (parts.length === 2) {
    return { category: parts[0], display: parts[1] };
  }
  return { category: null, display: handle };
}

async function _loadShallowness(runId) {
  // Advisory per-UC grounding signal (#45a). Fetches /api/runs/{id}/shallowness
  // and re-renders the UC list so thin-but-successful analyses get a badge.
  _rdShallowByUuid = {}; _rdShallowSummary = null;
  try {
    const sh = await api(`/api/runs/${encodeURIComponent(runId)}/shallowness`);
    (sh.ucs || []).forEach(u => { if (u.uc_uuid) _rdShallowByUuid[u.uc_uuid] = u; });
    _rdShallowSummary = sh;
  } catch (e) { /* advisory — stay silent when unavailable */ }
  if (activeRunResultId === runId && activeRunSummary) {
    renderUCResultList(activeRunSummary);
  }
}

function _ucRowEl(u, { stripCategory = false } = {}) {
  const item = document.createElement('div');
  item.className = 'uc-row' + (activeUCResult===u.uc_uuid ? ' active' : '');
  const vClass = u.status==='failed' ? 'verdict-error' : verdictClass(u.verdict);
  const vLabel = u.status==='failed' ? 'failed'
    : (u.verdict||'?').replace(/_/g,' ').replace('partially supported','partial').replace('not supported','not supp.');
  const rawHandle = u.uc_handle || u.uc_uuid;
  const displayHandle = stripCategory ? _ucHandleParts(rawHandle).display : rawHandle;
  // R2: lifecycle_state_at_run badge — only shown when the UC was managed at
  // run time. Color-coded so reviewers see at a glance which results came
  // from pre-promotion vs approved UCs.
  let stateBadge = '';
  if (u.lifecycle_state_at_run) {
    const stateColors = { draft:'var(--text-faint)', ready:'var(--blue)', in_review:'var(--accent)', approved:'var(--green)', deprecated:'var(--red)' };
    const c = stateColors[u.lifecycle_state_at_run] || 'var(--text-faint)';
    stateBadge = `<span style="font-size:8px;text-transform:uppercase;letter-spacing:0.08em;color:${c};border:1px solid ${c};padding:0 4px;border-radius:2px;flex-shrink:0;" title="UC lifecycle state when this ingestion was triggered">${esc(u.lifecycle_state_at_run)}</span>`;
  }
  // Advisory grounding flag (#45a): a thin-but-successful analysis.
  let shallowBadge = '';
  const _sh = _rdShallowByUuid[u.uc_uuid];
  if (_sh && _sh.shallow) {
    const _r = esc((_sh.reasons || []).join('; '));
    shallowBadge = `<span style="font-size:8px;text-transform:uppercase;letter-spacing:0.08em;color:#d79a3a;border:1px solid #d79a3a;padding:0 4px;border-radius:2px;flex-shrink:0;" title="Thin grounding — ${_r}">⚠ thin</span>`;
  }
  // #121 mirror: failure reason/phase on a failed UC (hover for the full reason).
  let failBadge = '';
  if (u.status === 'failed') {
    const phLabel = u.error_phase === 'not_emitted' ? 'dropped' : (u.error_phase || 'failed');
    failBadge = `<span title="${esc(u.error_reason || 'The engine reported a failure for this use case.')}" style="font-size:8px;text-transform:uppercase;letter-spacing:0.08em;color:var(--red);border:1px solid var(--red);padding:0 4px;border-radius:2px;flex-shrink:0;">✗ ${esc(phLabel)}</span>`;
  }
  item.innerHTML = `
    <span class="${vClass}" style="font-size:9px;text-transform:uppercase;letter-spacing:0.08em;flex-shrink:0;width:52px;padding-top:1px">${esc(vLabel)}</span>
    <span class="uc-handle">${esc(displayHandle)}</span>
    ${stateBadge}
    ${failBadge}
    ${shallowBadge}
    <span class="uc-time">${u.wall_time_seconds ? u.wall_time_seconds+'s' : ''}</span>`;
  item.addEventListener('click', () => selectUCResult(u.uc_uuid));
  return item;
}

function _renderGrouped(el, items, keyFn, labelFn, storagePrefix, rowOpts = {}) {
  const buckets = new Map();
  const order = [];
  for (const u of items) {
    const k = keyFn(u);
    if (!buckets.has(k)) { buckets.set(k, []); order.push(k); }
    buckets.get(k).push(u);
  }
  for (const key of order) {
    const groupItems = buckets.get(key);
    const groupEl = document.createElement('div');
    groupEl.className = 'uc-group';
    const storKey = storagePrefix + key;
    try {
      if (localStorage.getItem('ucGroupCollapsed:' + storKey) === '1') groupEl.classList.add('collapsed');
    } catch(e) {}
    const header = document.createElement('div');
    header.className = 'uc-group-header';
    header.innerHTML = `<span>${esc(labelFn(key))}</span><span class="badge">${groupItems.length}</span>`;
    header.addEventListener('click', () => {
      groupEl.classList.toggle('collapsed');
      try { localStorage.setItem('ucGroupCollapsed:'+storKey, groupEl.classList.contains('collapsed') ? '1' : '0'); } catch(e) {}
    });
    groupEl.appendChild(header);
    const body = document.createElement('div');
    body.className = 'uc-group-body';
    groupItems.forEach(u => body.appendChild(_ucRowEl(u, rowOpts)));
    groupEl.appendChild(body);
    el.appendChild(groupEl);
  }
}

function renderUCResultList(summary) {
  const el = document.getElementById('ucResultList');
  const vFilter = document.getElementById('ucVerdictFilter').value;
  const groupBy  = document.getElementById('ucGroupBy').value;  // '' | 'verdict' | 'category'
  const ucs = summary.ucs || [];
  const filtered = vFilter
    ? ucs.filter(u => vFilter==='failed' ? u.status==='failed' : u.verdict===vFilter)
    : ucs;
  if (!filtered.length) { el.innerHTML = '<div class="empty">No use cases match.</div>'; return; }
  el.innerHTML = '';

  if (groupBy === 'verdict') {
    // Fixed-order verdict buckets
    const buckets = {};
    for (const u of filtered) {
      const k = _ucBucketKey(u);
      (buckets[k] = buckets[k] || []).push(u);
    }
    UC_VERDICT_GROUPS.forEach(([key, label]) => {
      const items = buckets[key];
      if (!items || !items.length) return;
      const groupEl = document.createElement('div');
      groupEl.className = 'uc-group';
      try {
        if (localStorage.getItem('ucGroupCollapsed:v:' + key) === '1') groupEl.classList.add('collapsed');
      } catch(e) {}
      const header = document.createElement('div');
      header.className = 'uc-group-header';
      header.innerHTML = `<span>${esc(label)}</span><span class="badge">${items.length}</span>`;
      header.addEventListener('click', () => {
        groupEl.classList.toggle('collapsed');
        try { localStorage.setItem('ucGroupCollapsed:v:'+key, groupEl.classList.contains('collapsed') ? '1' : '0'); } catch(e) {}
      });
      groupEl.appendChild(header);
      const body = document.createElement('div');
      body.className = 'uc-group-body';
      items.forEach(u => body.appendChild(_ucRowEl(u)));
      groupEl.appendChild(body);
      el.appendChild(groupEl);
    });
    return;
  }

  if (groupBy === 'category') {
    // Group by UC category (2nd segment of handle), alpha-sorted groups.
    // Within each group strip the category prefix from the displayed handle.
    const catOf = u => _ucHandleParts(u.uc_handle || u.uc_uuid).category || '(uncategorized)';
    const sorted = [...filtered].sort((a, b) => catOf(a).localeCompare(catOf(b)));
    _renderGrouped(
      el, sorted,
      u => catOf(u),
      k => k,
      'cat:',
      { stripCategory: true },
    );
    return;
  }

  // Flat list
  filtered.forEach(u => el.appendChild(_ucRowEl(u)));
}

async function selectUCResult(ucUuid) {
  activeUCResult = ucUuid; renderUCResultList(activeRunSummary);
  _clearAnalysis('loading…');
  try {
    const data = await api(`/api/results/${encodeURIComponent(activeRunResultId)}/uc/${encodeURIComponent(ucUuid)}`);
    renderAnalysis(data, ucUuid);
  } catch (e) {
    _clearAnalysis(); document.getElementById('analysisDetail').innerHTML = `<div class="detail-pane"><div style="color:var(--red);padding:20px">${esc(e.message)}</div></div>`;
  }
}

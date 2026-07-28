// ══════════════════════════ COMPARISON ══════════════════════════

let compareData = null;
let cmpMode = false;
let cmpFilter = 'all';
let activeCompareUCUuid = null;

function _enterCompareMode() {
  cmpMode = true;
  document.getElementById('ucNormalControls').style.display = 'none';
  document.getElementById('cmpFilterRow').style.display = 'flex';
  document.getElementById('ucPanelTitle').textContent = 'Comparison';
  document.getElementById('cmpToggleBtn').style.color = 'var(--accent)';
}

function _exitCompareMode() {
  cmpMode = false; compareData = null; activeCompareUCUuid = null; cmpFilter = 'all';
  document.getElementById('ucNormalControls').style.display = '';
  document.getElementById('cmpFilterRow').style.display = 'none';
  document.getElementById('cmpPickerRow').style.display = 'none';
  document.getElementById('ucPanelTitle').textContent = 'Use Cases';
  document.getElementById('cmpToggleBtn').style.color = '';
  if (activeRunSummary) renderUCResultList(activeRunSummary);
  else document.getElementById('ucResultList').innerHTML = '<div class="empty">select an analysis</div>';
  _clearAnalysis();
}

// Populate the run-B <select> with every other ingested result; label run A.
function _populateCompareBSelect() {
  const sel = document.getElementById('cmpRunBSelect');
  if (!sel) return;
  sel.innerHTML = '<option value="">— pick an analysis —</option>';
  allResults.forEach(r => {
    if (r.run_id === activeRunResultId) return;
    const opt = document.createElement('option');
    opt.value = r.run_id;
    opt.textContent = r.session_name
      ? `${r.session_name} (${r.run_id.slice(0,16)}…)`
      : r.run_id;
    opt.title = r.run_id;
    sel.appendChild(opt);
  });
  const activeRun = allResults.find(r => r.run_id === activeRunResultId);
  document.getElementById('cmpRunALabel').textContent = activeRun?.session_name || activeRunResultId || '—';
  document.getElementById('cmpRunALabel').title = activeRunResultId || '';
}

// Open the compare picker for the current run A (activeRunResultId).
function _openComparePicker() {
  if (!activeRunResultId) { toast('Select an analysis first'); return; }
  _populateCompareBSelect();
  document.getElementById('cmpPickerRow').style.display = 'flex';
}

// Run the A-vs-B comparison and enter compare mode. Shared by the picker Go
// button and the one-click "vs previous" entry point.
async function _runCompare(runB) {
  if (!runB || !activeRunResultId) return;
  document.getElementById('cmpPickerRow').style.display = 'none';
  _enterCompareMode();
  document.getElementById('ucResultList').innerHTML = '<div class="empty">loading comparison…</div>';
  _clearAnalysis('Loading…');
  try {
    compareData = await api(`/api/results/compare?a=${encodeURIComponent(activeRunResultId)}&b=${encodeURIComponent(runB)}`);
    renderCompareHeader();
    renderCompareList();
  } catch(e) {
    document.getElementById('ucResultList').innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
  }
}

// The previous INGESTED run of the same Scoping Set (by start time) — for "vs previous".
function _prevIngestedRunOfSet(r) {
  const t = Date.parse(r.started_at || r.created_at || '') || 0;
  const cands = (allRuns || []).filter(x =>
    x.name !== r.name && x.run_id &&
    String(x.set_id ?? '') === String(r.set_id ?? '') &&
    (Date.parse(x.started_at || x.created_at || '') || 0) < t);
  cands.sort((a, b) =>
    (Date.parse(b.started_at || b.created_at || '') || 0) - (Date.parse(a.started_at || a.created_at || '') || 0));
  return cands[0] || null;
}

// Run-row entry point (a): make this run current (A), go to Results, open the picker.
function compareRunFromRow(name) {
  const r = (allRuns || []).find(x => x.name === name);
  if (!r || !r.run_id) { toast('This analysis has no ingested results to compare yet', true); return; }
  selectRunResult(r.run_id);
  switchView('results');
  setTimeout(_openComparePicker, 120);
}

// Run-row entry point (b): one-click compare vs the previous ingested run of the same set.
function compareRunVsPrev(name) {
  const r = (allRuns || []).find(x => x.name === name);
  if (!r || !r.run_id) { toast('This analysis has no ingested results to compare yet', true); return; }
  const prev = _prevIngestedRunOfSet(r);
  if (!prev) { toast('No earlier ingested analysis of the same Scoping Set to compare against', true); return; }
  selectRunResult(r.run_id);
  switchView('results');
  setTimeout(() => _runCompare(prev.run_id), 120);
}

document.getElementById('cmpToggleBtn').addEventListener('click', () => {
  if (cmpMode) { _exitCompareMode(); return; }
  _openComparePicker();
});

document.getElementById('cmpCancelBtn').addEventListener('click', () => {
  document.getElementById('cmpPickerRow').style.display = 'none';
  if (cmpMode) _exitCompareMode();
});

document.getElementById('cmpRunBtn').addEventListener('click', () => {
  _runCompare(document.getElementById('cmpRunBSelect').value);
});

document.querySelectorAll('.cmp-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.cmp-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    cmpFilter = btn.dataset.cf;
    if (compareData) renderCompareList();
  });
});

function renderCompareHeader() {
  if (!compareData) return;
  const d = compareData.delta;
  const sa = compareData.summary_a, sb = compareData.summary_b;
  const dtSign = (v) => v === null ? '—' : (v > 0 ? '+' + v : '' + v);
  const el = document.getElementById('analysisDetail');
  el.innerHTML = `<div class="detail-pane">
    <div class="detail-title">Analysis <em>comparison</em></div>
    <div class="detail-sub">${esc(compareData.run_a)} → ${esc(compareData.run_b)}</div>
    <div class="stat-row">
      <div class="stat-box"><div class="sv">${d.verdict_changes}</div><div class="sl">Verdict changes</div></div>
      <div class="stat-box ${d.successful > 0 ? 'green' : (d.successful < 0 ? 'red' : '')}">
        <div class="sv">${dtSign(d.successful)}</div><div class="sl">Successes</div></div>
      <div class="stat-box ${d.failed < 0 ? 'green' : (d.failed > 0 ? 'red' : '')}">
        <div class="sv">${dtSign(d.failed)}</div><div class="sl">Failures</div></div>
      ${d.wall_time_seconds !== null ? `<div class="stat-box"><div class="sv" style="font-size:20px">${dtSign(d.wall_time_seconds)}s</div><div class="sl">Wall time Δ</div></div>` : ''}
    </div>
    <div class="cmp-side-grid">
      <div class="cmp-side">
        <div class="cmp-side-label">A · ${esc(compareData.run_a)}</div>
        <div class="kv-grid">
          <div class="kv-label">UCs</div><div class="kv-val">${sa.total_ucs ?? '—'}</div>
          <div class="kv-label">Succeeded</div><div class="kv-val">${sa.successful ?? '—'}</div>
          <div class="kv-label">Failed</div><div class="kv-val">${sa.failed ?? '—'}</div>
          <div class="kv-label">Wall time</div><div class="kv-val">${sa.runner_total_seconds != null ? sa.runner_total_seconds+'s' : '—'}</div>
        </div>
      </div>
      <div class="cmp-side">
        <div class="cmp-side-label">B · ${esc(compareData.run_b)}</div>
        <div class="kv-grid">
          <div class="kv-label">UCs</div><div class="kv-val">${sb.total_ucs ?? '—'}</div>
          <div class="kv-label">Succeeded</div><div class="kv-val">${sb.successful ?? '—'}</div>
          <div class="kv-label">Failed</div><div class="kv-val">${sb.failed ?? '—'}</div>
          <div class="kv-label">Wall time</div><div class="kv-val">${sb.runner_total_seconds != null ? sb.runner_total_seconds+'s' : '—'}</div>
        </div>
      </div>
    </div>
    <div style="color:var(--text-faint);font-family:var(--serif);font-style:italic;font-size:13px;margin-top:4px;">
      ← Select a use case to view its verdict diff.
    </div>
  </div>`;
}

function _verdictArrow(va, vb) {
  if (!va && !vb) return '';
  if (!va) return `<span class="cmp-only-b">+ ${esc(vb)}</span>`;
  if (!vb) return `<span class="cmp-only-a">− ${esc(va)}</span>`;
  if (va === vb) return `<span style="color:var(--text-faint)">${esc(vb.replace(/_/g,' '))}</span>`;
  const aClass = verdictClass(va), bClass = verdictClass(vb);
  return `<span class="${aClass}" style="font-size:9px">${esc(va.replace(/_/g,' '))}</span>
          <span style="color:var(--text-faint);font-size:9px">→</span>
          <span class="${bClass}" style="font-size:9px">${esc(vb.replace(/_/g,' '))}</span>`;
}

function renderCompareList() {
  if (!compareData) return;
  const el = document.getElementById('ucResultList');
  let diffs = compareData.uc_diffs || [];
  if (cmpFilter === 'changed') diffs = diffs.filter(d => d.changed || d.only_a || d.only_b);
  else if (cmpFilter === 'only_a') diffs = diffs.filter(d => d.only_a);
  else if (cmpFilter === 'only_b') diffs = diffs.filter(d => d.only_b);
  // Sort: changed first, then by handle
  diffs = [...diffs].sort((a, b) => {
    const ac = a.changed || a.only_a || a.only_b ? 0 : 1;
    const bc = b.changed || b.only_a || b.only_b ? 0 : 1;
    if (ac !== bc) return ac - bc;
    return (a.uc_handle || a.uc_uuid || '').localeCompare(b.uc_handle || b.uc_uuid || '');
  });
  if (!diffs.length) { el.innerHTML = '<div class="empty">No diffs match filter.</div>'; return; }
  el.innerHTML = '';
  diffs.forEach(d => {
    const row = document.createElement('div');
    const changed = d.changed || d.only_a || d.only_b;
    const isActive = activeCompareUCUuid === d.uc_uuid;
    row.className = 'cmp-row' + (changed ? ' changed' : '') + (isActive ? ' active' : '');
    row.innerHTML = `
      <span class="cmp-handle">${esc(d.uc_handle || d.uc_uuid)}</span>
      <span class="cmp-arrow">${_verdictArrow(d.verdict_a, d.verdict_b)}</span>`;
    row.addEventListener('click', () => selectCompareUC(d));
    el.appendChild(row);
  });
}

function selectCompareUC(diff) {
  activeCompareUCUuid = diff.uc_uuid;
  renderCompareList();
  const bar = document.getElementById('analysisActionBar'); bar.innerHTML=''; bar.style.display='none';
  const el = document.getElementById('analysisDetail');
  const va = diff.verdict_a, vb = diff.verdict_b;
  const gapsAdded   = diff.gaps_added || [];
  const gapsRemoved = diff.gaps_removed || [];
  let gapHtml = '';
  if (gapsAdded.length || gapsRemoved.length) {
    gapHtml = '<div class="detail-section"><div class="detail-section-title">Gap changes</div><div class="cmp-gap-list">';
    gapsAdded.forEach(g => { gapHtml += `<div class="added">+ ${esc(g)}</div>`; });
    gapsRemoved.forEach(g => { gapHtml += `<div class="removed">− ${esc(g)}</div>`; });
    gapHtml += '</div></div>';
  }
  el.innerHTML = `<div class="detail-pane">
    <div class="detail-title">Verdict <em>diff</em></div>
    <div class="detail-sub">${esc(diff.uc_handle || diff.uc_uuid)}</div>
    <div class="cmp-side-grid" style="margin-bottom:16px;">
      <div class="cmp-side">
        <div class="cmp-side-label">A</div>
        ${diff.only_a ? '<span class="cmp-only-a">only in analysis A</span>' : ''}
        ${!diff.only_a ? `<span class="${verdictClass(va)}" style="font-size:18px;font-family:var(--serif);font-weight:300">${esc((va||'—').replace(/_/g,' '))}</span>` : ''}
        ${diff.wall_time_a != null ? `<div style="font-size:10px;color:var(--text-faint);margin-top:6px">${diff.wall_time_a}s</div>` : ''}
      </div>
      <div class="cmp-side">
        <div class="cmp-side-label">B</div>
        ${diff.only_b ? '<span class="cmp-only-b">only in analysis B</span>' : ''}
        ${!diff.only_b ? `<span class="${verdictClass(vb)}" style="font-size:18px;font-family:var(--serif);font-weight:300">${esc((vb||'—').replace(/_/g,' '))}</span>` : ''}
        ${diff.wall_time_b != null ? `<div style="font-size:10px;color:var(--text-faint);margin-top:6px">${diff.wall_time_b}s</div>` : ''}
      </div>
    </div>
    ${gapHtml}
    ${!diff.changed && !diff.only_a && !diff.only_b ? '<div style="color:var(--text-faint);font-style:italic;font-size:12px;">Verdict unchanged between analyses.</div>' : ''}
  </div>`;
}

function _clearAnalysis(msg) {
  _lastAnalysisData = null;
  const bar = document.getElementById('analysisActionBar');
  bar.innerHTML = ''; bar.style.display = 'none';
  document.getElementById('analysisDetail').innerHTML =
    `<div class="detail-pane"><div class="detail-empty">${msg||'Select a use case to view its analysis.'}</div></div>`;
}

function _fmtSec(sec) {
  if (!sec) return '—';
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

function _collapsibleAnalysisSection(title, rowsHtml) {
  if (!rowsHtml.length) return '';
  const id = 'cb-' + Math.random().toString(36).slice(2,8);
  return `<div class="detail-section"><div class="detail-section-title">${esc(title)} (${rowsHtml.length})</div>
    <div class="analysis-block">
      <button class="collapsible-hdr" onclick="document.getElementById('${id}').classList.toggle('open');this.querySelector('.chev').textContent=document.getElementById('${id}').classList.contains('open')?'▲':'▼'">
        <span>Show ${rowsHtml.length} item${rowsHtml.length===1?'':'s'}</span><span class="chev">▼</span>
      </button>
      <div class="collapsible-body-inner" id="${id}">${rowsHtml.join('')}</div>
    </div></div>`;
}

function _specRefChips(refs) {
  return (refs||[]).map(r =>
    `<button class="spec-ref-chip" onclick="_copySpecRef('${esc(r)}')" title="Click to copy spec ref path">${esc(r)}</button>`
  ).join('');
}

function _copySpecRef(ref) {
  navigator.clipboard.writeText(ref).catch(()=>{});
  toast('Copied: ' + ref);
}

function _copyGapReport() {
  if (!_lastAnalysisData) return;
  const data    = _lastAnalysisData;
  const summary = data.summary || {};
  const gaps    = data.gaps_identified || [];
  const ucEntry = (activeRunSummary?.ucs||[]).find(u=>u.uc_uuid===activeUCResult);
  const handle  = ucEntry?.uc_handle || activeUCResult || '';
  let md = `## DAV Analysis: ${handle}\n\n`;
  md += `**Verdict:** ${(summary.verdict||'unknown').replace(/_/g,' ')}  \n`;
  if (summary.overall_confidence) {
    const conf = typeof summary.overall_confidence==='object' ? summary.overall_confidence.label : summary.overall_confidence;
    md += `**Confidence:** ${conf}  \n`;
  }
  if (summary.notes) md += `\n**Assessment:**\n${summary.notes}\n`;
  if (gaps.length) {
    md += `\n### Gaps (${gaps.length})\n`;
    _sortGapsBySeverity(gaps).forEach((g,i) => {
      const sev = typeof g.severity==='object' ? g.severity.label : (g.severity||'unknown');
      md += `\n#### ${i+1}. [${sev.toUpperCase()}] ${g.description}\n`;
      if (g.rationale) md += `\n**Rationale:** ${g.rationale}\n`;
      if (g.recommendation) md += `\n**Recommendation:** ${g.recommendation}\n`;
      const refs = g.spec_refs_consulted||[];
      if (refs.length) md += `\n**Spec refs:** ${refs.join(', ')}\n`;
      if (g.spec_refs_missing) md += `\n**Missing from spec:** ${g.spec_refs_missing}\n`;
    });
  }
  navigator.clipboard.writeText(md).catch(()=>{});
  toast('Gap report copied to clipboard');
}

function _reanalyzeUC(ucUuid, ucHandle) {
  openNewRun(`Re-analyze: ${ucHandle||ucUuid}`, null);
}

function renderAnalysis(data, ucUuid) {
  _lastAnalysisData = data;
  const el = document.getElementById('analysisDetail');

  const actionBar = document.getElementById('analysisActionBar');
  if (data._source === 'failure') {
    actionBar.innerHTML = `<div class="analysis-action-bar">
        <button class="btn ghost btn-sm" onclick="_reanalyzeUC('${esc(ucUuid)}','')">↺ Re-analyze</button>
      </div>`;
    actionBar.style.display = '';
    el.innerHTML = `<div class="detail-pane">
      <div class="detail-title" style="color:var(--red)">Analysis <em>failed</em></div>
      <div class="detail-sub">${esc(ucUuid)}</div>
      <div class="analysis-block"><div class="analysis-block-header">Error</div>
        <div class="analysis-block-body"><pre style="white-space:pre-wrap;word-break:break-word">${esc(data.error)}</pre></div>
      </div></div>`; return;
  }
  if (data._source === 'explore') { renderExploreAnalysis(data, ucUuid); return; }

  const summary = data.summary || {};
  const verdict = summary.verdict || '?';
  const conf    = typeof summary.overall_confidence==='object' ? summary.overall_confidence.label : (summary.overall_confidence||'?');
  const notes   = summary.notes || '';
  const meta    = data.analysis_metadata || {};
  const sa      = data.sample_annotations;
  const gaps    = data.gaps_identified || [];
  const comps   = data.components_required || [];
  const dm      = data.data_model_touched || [];
  const caps    = data.capabilities_invoked || [];
  const pols    = data.policy_modes_required || [];

  const ucEntry  = (activeRunSummary?.ucs||[]).find(u=>u.uc_uuid===ucUuid);
  const ucHandle = ucEntry?.uc_handle || ucUuid;

  actionBar.innerHTML = `<div class="analysis-action-bar">
    <button class="btn ghost btn-sm" onclick="switchView('usecases');setTimeout(()=>editUC('${esc(ucUuid)}'),200)" title="Open this UC in the editor">✏ Edit UC</button>
    <button class="btn ghost btn-sm" onclick="_reanalyzeUC('${esc(ucUuid)}','${esc(ucHandle)}')" title="Open the analysis trigger pre-filled for re-analysis">↺ Re-analyze</button>
    <button class="btn ghost btn-sm" onclick="_copyGapReport()" title="Copy gap report as markdown">⧉ Copy report</button>
    <button class="btn ghost btn-sm" onclick="openReviewPane('uc','${esc(ucUuid)}','review')" title="Get an architectural review of these findings">Arch Review</button>
    <button class="btn ghost btn-sm" onclick="openReviewPane('uc','${esc(ucUuid)}','enhance')" title="Plan enhancements to address these findings">Enhancements</button>
    <span style="flex:1"></span>
    <span style="font-size:10px;color:var(--text-faint)" title="${esc(ucUuid)}">${esc(ucUuid.slice(0,8))}…</span>
  </div>`;
  actionBar.style.display = '';

  let html = `<div class="detail-pane">
    <div class="detail-title">${esc(ucHandle)}</div>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap">
      <span class="verdict-large ${verdictClass(verdict)}">${esc(verdict.replace(/_/g,' '))}</span>
      <span class="sev-label">${esc(conf)} confidence</span>
      ${sa ? `<span style="font-size:11px;color:var(--text-dim)">${sa.sample_count} samples · ${Object.entries(sa.verdict_votes||{}).map(([k,v])=>k.replace(/_/g,' ')+': '+v).join(', ')}</span>` : ''}
    </div>`;

  if (notes) {
    html += `<div class="detail-section">
      <div class="detail-section-title">Overall assessment</div>
      <div class="analysis-notes">${esc(notes)}</div>
    </div>`;
  }

  // Gaps — E3 advisory split: primary (quorum-backed) first, sub-quorum
  // findings in a separate advisory section so a reader never wades through
  // 1-of-N hedges to find the load-bearing findings (F2: 24/30 judge-rejected
  // findings were exactly those hedges).
  const primaryGaps = (gaps || []).filter(g => !g.advisory);
  const advisoryGaps = (gaps || []).filter(g => g.advisory);
  if (primaryGaps.length) {
    html += `<div class="detail-section"><div class="detail-section-title">Gaps identified (${primaryGaps.length})</div>`;
    _sortGapsBySeverity(primaryGaps).forEach(g => {
      const sev     = typeof g.severity==='object' ? g.severity : {};
      const sevLabel= sev.label || (typeof g.severity==='string' ? g.severity : 'minor');
      const sevBand = sev.band ? ' · '+sev.band : '';
      const gConf   = typeof g.confidence==='object' ? g.confidence.label : (g.confidence||'?');
      const refs    = g.spec_refs_consulted||[];
      html += `<div class="gap-card">
        <div class="gap-card-header">
          <span class="sev-label ${sevClass(sevLabel)}">${esc(sevLabel)}${esc(sevBand)}</span>
          <span style="font-size:10px;color:var(--text-faint)">${esc(gConf)} confidence</span>
        </div>
        <div class="gap-desc">${esc(g.description)}</div>
        ${g.rationale ? `<div class="gap-sub"><span class="gap-sub-label">Rationale</span>${esc(g.rationale)}</div>` : ''}
        ${g.recommendation ? `<div class="gap-recommendation"><span class="gap-sub-label">Recommendation</span>${esc(g.recommendation)}</div>` : ''}
        ${refs.length ? `<div class="gap-sub"><span class="gap-sub-label">Spec refs</span>${_specRefChips(refs)}</div>` : ''}
        ${g.spec_refs_missing ? `<div class="gap-sub" style="color:var(--red)"><span class="gap-sub-label" style="color:var(--red)">Missing from spec</span>${esc(g.spec_refs_missing)}</div>` : ''}
      </div>`;
    });
    html += `</div>`;
  }
  if (advisoryGaps.length) {
    html += `<div class="detail-section"><div class="detail-section-title" style="color:var(--text-faint)">Advisory (sub-quorum) findings (${advisoryGaps.length})</div>
      <div style="font-size:10px;color:var(--text-faint);margin:2px 0 8px;">Fewer than a majority of ensemble samples agreed on these. Kept for taxonomy/roadmap review; they do not vote on the verdict or count as primary findings.</div>`;
    _sortGapsBySeverity(advisoryGaps).forEach(g => {
      const sevLabel = (typeof g.severity==='object' ? (g.severity.label||'minor') : (g.severity||'minor'));
      html += `<div class="gap-card" style="opacity:.7">
        <div class="gap-card-header">
          <span class="sev-label ${sevClass(sevLabel)}">${esc(sevLabel)}</span>
          <span style="font-size:10px;color:var(--text-faint)">consensus ${esc(g.consensus||'?')}</span>
        </div>
        <div class="gap-desc">${esc(g.description||g.title||'')}</div>
      </div>`;
    });
    html += '</div>';
  }

  // Components (collapsible)
  if (comps.length) {
    html += _collapsibleAnalysisSection('Components required', comps.map(c => {
      const cConf = typeof c.confidence==='object' ? c.confidence.label : (c.confidence||'?');
      return `<div class="comp-row">
        <div class="comp-id">${esc(c.id)}</div>
        <div class="comp-role">${esc(c.role)}</div>
        ${c.rationale ? `<div class="comp-rat">${esc(c.rationale)}</div>` : ''}
        <div style="margin-top:5px;display:flex;align-items:center;gap:4px;flex-wrap:wrap">
          <span class="sev-label">${esc(cConf)}</span>${_specRefChips(c.spec_refs)}
        </div></div>`;
    }));
  }

  // Data model (collapsible)
  if (dm.length) {
    html += _collapsibleAnalysisSection('Data model touched', dm.map(d =>
      `<div class="comp-row">
        <div class="comp-id">${esc(d.entity)}</div>
        <div class="comp-role">${esc((d.fields_accessed||[]).join(', '))} · ops: ${esc((d.operations||[]).join(', '))}</div>
        ${d.rationale ? `<div class="comp-rat">${esc(d.rationale)}</div>` : ''}
        ${(d.spec_refs||[]).length ? `<div style="margin-top:5px">${_specRefChips(d.spec_refs)}</div>` : ''}
      </div>`
    ));
  }

  // Capabilities (collapsible)
  if (caps.length) {
    html += _collapsibleAnalysisSection('Capabilities invoked', caps.map(c =>
      `<div class="comp-row">
        <div class="comp-id">${esc(c.id)}</div>
        <div class="comp-role">${esc(c.usage)}</div>
        ${c.rationale ? `<div class="comp-rat">${esc(c.rationale)}</div>` : ''}
        ${(c.spec_refs||[]).length ? `<div style="margin-top:5px">${_specRefChips(c.spec_refs)}</div>` : ''}
      </div>`
    ));
  }

  // Policy modes (collapsible)
  if (pols.length) {
    html += _collapsibleAnalysisSection('Policy modes required', pols.map(p =>
      `<div class="comp-row">
        <div class="comp-id">${esc(p.mode)}</div>
        ${p.rationale ? `<div class="comp-rat">${esc(p.rationale)}</div>` : ''}
        ${(p.spec_refs||[]).length ? `<div style="margin-top:5px">${_specRefChips(p.spec_refs)}</div>` : ''}
      </div>`
    ));
  }

  // Run metadata
  const hasMetaRow = meta.model||meta.mode||meta.sample_count||meta.tool_call_count||meta.total_tokens||meta.wall_time_seconds||meta.engine_version||meta.endpoint_url;
  if (hasMetaRow) {
    html += `<div class="detail-section"><div class="detail-section-title">Analysis metadata</div><div class="kv-grid">
      ${meta.mode          ? `<div class="kv-label">mode</div><div class="kv-val">${esc(meta.mode)}</div>` : ''}
      ${meta.model         ? `<div class="kv-label">model</div><div class="kv-val">${esc(meta.model)}</div>` : ''}
      ${meta.sample_count  ? `<div class="kv-label">samples</div><div class="kv-val">${meta.sample_count}</div>` : ''}
      ${meta.tool_call_count ? `<div class="kv-label">tool calls</div><div class="kv-val">${meta.tool_call_count}</div>` : ''}
      ${meta.total_tokens  ? `<div class="kv-label">tokens</div><div class="kv-val">${(meta.total_tokens||0).toLocaleString()}</div>` : ''}
      ${meta.wall_time_seconds ? `<div class="kv-label">wall time</div><div class="kv-val">${_fmtSec(meta.wall_time_seconds)}</div>` : ''}
      ${meta.engine_version? `<div class="kv-label">engine</div><div class="kv-val">${esc(meta.engine_version)}</div>` : ''}
      ${meta.endpoint_url  ? `<div class="kv-label">endpoint</div><div class="kv-val" style="word-break:break-all">${esc(meta.endpoint_url)}</div>` : ''}
    </div></div>`;
  }

  html += '</div>'; // close detail-pane
  el.innerHTML = html;
}

function renderExploreAnalysis(data, ucUuid) {
  _lastAnalysisData = data;
  const samples  = data.samples || [];
  const ucEntry  = (activeRunSummary?.ucs||[]).find(u=>u.uc_uuid===ucUuid);
  const ucHandle = ucEntry?.uc_handle || ucUuid;
  const actionBar = document.getElementById('analysisActionBar');

  actionBar.innerHTML = `<div class="analysis-action-bar">
    <button class="btn ghost btn-sm" onclick="switchView('usecases');setTimeout(()=>editUC('${esc(ucUuid)}'),200)">✏ Edit UC</button>
    <button class="btn ghost btn-sm" onclick="_reanalyzeUC('${esc(ucUuid)}','${esc(ucHandle)}')">↺ Re-analyze</button>
    <button class="btn ghost btn-sm" onclick="openReviewPane('uc','${esc(ucUuid)}','review')" title="Get an architectural review of these findings">Arch Review</button>
    <button class="btn ghost btn-sm" onclick="openReviewPane('uc','${esc(ucUuid)}','enhance')" title="Plan enhancements to address these findings">Enhancements</button>
    <span style="flex:1"></span>
    <span style="font-size:10px;color:var(--text-faint)" title="${esc(ucUuid)}">${esc(ucUuid.slice(0,8))}…</span>
  </div>`;
  actionBar.style.display = '';

  let html = `<div class="detail-pane">
    <div class="detail-title">${esc(ucHandle)}</div>
    <div style="font-size:12px;color:var(--text-dim);margin-bottom:16px">Explore mode · ${samples.length} sample${samples.length===1?'':'s'}</div>`;

  if (data.variance) {
    const va = data.variance;
    const consensusVerdict = (va.sample_annotations||{}).consensus_verdict;
    if (consensusVerdict) {
      html += `<div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
        <span class="verdict-large ${verdictClass(consensusVerdict)}">${esc(consensusVerdict.replace(/_/g,' '))}</span>
        <span style="font-size:11px;color:var(--text-dim)">consensus</span></div>`;
    }
    html += `<div class="detail-section"><div class="detail-section-title">Variance report</div>
      <div class="analysis-block"><div class="analysis-block-body">
        <pre style="white-space:pre-wrap;word-break:break-word;font-size:11px;margin:0">${esc(JSON.stringify(va,null,2))}</pre>
      </div></div></div>`;
  }

  samples.forEach((s,i) => {
    const sv = (s.summary||{}).verdict || s.verdict || '?';
    const sNotes = (s.summary||{}).notes || s.overall_assessment || '';
    const sGaps  = s.gaps_identified || [];
    html += `<div class="detail-section"><div class="detail-section-title">Sample ${i}</div>
      <div class="analysis-block">
        <div class="analysis-block-header">
          <span class="${verdictClass(sv)}">${esc(sv.replace(/_/g,' '))}</span>
          ${sGaps.length ? `<span style="font-size:10px;color:var(--text-dim)">${sGaps.length} gap${sGaps.length===1?'':'s'}</span>` : ''}
        </div>
        ${sNotes ? `<div class="analysis-block-body">${esc(sNotes)}</div>` : ''}
      </div>`;
    if (sGaps.length) {
      _sortGapsBySeverity(sGaps).forEach(g => {
        const sev = typeof g.severity==='object' ? g.severity.label : (g.severity||'minor');
        html += `<div class="gap-card" style="margin-left:0">
          <div class="gap-card-header"><span class="sev-label ${sevClass(sev)}">${esc(sev)}</span></div>
          <div class="gap-desc">${esc(g.description)}</div>
          ${g.recommendation ? `<div class="gap-recommendation"><span class="gap-sub-label">Recommendation</span>${esc(g.recommendation)}</div>` : ''}
        </div>`;
      });
    }
    html += '</div>';
  });

  document.getElementById('analysisDetail').innerHTML = html + '</div>';
}

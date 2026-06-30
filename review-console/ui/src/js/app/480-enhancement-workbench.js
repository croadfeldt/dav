// ══════════════ Enhancement / PR Workbench (#138) ══════════════
// Loads the Enhancement Plan for the masthead scope, routes each parsed finding to its
// target enhancement-target repo (server-side, read-only preview), and lets the user select
// findings — per finding, per PR group, or in bulk — then opens one PR per repo via
// /api/enhancements/apply (selected_ids). Retarget unmatched namespaces inline.
let _ewData = null;

function _ewScopeName(){
  const v = _activeScope || '';
  if (v === '__unassigned__') return 'unassigned use cases';
  if (v){ const s=(allSets||[]).find(x=>String(x.id)===String(v)); return s?s.name:`Set ${v}`; }
  return 'all use cases';
}

async function _ewLoadPlan(){
  const { runId, scope } = _rpGetContext();
  const status = document.getElementById('ewStatus');
  const ta = document.getElementById('ewPlanText');
  if (status) status.textContent = 'Loading plan…';
  try {
    const d = await api(`/api/analysis/output?run_id=${encodeURIComponent(runId)}&kind=enhancement&scope=${scope}`);
    if (d && d.cached && d.content){
      ta.value = d.content;
      if (status) status.textContent = 'Plan loaded — click “Route into PRs →”.';
      return true;
    }
    if (status) status.textContent = 'No enhancement plan cached for this scope — generate one in Arch Review.';
    return false;
  } catch(e){ if (status) status.textContent = 'Load failed: ' + e.message; return false; }
}

function loadEnhancementWorkbench(){
  const chip = document.getElementById('ewScopeChip');
  if (chip) chip.textContent = 'Scope: ' + _ewScopeName();
  // Generation moved here from Arch Review: populate the enhancement model picker + surface the
  // cached plan (rpEnhStream + cache chip + the "Route into PRs ↓" row) for the active scope.
  try { _updateArchModelInfo(); } catch(_) {}
  try { _rpLoadCached(); } catch(_) {}
  const ta = document.getElementById('ewPlanText');
  if (ta && !ta.value.trim()) _ewLoadPlan();   // auto-load the workbench source once; routing is explicit
}

async function _ewRoute(){
  const status = document.getElementById('ewStatus');
  const text = (document.getElementById('ewPlanText')?.value || '').trim();
  if (!text){ if (status) status.textContent = 'No plan text — load or paste an Enhancement Plan first.'; return; }
  if (status) status.textContent = 'Routing…';
  // Make sure we have the enhancement-target repo list for the retarget pickers.
  if (!_codeRepos.length){ try { const r = await api('/api/repos?role=enhancement-target'); _codeRepos = r.repos || []; } catch {} }
  try {
    const d = await api('/api/enhancements/preview', { method:'POST', body: JSON.stringify({ enhancement_text: text }) });
    _ewData = d;
    _ewRenderGroups(d);
    if (status) status.textContent = `${d.total} finding(s) · ${d.groups.length} PR group(s)`
      + (d.unmatched.length ? ` · ${d.unmatched.length} unmatched` : '')
      + (d.no_target.length ? ` · ${d.no_target.length} targetless` : '')
      + (d.parse_errors.length ? ` · ⚠ ${d.parse_errors.length} parse error(s)` : '');
  } catch(e){ if (status) status.textContent = 'Route failed: ' + e.message; }
}

function _ewFindingRow(f, opts){
  opts = opts || {};
  const checkbox = opts.selectable !== false
    ? `<input type="checkbox" class="ew-fchk" data-id="${esc(f.id)}" data-ns="${esc(f.target_namespace||'')}" style="margin-top:3px;">`
    : `<span style="width:13px;display:inline-block;"></span>`;
  const tags = [];
  (f.gap_ids||[]).forEach(g => tags.push(`<span class="ew-tag">gap ${esc(g)}</span>`));
  (f.uc_handles||[]).forEach(u => tags.push(`<span class="ew-tag">${esc(u)}</span>`));
  if (f.action) tags.push(`<span class="ew-tag" style="background:var(--accent-dim,#2a3a4a);">${esc(f.action)}</span>`);
  const err = (f.parse_errors||[]).length ? `<div style="color:var(--red);font-size:10px;margin-top:2px;">⚠ ${(f.parse_errors||[]).map(esc).join('; ')}</div>` : '';
  const body = (f.content||'').trim();
  const acc = (f.acceptance||'').trim();
  return `<div style="display:flex;gap:8px;padding:7px 0;border-top:1px solid var(--border);">
    ${checkbox}
    <div style="flex:1;min-width:0;">
      <div style="font-size:12px;color:var(--text);"><strong>${esc(f.section_title || f.target_path || f.id)}</strong>
        <span style="color:var(--text-faint);font-size:10px;">${esc(f.target_path || f.target || '')}</span></div>
      <div style="margin:3px 0;">${tags.join(' ')}</div>
      ${f.rationale ? `<div style="font-size:11px;color:var(--text-dim);">${esc(f.rationale)}</div>` : ''}
      ${err}
      ${body || acc ? `<details style="margin-top:4px;"><summary style="cursor:pointer;font-size:10px;color:var(--text-faint);">View patch${acc?' + acceptance':''}</summary>
        ${body ? `<pre class="ew-pre">${esc(body)}</pre>` : ''}
        ${acc ? `<div style="font-size:10px;color:var(--text-faint);margin-top:3px;">Acceptance:</div><pre class="ew-pre">${esc(acc)}</pre>` : ''}
      </details>` : ''}
    </div>
  </div>`;
}

function _ewRepoOptions(selectedNs){
  const opts = _codeRepos.map(r => `<option value="${esc(r.uuid)}">${esc(r.display_name || r.namespace)}</option>`).join('');
  return `<option value="">— retarget to a repo —</option>` + opts;
}

function _ewRenderGroups(d){
  const wrap = document.getElementById('ewResults');
  const empty = document.getElementById('ewEmpty');
  const box = document.getElementById('ewGroups');
  document.getElementById('ewPrResults').innerHTML = '';
  if (!d || (!d.groups.length && !d.unmatched.length && !d.no_target.length)){
    if (wrap) wrap.style.display = 'none';
    if (empty){ empty.style.display = ''; empty.innerHTML = 'No findings parsed from this plan. Check the plan text (it must contain ENHANCEMENT blocks).'; }
    return;
  }
  if (empty) empty.style.display = 'none';
  if (wrap) wrap.style.display = '';
  let html = '';
  // Matched groups → one PR per repo
  d.groups.forEach((g, gi) => {
    html += `<div class="ew-group" data-ns="${esc(g.namespace)}" data-repo="${esc(g.repo.uuid)}">
      <div style="display:flex;gap:8px;align-items:center;background:var(--bg-raised);padding:7px 10px;border-radius:2px;">
        <input type="checkbox" class="ew-gchk" data-gi="${gi}" checked>
        <strong style="font-size:12px;">PR → ${esc(g.repo.name)}</strong>
        <span style="font-size:10px;color:var(--text-faint);">${esc(g.namespace)} · base ${esc(g.repo.branch||'main')} · ${g.findings.length} finding(s)</span>
        ${g.repo.url ? `<a href="${esc(g.repo.url)}" target="_blank" style="font-size:10px;margin-left:auto;">repo ↗</a>` : ''}
      </div>
      <div class="ew-flist" style="padding:2px 10px 8px;">${g.findings.map(f => _ewFindingRow(f)).join('')}</div>
    </div>`;
  });
  // Unmatched namespaces → need a retarget before they can be submitted
  d.unmatched.forEach(u => {
    html += `<div class="ew-group ew-unmatched" data-ns="${esc(u.namespace)}">
      <div style="display:flex;gap:8px;align-items:center;background:var(--bg-raised);padding:7px 10px;border-radius:2px;border-left:2px solid var(--amber,#b8860b);">
        <strong style="font-size:12px;">${esc(u.namespace)}</strong>
        <span style="font-size:10px;color:var(--amber,#b8860b);">${esc(u.reason)}</span>
        <select class="ew-retarget" data-ns="${esc(u.namespace)}" style="margin-left:auto;font-size:11px;">${_ewRepoOptions()}</select>
      </div>
      <div class="ew-flist" style="padding:2px 10px 8px;">${u.findings.map(f => _ewFindingRow(f)).join('')}</div>
    </div>`;
  });
  // Targetless findings → informational, can't be submitted
  if (d.no_target.length){
    html += `<div class="ew-group">
      <div style="background:var(--bg-raised);padding:7px 10px;border-radius:2px;border-left:2px solid var(--text-faint);">
        <strong style="font-size:12px;">No target</strong>
        <span style="font-size:10px;color:var(--text-faint);">these findings name no <code>target:</code> repo — fix the plan to route them</span>
      </div>
      <div class="ew-flist" style="padding:2px 10px 8px;">${d.no_target.map(f => _ewFindingRow(f, {selectable:false})).join('')}</div>
    </div>`;
  }
  box.innerHTML = html;
  _ewWireSelection();
  _ewUpdateSel();
}

function _ewWireSelection(){
  // Group checkbox toggles all its findings.
  document.querySelectorAll('#ewGroups .ew-gchk').forEach(gc => {
    gc.addEventListener('change', function(){
      const grp = this.closest('.ew-group');
      grp.querySelectorAll('.ew-fchk').forEach(fc => { fc.checked = gc.checked; });
      _ewUpdateSel();
    });
  });
  document.querySelectorAll('#ewGroups .ew-fchk').forEach(fc => fc.addEventListener('change', _ewUpdateSel));
  // A retarget pick lets that previously-unmatched group be submitted.
  document.querySelectorAll('#ewGroups .ew-retarget').forEach(rt => rt.addEventListener('change', _ewUpdateSel));
}

function _ewSelectedIds(){
  return Array.from(document.querySelectorAll('#ewGroups .ew-fchk:checked')).map(c => c.dataset.id);
}
function _ewRepoOverrides(){
  const ov = {};
  document.querySelectorAll('#ewGroups .ew-retarget').forEach(rt => { if (rt.value) ov[rt.dataset.ns] = rt.value; });
  return ov;
}

function _ewUpdateSel(){
  const ids = _ewSelectedIds();
  document.getElementById('ewSelCount').textContent = String(ids.length);
  // Count PR groups that have ≥1 selected finding + a resolvable repo.
  const ov = _ewRepoOverrides();
  let repos = new Set();
  let blocked = 0;
  document.querySelectorAll('#ewGroups .ew-group').forEach(grp => {
    const sel = grp.querySelectorAll('.ew-fchk:checked').length;
    if (!sel) return;
    const ns = grp.dataset.ns;
    if (grp.classList.contains('ew-unmatched')){
      if (ov[ns]) repos.add(ov[ns]); else blocked += sel;
    } else if (grp.dataset.repo){ repos.add(grp.dataset.repo); }
  });
  const sum = document.getElementById('ewRouteSummary');
  if (sum) sum.textContent = ids.length
    ? `→ ${repos.size} PR(s)` + (blocked ? ` · ${blocked} need a repo (retarget above)` : '')
    : '';
  const btn = document.getElementById('ewSubmitBtn');
  if (btn) btn.disabled = !ids.length;
}

async function _ewSubmit(){
  const ids = _ewSelectedIds();
  if (!ids.length) return;
  const ov = _ewRepoOverrides();
  const status = document.getElementById('ewStatus');
  const out = document.getElementById('ewPrResults');
  // Block submit if any selected unmatched group still lacks a repo.
  let blocked = false;
  document.querySelectorAll('#ewGroups .ew-group.ew-unmatched').forEach(grp => {
    if (grp.querySelectorAll('.ew-fchk:checked').length && !ov[grp.dataset.ns]) blocked = true;
  });
  if (blocked){ if (status) status.textContent = 'Some selected findings have no target repo — retarget them or deselect.'; return; }
  if (!confirm(`Open pull request(s) for ${ids.length} selected finding(s)? This pushes branches and opens PRs on the target repos.`)) return;
  if (status) status.textContent = 'Creating PRs…';
  const text = (document.getElementById('ewPlanText')?.value || '').trim();
  try {
    const r = await api('/api/enhancements/apply', { method:'POST', body: JSON.stringify({
      enhancement_text: text, selected_ids: ids, repo_overrides: ov,
      scope: 'set', pr_title: `DAV enhancements — ${_ewScopeName()}`,
    }) });
    _ewRenderPrResults(r);
    if (status) status.textContent = 'Done.';
  } catch(e){ if (status) status.textContent = 'Submit failed: ' + e.message; }
}

function _ewRenderPrResults(r){
  const out = document.getElementById('ewPrResults');
  if (!out) return;
  let html = '<div class="rp-section-title" style="margin-top:6px;">Pull requests</div>';
  (r.repo_results || r.results || []).forEach(pr => {
    const ok = pr.pr_url || pr.url;
    html += `<div style="font-size:12px;padding:5px 0;border-top:1px solid var(--border);">
      ${ok ? '✅' : '⚠'} <strong>${esc(pr.repo || pr.namespace || '')}</strong>
      ${ok ? `<a href="${esc(ok)}" target="_blank">${esc(ok)}</a>` : esc(pr.error || pr.message || 'no PR created')}
      ${pr.files ? `<span style="color:var(--text-faint);"> · ${pr.files} file(s)</span>` : ''}
    </div>`;
  });
  (r.unmatched_namespaces || []).forEach(u => {
    html += `<div style="font-size:11px;color:var(--amber,#b8860b);padding:3px 0;">⚠ ${esc(u.namespace || u)} — no target repo</div>`;
  });
  (r.apply_warnings || []).forEach(w => {
    html += `<div style="font-size:10px;color:var(--text-faint);">• ${esc(w)}</div>`;
  });
  out.innerHTML = html;
}

document.getElementById('ewLoadBtn')?.addEventListener('click', _ewLoadPlan);
document.getElementById('ewRouteBtn')?.addEventListener('click', _ewRoute);
document.getElementById('ewSubmitBtn')?.addEventListener('click', _ewSubmit);
document.getElementById('ewSelectAllBtn')?.addEventListener('click', () => {
  document.querySelectorAll('#ewGroups .ew-fchk').forEach(c => { c.checked = true; });
  document.querySelectorAll('#ewGroups .ew-gchk').forEach(c => { c.checked = true; });
  _ewUpdateSel();
});
document.getElementById('ewSelectNoneBtn')?.addEventListener('click', () => {
  document.querySelectorAll('#ewGroups .ew-fchk, #ewGroups .ew-gchk').forEach(c => { c.checked = false; });
  _ewUpdateSel();
});

document.getElementById('rpRevRunBtn')?.addEventListener('click', async () => {
  const { runId, scope, ucUuid, setId } = _rpGetContext();
  if (!runId) { toast('Pick a Scoping Set in the masthead first'); return; }
  // Warn before discarding a cached generation (real cache only, not the hint).
  if (_rpCached.review &&
      !confirm('Re-generate the Architectural Review and replace the cached result?')) return;
  const body = { scope, run_id: runId, set_id: setId, ..._overrideModelBody('rpRevModelSel') };
  if (scope === 'uc') body.uc_uuid = ucUuid;
  _reviewCtx = { runId, ucUuid };
  const _ok = await _runStream({
    endpoint: '/api/arch-review', body,
    streamEl:     document.getElementById('rpRevStream'),
    statusEl:     document.getElementById('rpRevStatus'),
    copyBtn:      document.getElementById('rpRevCopyBtn'),
    runBtn:       document.getElementById('rpRevRunBtn'),
    reasoningBtn: document.getElementById('rpRevReasoningBtn'),
    nextRow:      null,
    rawHolder:    _rpRevRaw,
    showReasoningKey: 'rpRevShowReasoning',
    renderMode: 'markdown',
  });
  _rpRefreshChip('review');   // chip only — never hides the just-rendered pane
  if (_ok) toast('✓ Architectural Review ready');   // notify even if you navigated away
});

document.getElementById('rpEnhRunBtn')?.addEventListener('click', async () => {
  const { runId, scope, ucUuid, setId } = _rpGetContext();
  if (!runId) { toast('Pick a Scoping Set in the masthead first'); return; }
  if (_rpCached.enhancement &&
      !confirm('Re-generate the Enhancement Plan and replace the cached result?')) return;
  const body = { scope, run_id: runId, set_id: setId, ..._overrideModelBody('rpEnhModelSel') };
  if (scope === 'uc') body.uc_uuid = ucUuid;
  _reviewCtx = { runId, ucUuid };
  const _ok = await _runStream({
    endpoint: '/api/enhancements', body,
    streamEl:     document.getElementById('rpEnhStream'),
    statusEl:     document.getElementById('rpEnhStatus'),
    copyBtn:      document.getElementById('rpEnhCopyBtn'),
    runBtn:       document.getElementById('rpEnhRunBtn'),
    reasoningBtn: document.getElementById('rpEnhReasoningBtn'),
    nextRow:      document.getElementById('rpEnhNextRow'),
    rawHolder:    _rpEnhRaw,
    showReasoningKey: 'rpEnhShowReasoning',
    renderMode: 'enhancement',
  });
  _rpRefreshChip('enhancement');   // chip only — never hides the just-rendered pane
  if (_ok) toast('✓ Enhancement Plan ready');   // notify even if you navigated away
});

// ── Audit log (F3): who did what + auth events ───────────────────────────────
function _auOutcomeColor(o) {
  return o === 'denied' ? 'var(--amber,gold)'
    : (o === 'error' || o === 'failure') ? 'var(--red)' : 'var(--green)';
}
async function loadAudit() {
  const box = document.getElementById('auTable');
  const status = document.getElementById('auStatus');
  const qs = new URLSearchParams();
  const v = id => (document.getElementById(id).value || '').trim();
  if (v('auActor')) qs.set('actor', v('auActor'));
  if (v('auAction')) qs.set('action', v('auAction'));
  if (v('auOutcome')) qs.set('outcome', v('auOutcome'));
  if (v('auHours')) qs.set('hours', v('auHours'));
  qs.set('limit', '300');
  box.innerHTML = '<div class="empty">loading…</div>'; status.textContent = '';
  let data;
  try { data = await api('/api/audit?' + qs.toString()); }
  catch (e) { box.innerHTML = `<div style="color:var(--red);font-size:11px;padding:10px;">${esc(e.message)}</div>`; return; }
  const ev = data.events || [];
  if (!ev.length) { box.innerHTML = '<div class="empty">No audit events match.</div>'; return; }
  const th = (t) => `<th style="padding:5px 8px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--bg-panel);text-align:left;font-weight:600;">${t}</th>`;
  let h = '<table style="width:100%;border-collapse:collapse;font-size:11px;"><thead><tr>'
    + th('When') + th('Actor') + th('Action') + th('Object') + th('Outcome') + th('Path') + th('Detail') + th('IP') + '</tr></thead><tbody>';
  for (const e of ev) {
    const when = e.ts ? new Date(e.ts).toLocaleString() : '';
    // Object (what was acted on) + structured detail (e.g. a delete's propagation impact) — #103.
    const obj = e.object_type ? esc(e.object_type) + (e.object_id ? ' ' + esc(String(e.object_id)) : '') : '';
    const detailFull = (e.detail && typeof e.detail === 'object') ? JSON.stringify(e.detail, null, 2) : '';
    const detailShort = (e.detail && typeof e.detail === 'object') ? JSON.stringify(e.detail) : '';
    h += '<tr style="border-bottom:1px solid var(--border);">'
      + `<td style="padding:4px 8px;white-space:nowrap;color:var(--text-dim);" title="${esc(e.ts || '')}">${esc(when)}</td>`
      + `<td style="padding:4px 8px;white-space:nowrap;font-family:var(--mono,monospace);">${esc(e.actor || '—')}${e.actor_source ? ` <span style="color:var(--text-faint);">(${esc(e.actor_source)})</span>` : ''}</td>`
      + `<td style="padding:4px 8px;white-space:nowrap;">${esc(e.action || '')}</td>`
      + `<td style="padding:4px 8px;white-space:nowrap;font-family:var(--mono,monospace);color:var(--text-dim);">${obj}</td>`
      + `<td style="padding:4px 8px;color:${_auOutcomeColor(e.outcome)};">${esc(e.outcome || '')}${e.status_code ? ` <span style="color:var(--text-faint);">${e.status_code}</span>` : ''}</td>`
      + `<td style="padding:4px 8px;font-family:var(--mono,monospace);color:var(--text-dim);">${esc(e.path || e.summary || '')}</td>`
      + `<td style="padding:4px 8px;font-family:var(--mono,monospace);color:var(--text-faint);max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:${detailShort ? 'help' : 'default'};" title="${esc(detailFull)}">${esc(detailShort)}</td>`
      + `<td style="padding:4px 8px;white-space:nowrap;color:var(--text-faint);">${esc(e.ip || '')}</td></tr>`;
  }
  h += '</tbody></table>';
  box.innerHTML = h;
  status.textContent = `${ev.length} event${ev.length === 1 ? '' : 's'}`;
}

// ── Assessments (F7): ingest assessment outputs → capability findings ────────
let _asSel = null;
function _asStateColor(s) {
  return s === 'absent' ? 'var(--red)' : s === 'partial' ? 'var(--amber,gold)'
    : s === 'n/a' ? 'var(--text-faint)' : 'var(--green)';
}
// Pure maturity 1–5 + N/A. Deliberately darkened palette (esp. the 2 "gold") with white
// text + a subtle dark outline so every bubble reads on light OR dark panels; the target
// (3) gets a green ring. 1 red → 2 dark-gold → 3 green (target) → 5 deep green; N/A neutral.
const _MAT_COLORS = { 1:'#c0392b', 2:'#b8860b', 3:'#3fae4a', 4:'#2e8b3d', 5:'#1d6e2e' };
function _matBubble(m, target) {
  if (m === null || m === undefined)
    return `<span title="N/A — not asked / not applicable" style="display:inline-block;min-width:28px;text-align:center;padding:1px 7px;border-radius:9px;border:1px dashed var(--text-faint);color:var(--text-faint);font-size:10px;">N/A</span>`;
  const bg = _MAT_COLORS[m] || 'var(--text-faint)';
  const isTarget = (target !== null && target !== undefined && m === target);
  const ring = isTarget ? 'box-shadow:0 0 0 2px rgba(63,174,74,0.5);' : '';
  return `<span title="maturity ${m}${isTarget ? ' (engagement target)' : ''}" style="display:inline-block;min-width:18px;text-align:center;padding:1px 7px;border-radius:9px;background:${bg};color:#fff;border:1px solid rgba(0,0,0,0.35);font-weight:700;font-size:10px;${ring}">${m}</span>`;
}
function _matLegend(scale, target) {
  if (!scale || !scale.length) return '';
  return `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:9px;color:var(--text-faint);margin-top:6px;"><span>maturity:</span>`
    + scale.map(s => `<span title="${esc(s.desc || '')}" style="display:inline-flex;align-items:center;gap:3px;">${_matBubble(s.value, target)} ${esc(s.label)}${s.target ? ' ◄target' : ''}</span>`).join('')
    + `<span style="display:inline-flex;align-items:center;gap:3px;">${_matBubble(null)} no maturity (absent / N/A)</span></div>`;
}
// A capability "pill": name colored by maturity heat; falls back to state color when there's
// no maturity (absent = muted red; n/a = neutral dashed; the target maturity gets a ring).
function _capPill(f, target) {
  const m = f.maturity;
  let bg, txt = '#fff', border = '1px solid rgba(0,0,0,0.3)', extra = '';
  if (m !== null && m !== undefined) {
    bg = _MAT_COLORS[m] || 'var(--text-faint)';
    if (target !== null && target !== undefined && m === target) extra = 'box-shadow:0 0 0 2px rgba(63,174,74,0.5);';
  } else if (f.state === 'n/a') { bg = 'transparent'; txt = 'var(--text-faint)'; border = '1px dashed var(--text-faint)'; }
  else if (f.state === 'absent') { bg = '#8a3a32'; }       // asked, no capability
  else if (f.state === 'partial') { bg = '#b8860b'; }
  else { bg = '#2e8b3d'; }
  const badge = (m !== null && m !== undefined) ? ('m' + m) : (f.state === 'n/a' ? 'N/A' : esc(f.state));
  const gapMark = (!f.normalized_to) ? ` <span title="taxonomy gap (back-fill candidate)" style="opacity:0.85;">°</span>` : '';
  const tip = [f.state.toUpperCase(), f.evidence || f.notes || '',
               f.normalized_to ? ('→ ' + f.normalized_to) : (f.normalization_status || '')].filter(Boolean).join(' · ');
  return `<div title="${esc(tip)}" style="display:flex;align-items:center;gap:8px;padding:4px 10px;border-radius:12px;background:${bg};color:${txt};border:${border};${extra}margin:3px 0;font-size:11px;">`
    + `<span style="font-weight:600;">${esc(f.capability_handle)}${gapMark}</span>`
    + `<span style="margin-left:auto;font-size:9px;opacity:0.92;text-transform:uppercase;letter-spacing:0.04em;">${badge}</span></div>`;
}
// Masthead quick selector — jump to an assessment in the active project (mirrors the
// project + run selectors). Hidden unless the user can view assessments and some exist.
async function loadAssessmentSelector() {
  const chip = document.getElementById('assessmentChip');
  const sel = document.getElementById('globalAssessmentSel');
  if (!chip || !sel) return;
  if (!can('assessment.view')) { chip.style.display = 'none'; return; }
  try {
    const data = await api('/api/assessments');
    const items = data.assessments || [];
    sel.innerHTML = items.length
      ? '<option value="">— select —</option>' +
        items.map(a => `<option value="${esc(a.id)}">${esc(a.handle)}${a.gaps ? ` (${a.gaps} gaps)` : ''}</option>`).join('')
      : '<option value="">— no assessments —</option>';
    chip.style.display = '';   // always visible to assessment viewers (placeholder when empty)
  } catch (e) { chip.style.display = 'none'; }
}
// Blueprint quick selector — populated once blueprints (task #95) exist; hidden until then.
async function loadBlueprintSelector() {
  const chip = document.getElementById('blueprintChip');
  if (chip) chip.style.display = 'none';   // no blueprint projects yet
}
document.getElementById('globalAssessmentSel')?.addEventListener('change', function () {
  const id = this.value;
  if (!id) return;
  switchView('assess');
  renderAssessment(id);
});

// ════════════════ IMPROVE — self-improvement: diagnose & propose ════════════════
// Review queue for the typed change proposals the diagnoser (diagnose.py) files
// against failed runs. Review-only: accept/reject does NOT apply a change.
const _improveState = { mode: 'proposals', list: [], selectedId: null, statusFilter: 'proposed',
                        experiments: [], selectedExpId: null, bound: false };

function _improveBind() {
  if (_improveState.bound) return;
  _improveState.bound = true;
  document.getElementById('improveRefreshBtn')?.addEventListener('click', loadImproveQueue);
  document.getElementById('improveDiagnoseBtn')?.addEventListener('click', diagnoseSelectedRun);
  document.querySelectorAll('.improve-status-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.improve-status-chip').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _improveState.statusFilter = btn.dataset.status;
      loadImproveQueue();
    });
  });
  document.querySelectorAll('.improve-mode-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.improve-mode-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _improveState.mode = btn.dataset.mode;
      // The status chips + diagnose control only apply to the proposals view.
      const propCtl = document.getElementById('improveRunSelect')?.closest('div');
      loadImproveQueue();
    });
  });
}

async function loadImproveQueue() {
  _improveBind();
  if (_improveState.mode === 'experiments') return loadExperiments();
  // Populate the "diagnose a run" dropdown (failed runs first — those have signatures).
  try {
    const resp = await api('/api/runs').catch(() => ({ runs: [] }));
    const runs = (resp.runs || []).slice();
    runs.sort((a, b) => (a.phase === 'Failed' ? -1 : 0) - (b.phase === 'Failed' ? -1 : 0));
    const sel = document.getElementById('improveRunSelect');
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">Diagnose an analysis…</option>' +
        runs.slice(0, 40).map(r =>
          `<option value="${esc(r.name)}">${r.phase === 'Failed' ? '⚠ ' : ''}${esc(r.name)} · ${esc(r.phase || '')}</option>`
        ).join('');
      sel.value = cur;
    }
  } catch (e) { /* dropdown best-effort */ }

  // Load the proposal queue.
  const qs = _improveState.statusFilter ? `?status=${encodeURIComponent(_improveState.statusFilter)}` : '';
  try {
    const data = await api(`/api/improvement-proposals${qs}`);
    _improveState.list = data.proposals || [];
    renderImproveList();
    // Badge: count of proposed (unreviewed) across all.
    const pending = await api('/api/improvement-proposals?status=proposed').catch(() => ({ count: 0 }));
    const badge = document.getElementById('badgeImprove');
    if (badge) badge.textContent = pending.count ? pending.count : '';
  } catch (e) {
    document.getElementById('improveList').innerHTML =
      `<div class="empty" style="padding:22px;text-align:center;color:var(--red);">${esc(e.message)}</div>`;
  }
}

function _confColor(c) { return c === 'high' ? 'var(--green)' : (c === 'medium' ? 'var(--accent)' : 'var(--text-faint)'); }
// Compact relative time (e.g. "3d ago", "just now") from an ISO string.
function _ago(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime(); if (!t) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  const m = s / 60; if (m < 60) return `${Math.floor(m)}m ago`;
  const h = m / 60; if (h < 24) return `${Math.floor(h)}h ago`;
  const d = h / 24; if (d < 30) return `${Math.floor(d)}d ago`;
  const mo = d / 30; if (mo < 12) return `${Math.floor(mo)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}
// Proposal lifecycle action → display label + dot colour (Activity timeline).
function _propActionLabel(a) {
  return ({ proposed: 'Proposed', 'proposal.accepted': 'Accepted', 'proposal.rejected': 'Rejected',
            'proposal.applied': 'Applied' })[a] || a;
}
function _propActionColor(a) {
  return a === 'proposal.accepted' ? 'var(--green)'
    : a === 'proposal.rejected' ? 'var(--red)'
    : a === 'proposal.applied' ? 'var(--blue,#4a90c9)' : 'var(--text-faint)';
}
function _statusBadge(s) {
  const col = s === 'accepted' ? 'var(--green)' : (s === 'rejected' ? 'var(--red)' : (s === 'applied' ? 'var(--blue,#4a90c9)' : 'var(--text-faint)'));
  const bg = s === 'accepted' ? 'rgba(123,168,79,0.12)' : (s === 'rejected' ? 'rgba(217,101,58,0.12)' : 'transparent');
  return `<span style="color:${col};background:${bg};padding:1px 5px;border-radius:2px;font-size:9px;text-transform:uppercase;letter-spacing:.05em;">${esc(s)}</span>`;
}

function renderImproveList() {
  const el = document.getElementById('improveList');
  if (!_improveState.list.length) {
    el.innerHTML = `<div class="empty" style="padding:22px;text-align:center;color:var(--text-faint);font-size:12px;">No proposals${_improveState.statusFilter ? ` (${esc(_improveState.statusFilter)})` : ''}. Diagnose an analysis to generate some.</div>`;
    return;
  }
  el.innerHTML = _improveState.list.map(p => {
    const active = p.id === _improveState.selectedId;
    return `
      <div class="improve-row" data-id="${p.id}"
           style="padding:10px 14px;border-bottom:1px solid var(--border);cursor:pointer;
                  ${active ? 'background:var(--bg-raised);border-left:2px solid var(--accent);' : 'border-left:2px solid transparent;'}"
           onclick="selectProposal(${p.id})">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;">
          <div style="font-size:11px;font-weight:600;display:flex;align-items:center;gap:6px;min-width:0;">
            <span style="width:7px;height:7px;border-radius:50%;background:${_confColor(p.confidence)};flex-shrink:0;" title="${esc(p.confidence)} confidence"></span>
            <span style="background:var(--bg-raised);border:1px solid var(--border);padding:0 5px;border-radius:2px;font-size:9px;text-transform:uppercase;flex-shrink:0;">${esc(p.kind)}</span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(p.target || '')}</span>
          </div>
          ${_statusBadge(p.status)}
        </div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:3px;">
          ${esc(p.signature_class || '')} · ${esc(p.source)} · ${esc((p.session_name || p.run_name || p.run_id || '').slice(0, 28))}
        </div>
        <div style="font-size:9px;color:var(--text-faint);margin-top:2px;" title="${esc(p.created_at ? new Date(p.created_at).toLocaleString() : '')}">
          proposed ${esc(_ago(p.created_at))}${p.reviewed_at && p.status !== 'proposed' ? ` · ${esc(p.status)} ${esc(_ago(p.reviewed_at))}` : ''}
        </div>
      </div>`;
  }).join('');
}

async function selectProposal(id) {
  _improveState.selectedId = id;
  renderImproveList();
  const p = _improveState.list.find(x => x.id === id);
  if (!p) return;
  const body = document.getElementById('improveDetailBody');
  document.getElementById('improveDetailEmpty').style.display = 'none';
  body.style.display = '';
  const reviewable = p.status === 'proposed';
  body.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;border-bottom:1px solid var(--border);padding-bottom:10px;">
      <div style="min-width:0;">
        <div style="font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span style="width:8px;height:8px;border-radius:50%;background:${_confColor(p.confidence)};"></span>
          <span style="background:var(--bg-raised);border:1px solid var(--border);padding:1px 6px;border-radius:2px;font-size:10px;text-transform:uppercase;">${esc(p.kind)}</span>
          <span>${esc(p.target || '')}</span>
        </div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:4px;">
          ${esc(p.confidence)} confidence · source: ${esc(p.source)} · signature: ${esc(p.signature_class || '—')}
        </div>
      </div>
      ${_statusBadge(p.status)}
    </div>
    <div style="display:flex;flex-direction:column;gap:12px;margin-top:12px;">
      ${_detailBlock('Proposed change', p.proposed_change)}
      ${_detailBlock('Rationale', p.rationale)}
      ${_detailBlock('Predicted effect', p.predicted_effect)}
      <div style="font-size:10px;color:var(--text-faint);">
        From run <span style="color:var(--text-dim);">${esc(p.session_name || p.run_name || p.run_id || '')}</span>
      </div>
      <div>
        <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px;">Activity</div>
        <div id="improveActivity"><div style="font-size:10px;color:var(--text-faint);">loading…</div></div>
      </div>
    </div>
    ${reviewable ? `
      <div style="display:flex;gap:8px;align-items:center;margin-top:16px;padding-top:12px;border-top:1px solid var(--border);">
        <input id="improveReviewNote" placeholder="optional note…" style="flex:1;font-size:11px;padding:5px 8px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);">
        <button class="btn" style="background:var(--green);color:#fff;" onclick="reviewProposal(${p.id}, 'accepted')">✓ Accept</button>
        <button class="btn ghost" id="improveRejectBtn-${p.id}" onclick="_armDeleteBtn(this, () => reviewProposal(${p.id}, 'rejected'))">✕ Reject</button>
      </div>
      <div style="font-size:9px;color:var(--text-faint);margin-top:6px;">Review only — accepting does not apply the change (that is Phase 2). It flags the proposal for an operator or the future auto-apply gate.</div>
    ` : ''}
    ${p.change_spec && p.change_spec.type === 'max_tokens' ? `
      <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border);">
        <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;margin-bottom:6px;">🔬 A/B experiment (Phase 2)</div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          <label style="font-size:11px;display:flex;align-items:center;gap:4px;">candidate max_tokens
            <input id="abCandidate" type="number" value="${p.change_spec.direction === 'lower' ? Math.round((p.change_spec.current||10240)*0.625) : Math.round((p.change_spec.current||10240)*1.25)}" style="width:84px;font-size:11px;padding:3px 6px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);">
          </label>
          <select id="abSet" style="font-size:11px;padding:3px 6px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);"><option value="">eval set…</option></select>
          <button class="btn primary btn-sm" onclick="launchExperiment(${p.id}, parseInt(document.getElementById('abCandidate').value), parseInt(document.getElementById('abSet').value)||null)">🔬 A/B</button>
        </div>
        <div style="font-size:9px;color:var(--text-faint);margin-top:4px;">Evaluates baseline (current config) + candidate (this max_tokens) on the eval set; the gate auto-reverts a regression OR a new failure mode. Candidate is a per-analysis override — production + spamllm untouched.</div>
      </div>` : ''}`;
  if (p.change_spec && p.change_spec.type === 'max_tokens') _populateAbSets();
  _loadProposalActivity(p.id);
}

// Load + render the proposal's lifecycle timeline (proposed → accept/reject → applied).
async function _loadProposalActivity(pid) {
  const box = document.getElementById('improveActivity');
  if (!box) return;
  try {
    const data = await api(`/api/improvement-proposals/${pid}/activity`);
    const items = data.activity || [];
    box.innerHTML = items.map(ev => {
      const when = ev.at ? new Date(ev.at).toLocaleString() : '';
      const d = ev.detail || {};
      const note = d.note ? ` — “${esc(d.note)}”` : '';
      const via = d.via ? ` (via ${esc(d.via)}${d.experiment_id ? ` #${esc(String(d.experiment_id))}` : ''})` : '';
      return `<div style="display:flex;gap:8px;align-items:baseline;font-size:11px;padding:3px 0;">
        <span style="width:8px;height:8px;border-radius:50%;background:${_propActionColor(ev.action)};flex-shrink:0;position:relative;top:2px;"></span>
        <div style="min-width:0;line-height:1.4;">
          <span style="color:var(--text);font-weight:600;">${esc(_propActionLabel(ev.action))}</span>${ev.actor ? `<span style="color:var(--text-faint);"> by ${esc(ev.actor)}</span>` : ''}<span style="color:var(--text-faint);">${via}</span>
          <span style="color:var(--text-faint);" title="${esc(ev.at || '')}"> · ${esc(_ago(ev.at))}</span>
          <span style="color:var(--text-faint);font-size:9px;"> (${esc(when)})</span>${note ? `<span style="font-style:italic;color:var(--text-dim);">${note}</span>` : ''}
        </div>
      </div>`;
    }).join('') || '<div style="font-size:10px;color:var(--text-faint);">No activity recorded.</div>';
  } catch (e) {
    box.innerHTML = '<div style="font-size:10px;color:var(--text-faint);">activity unavailable</div>';
  }
}

async function _populateAbSets() {
  try {
    const data = await api('/api/sets');
    const sel = document.getElementById('abSet');
    if (!sel) return;
    sel.innerHTML = '<option value="">eval set…</option>' +
      (data.sets || []).map(s => `<option value="${s.id}">${esc(s.name)} (${s.member_count})</option>`).join('');
  } catch (e) { /* sets best-effort */ }
}

function _detailBlock(label, text) {
  return `<div>
    <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px;">${esc(label)}</div>
    <div style="font-size:12px;line-height:1.5;color:var(--text);white-space:pre-wrap;">${esc(text || '—')}</div>
  </div>`;
}

async function reviewProposal(id, status) {
  const note = document.getElementById('improveReviewNote')?.value || null;
  try {
    await api(`/api/improvement-proposals/${id}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, note }),
    });
    toast(`Proposal ${status}`, false);
    await loadImproveQueue();
    // Re-select to show the updated status, if still in the filtered list.
    if (_improveState.list.find(x => x.id === id)) selectProposal(id);
    else { _improveState.selectedId = null;
           document.getElementById('improveDetailBody').style.display = 'none';
           document.getElementById('improveDetailEmpty').style.display = ''; }
  } catch (e) { toast(`Review failed: ${e.message}`, true); }
}

async function diagnoseSelectedRun() {
  const sel = document.getElementById('improveRunSelect');
  const run = sel?.value;
  if (!run) { toast('Pick an analysis to diagnose', true); return; }
  const useLlm = document.getElementById('improveLLMChk')?.checked ? 'true' : 'false';
  const btn = document.getElementById('improveDiagnoseBtn');
  const orig = btn.textContent; btn.textContent = '…'; btn.disabled = true;
  try {
    const r = await api(`/api/diagnose/${encodeURIComponent(run)}?use_llm=${useLlm}`, { method: 'POST' });
    const n = (r.proposals || []).length;
    toast(`Diagnosed: ${n} proposal${n === 1 ? '' : 's'}${r.used_llm ? ' (incl. LLM)' : ''}`, false);
    // Show proposed regardless of current filter so the new ones are visible.
    document.querySelectorAll('.improve-status-chip').forEach(b => b.classList.toggle('active', b.dataset.status === 'proposed'));
    _improveState.statusFilter = 'proposed';
    await loadImproveQueue();
    if ((r.proposals || [])[0]) selectProposal(r.proposals[0].id);
  } catch (e) {
    toast(`Diagnose failed: ${e.message}`, true);
  } finally { btn.textContent = orig; btn.disabled = false; }
}

// ── Phase 2: A/B experiments ──
async function loadExperiments() {
  try {
    const data = await api('/api/experiments');
    _improveState.experiments = data.experiments || [];
    renderExperimentsList();
  } catch (e) {
    document.getElementById('improveList').innerHTML =
      `<div class="empty" style="padding:22px;text-align:center;color:var(--red);">${esc(e.message)}</div>`;
  }
}

function _expVerdictColor(v) {
  if (v === 'promote' || v === 'equivalent') return 'var(--green)';
  if (v === 'revert') return 'var(--red)';
  if (v === 'changed') return 'var(--amber,gold)';
  if (v === 'inconclusive') return 'var(--accent)';
  return 'var(--text-faint)';
}
function _sevColor(s) {
  return s === 'critical' ? 'var(--red)' : s === 'major' ? 'var(--amber,gold)'
    : s === 'minor' ? 'var(--accent)' : 'var(--text-faint)';
}
// Backported static-comparator diff. `diff` = {summary, pairs?}. pairs present for a
// static compare; dynamic experiments carry summary only.
function _renderSemanticDiff(diff) {
  if (!diff || !diff.summary) return '';
  const s = diff.summary;
  const chip = (label, n, color) => `<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:${color};color:#000;font-size:10px;font-weight:600;margin-right:6px;">${esc(label)}: ${n||0}</span>`;
  let h = `<div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border);">`
    + `<div style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--text-dim);margin-bottom:6px;">Semantic diff (analysis A ↔ B)</div>`
    + `<div style="margin-bottom:8px;">${chip('changed', s.changed, 'var(--amber,gold)')}${chip('equivalent', s.equivalent, 'var(--green)')}`
    + (s.missing ? chip('missing', s.missing, 'var(--text-faint)') : '')
    + (s.max_severity ? `<span style="font-size:10px;color:${_sevColor(s.max_severity)};font-weight:600;">max severity: ${esc(s.max_severity)}</span>` : '') + `</div>`;
  if (Array.isArray(diff.pairs) && diff.pairs.length) {
    const changed = diff.pairs.filter(p => p.verdict === 'changed');
    h += `<div style="font-size:10px;color:var(--text-faint);margin-bottom:4px;">${changed.length} of ${diff.pairs.length} UCs changed — details:</div>`;
    for (const p of changed) {
      h += `<details style="margin-bottom:4px;"><summary style="cursor:pointer;font-size:11px;"><span style="color:${_sevColor(p.max_severity)};font-weight:600;">[${esc(p.max_severity||'?')}]</span> ${esc(p.uc_uuid)}</summary>`
        + `<div style="padding:4px 0 4px 14px;">` + (p.findings||[]).map(f =>
            `<div style="font-size:10px;color:var(--text-dim);"><span style="color:${_sevColor(f.severity)};">${esc(f.severity)}</span> · <b>${esc(f.field)}</b>: ${esc(f.description)}</div>`).join('') + `</div></details>`;
    }
  }
  return h + `</div>`;
}

function _adhocForm() {
  return `
    <details style="border-bottom:1px solid var(--border);padding:8px 14px;">
      <summary style="cursor:pointer;font-size:11px;color:var(--accent);user-select:none;">+ New A/B experiment</summary>
      <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px;">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          <select id="adhocType" onchange="document.getElementById('adhocParam').style.display=this.value==='sampling'?'':'none';document.getElementById('adhocCandidate').style.display=(this.value==='sampling'||this.value==='max_tokens')?'':'none'" style="font-size:11px;padding:3px 5px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);" title="sampling/max_tokens take a candidate value; grounding nudge + evaluation prompt are flag-style (candidate arm on, baseline = production)">
            <option value="sampling">sampling</option><option value="max_tokens">max_tokens</option><option value="grounding_nudge">grounding nudge</option><option value="stage2_context">evaluation prompt</option>
          </select>
          <select id="adhocParam" style="font-size:11px;padding:3px 5px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);">
            <option value="temperature">temperature</option><option value="top_k">top_k</option><option value="top_p">top_p</option><option value="min_p">min_p</option>
          </select>
          <input id="adhocCandidate" type="number" step="any" placeholder="candidate" style="width:78px;font-size:11px;padding:3px 6px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);">
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          <select id="adhocSet" style="font-size:11px;padding:3px 5px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);"><option value="">eval set…</option></select>
          <label style="font-size:10px;color:var(--text-faint);display:flex;align-items:center;gap:3px;" title="Sampling only: auto-write the winning profile (reversible)"><input type="checkbox" id="adhocAuto" style="width:auto;height:auto;accent-color:var(--accent);"> auto-promote</label>
          <button class="btn primary btn-sm" onclick="launchAdhocExperiment()">🔬 Launch</button>
        </div>
        <div style="font-size:9px;color:var(--text-faint);">Evaluates baseline + candidate on the eval set; the gate auto-reverts a regression or new failure mode. Candidate is per-analysis — production + spamllm untouched.</div>
      </div>
    </details>`;
}

function _staticCompareForm() {
  return `
    <details style="border-bottom:1px solid var(--border);padding:8px 14px;">
      <summary style="cursor:pointer;font-size:11px;color:var(--accent);user-select:none;">+ Static A/B (compare two existing analyses)</summary>
      <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px;">
        <input id="scRunA" placeholder="analysis A (id)" style="font-size:11px;padding:3px 6px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);font-family:var(--mono,monospace);">
        <input id="scRunB" placeholder="analysis B (id)" style="font-size:11px;padding:3px 6px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);font-family:var(--mono,monospace);">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
          <select id="scSet" style="font-size:11px;padding:3px 5px;background:var(--bg-raised);border:1px solid var(--border);color:var(--text);"><option value="">eval set…</option></select>
          <button class="btn primary btn-sm" onclick="launchStaticCompare()">⚖ Compare</button>
        </div>
        <div style="font-size:9px;color:var(--text-faint);">Semantically diffs the two analyses (equivalent/changed + severity). No new analyses; server-side — raw analyses stay on the cluster.</div>
      </div>
    </details>`;
}

async function launchStaticCompare() {
  const a = (document.getElementById('scRunA').value || '').trim();
  const b = (document.getElementById('scRunB').value || '').trim();
  const setId = parseInt(document.getElementById('scSet').value) || null;
  if (!a || !b) { toast('Enter both analysis ids', true); return; }
  if (!setId) { toast('Pick an eval set', true); return; }
  try {
    const r = await api('/api/experiments/static-compare', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_a: a, run_b: b, set_id: setId }) });
    toast(`Static compare #${r.id} done`, false);
    await loadExperiments();
    selectExperiment(r.id);
  } catch (e) { toast(`Compare failed: ${e.message}`, true); }
}

async function _populateAdhocSets() {
  try {
    const data = await api('/api/sets');
    const opts = '<option value="">eval set…</option>' +
      (data.sets || []).map(s => `<option value="${s.id}">${esc(s.name)} (${s.member_count})</option>`).join('');
    const a = document.getElementById('adhocSet'); if (a) a.innerHTML = opts;
    const c = document.getElementById('scSet'); if (c) c.innerHTML = opts;
  } catch (e) {}
}

async function launchAdhocExperiment() {
  const type = document.getElementById('adhocType').value;
  const cand = parseFloat(document.getElementById('adhocCandidate').value);
  const setId = parseInt(document.getElementById('adhocSet').value) || null;
  if (!setId) { toast('Pick an eval set', true); return; }
  // sampling/max_tokens need a candidate value; the flag-style types (grounding nudge,
  // evaluation prompt) don't — the candidate arm flips on, baseline = production.
  const needsCand = (type === 'sampling' || type === 'max_tokens');
  if (needsCand && isNaN(cand)) { toast('Enter a candidate value', true); return; }
  const spec = type === 'sampling'
    ? { type: 'sampling', param: document.getElementById('adhocParam').value, candidate: cand }
    : type === 'max_tokens'
    ? { type: 'max_tokens', candidate: Math.round(cand) }
    : { type };   // grounding_nudge | stage2_context
  const auto = document.getElementById('adhocAuto').checked;
  try {
    const r = await api('/api/experiments', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ change_spec: spec, set_id: setId, sample_count: 1, auto_promote: auto }) });
    toast(`A/B experiment #${r.id} launched`, false);
    await loadExperiments();
    selectExperiment(r.id);
  } catch (e) { toast(`Launch failed: ${e.message}`, true); }
}

function renderExperimentsList() {
  const el = document.getElementById('improveList');
  if (!_improveState.experiments.length) {
    el.innerHTML = _adhocForm() + _staticCompareForm() + `<div class="empty" style="padding:22px;text-align:center;color:var(--text-faint);font-size:12px;">No experiments yet.</div>`;
    _populateAdhocSets();
    return;
  }
  el.innerHTML = _adhocForm() + _staticCompareForm() + _improveState.experiments.map(x => {
    const active = x.id === _improveState.selectedExpId;
    const v = x.verdict || (x.status === 'running' ? 'running' : '—');
    return `
      <div class="improve-row" data-id="${x.id}"
           style="padding:10px 14px;border-bottom:1px solid var(--border);cursor:pointer;
                  ${active ? 'background:var(--bg-raised);border-left:2px solid var(--accent);' : 'border-left:2px solid transparent;'}"
           onclick="selectExperiment(${x.id})">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;">
          <div style="font-size:11px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(x.title || ('experiment #' + x.id))}</div>
          <span style="font-size:9px;text-transform:uppercase;color:${_expVerdictColor(x.verdict)};">${esc(v)}</span>
        </div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:3px;">${esc(x.status)} · ${esc(x.eval_set_name || (x.eval_set_id ? 'set ' + x.eval_set_id : 'ad-hoc'))}</div>
      </div>`;
  }).join('');
  _populateAdhocSets();
}

async function selectExperiment(id) {
  _improveState.selectedExpId = id;
  renderExperimentsList();
  document.getElementById('improveDetailEmpty').style.display = 'none';
  const body = document.getElementById('improveDetailBody');
  body.style.display = '';
  body.innerHTML = `<div style="color:var(--text-faint);font-size:12px;">Loading experiment…</div>`;
  let x;
  try { x = await api(`/api/experiments/${id}`); }
  catch (e) { body.innerHTML = `<div style="color:var(--red);">${esc(e.message)}</div>`; return; }
  const bs = x.baseline_score, cs = x.candidate_score;
  const isStatic = x.change_spec?.type === 'static_compare';
  // Static compare stores the full diff in candidate_score; dynamic stores summary under .semantic_diff.
  const semDiff = isStatic ? cs : (cs && cs.semantic_diff ? { summary: cs.semantic_diff } : null);
  const scoreCell = (s) => s ? `${(s.success_rate*100).toFixed(0)}% (${s.succeeded}/${s.total})${s.high_sev_classes?.length ? ` · ⚠ ${esc(s.high_sev_classes.join(','))}` : ''}` : '—';
  const phaseLine = (arm) => `<span style="color:var(--text-faint);">${esc(x.arm_phases?.[arm] || '?')}</span>`;
  const candLabel = x.change_spec?.type === 'max_tokens' ? ` (max_tokens ${esc(String(x.change_spec?.candidate ?? ''))})`
    : x.change_spec?.type === 'sampling' ? ` (${esc(x.change_spec?.param||'')} ${esc(String(x.change_spec?.candidate ?? ''))})` : '';
  body.innerHTML = `
    <div style="border-bottom:1px solid var(--border);padding-bottom:10px;display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
      <div><div style="font-size:14px;font-weight:600;">${esc(x.title || ('Experiment #' + x.id))}</div>
        <div style="font-size:10px;color:var(--text-faint);margin-top:3px;">${esc(x.status)} · eval ${esc(x.eval_set_name || x.eval_set_id || 'ad-hoc')} · ${x.sample_count} sample(s)</div></div>
      ${x.verdict ? `<span style="font-size:11px;text-transform:uppercase;font-weight:600;color:${_expVerdictColor(x.verdict)};">${esc(x.verdict)}</span>` : ''}
    </div>
    ${isStatic ? `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">
      <div style="background:var(--bg-raised);border:1px solid var(--border);border-radius:2px;padding:10px;">
        <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;">Analysis A</div>
        <div style="font-size:11px;font-family:var(--mono,monospace);margin-top:4px;word-break:break-all;">${esc(x.baseline_run || '')}</div>
      </div>
      <div style="background:var(--bg-raised);border:1px solid var(--border);border-radius:2px;padding:10px;">
        <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;">Analysis B</div>
        <div style="font-size:11px;font-family:var(--mono,monospace);margin-top:4px;word-break:break-all;">${esc(x.candidate_run || '')}</div>
      </div>
    </div>` : `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">
      <div style="background:var(--bg-raised);border:1px solid var(--border);border-radius:2px;padding:10px;">
        <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;">Baseline ${phaseLine('baseline_run')}</div>
        <div style="font-size:16px;font-weight:600;margin-top:4px;">${scoreCell(bs)}</div>
        <div style="font-size:9px;color:var(--text-faint);margin-top:4px;">${esc(x.baseline_run || '')}</div>
      </div>
      <div style="background:var(--bg-raised);border:1px solid var(--border);border-radius:2px;padding:10px;">
        <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;">Candidate${candLabel} ${phaseLine('candidate_run')}</div>
        <div style="font-size:16px;font-weight:600;margin-top:4px;">${scoreCell(cs)}</div>
        <div style="font-size:9px;color:var(--text-faint);margin-top:4px;">${esc(x.candidate_run || '')}</div>
      </div>
    </div>`}
    ${x.verdict_reason ? `<div style="margin-top:12px;font-size:12px;line-height:1.5;padding:10px;background:var(--bg-raised);border-left:2px solid ${_expVerdictColor(x.verdict)};border-radius:2px;">${esc(x.verdict_reason)}</div>` : ''}
    ${_renderSemanticDiff(semDiff)}
    ${x.status === 'running' ? `<div style="margin-top:12px;font-size:11px;color:var(--text-faint);">⏳ Analyses in flight — refresh to update. (Candidate is a per-analysis override; production + spamllm untouched.)</div>` : ''}
    ${x.auto_promote ? `<div style="margin-top:8px;font-size:10px;color:var(--accent);">⚡ auto-promote on (a winning sampling verdict applies automatically, reversibly)</div>` : ''}
    ${x.verdict === 'promote' && x.status !== 'promoted' ? `
      <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border);">
        <button class="btn" style="background:var(--green);color:#fff;" onclick="promoteExperiment(${x.id})">✓ Promote</button>
        <div style="font-size:9px;color:var(--text-faint);margin-top:6px;">${x.change_spec?.type === 'sampling'
          ? 'Sampling — Promote writes the production profile (runtime, reversible).'
          : 'max_tokens — Promote returns the exact deploy-var change (human-gated). The A/B proof is automated.'}</div>
      </div>` : ''}
    ${x.status === 'promoted' ? `
      <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);">
        <div style="font-size:11px;color:var(--green);">✓ Promoted${x.change_spec?.applied ? `: ${esc(x.change_spec.applied.param)} → ${esc(String(x.change_spec.applied.to))}` : ''}</div>
        ${x.change_spec?.type === 'sampling'
          ? `<button class="btn ghost btn-sm" style="margin-top:8px;" onclick="_armDeleteBtn(this, () => revertExperiment(${x.id}))">↩ Revert</button>`
          : `<div style="font-size:9px;color:var(--text-faint);margin-top:4px;">Apply the deploy-var change to ship it.</div>`}
      </div>` : ''}
    ${x.status === 'reverted' ? `<div style="margin-top:12px;font-size:11px;color:var(--text-faint);">↩ Reverted — production profile restored to its prior state.</div>` : ''}`;
}

async function revertExperiment(id) {
  try {
    await api(`/api/experiments/${id}/revert`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    toast('Reverted — production profile restored', false);
    selectExperiment(id);
  } catch (e) { toast(`Revert failed: ${e.message}`, true); }
}

async function promoteExperiment(id) {
  try {
    const r = await api(`/api/experiments/${id}/promote`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    toast('Promotion staged — see apply instructions', false);
    alert('Apply this change to ship the promotion:\n\n' + r.instructions);
    selectExperiment(id);
  } catch (e) { toast(`Promote failed: ${e.message}`, true); }
}

async function launchExperiment(proposalId, candidateValue, setId) {
  try {
    const r = await api('/api/experiments', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_id: proposalId,
        change_spec: { type: 'max_tokens', candidate: candidateValue },
        set_id: setId, sample_count: 1 }),
    });
    toast(`A/B experiment #${r.id} launched (baseline + candidate analyses)`, false);
    document.querySelectorAll('.improve-mode-tab').forEach(b => b.classList.toggle('active', b.dataset.mode === 'experiments'));
    _improveState.mode = 'experiments';
    await loadImproveQueue();
    selectExperiment(r.id);
  } catch (e) { toast(`Launch failed: ${e.message}`, true); }
}

async function loadInbox() {
  // Hydrate repo filter dropdown from the registry (uses the same
  // _reposState cache populated by loadRepos when Config is opened;
  // hit /api/repos directly if state is empty).
  try {
    let repos = (_reposState && _reposState.list) || [];
    if (!repos.length) {
      const r = await api('/api/repos');
      repos = r.repos || [];
    }
    const sel = document.getElementById('inboxRepoFilter');
    if (sel) {
      const issueSourceRepos = repos.filter(x => (x.roles || []).includes('issue-source'));
      const opts = ['<option value="">all</option>'].concat(
        issueSourceRepos.map(x => `<option value="${esc(x.uuid)}">${esc(x.namespace)}</option>`)
      );
      sel.innerHTML = opts.join('');
      sel.value = _inboxState.repoFilter || '';
    }
  } catch { /* non-fatal */ }

  // Fetch comments per current filters
  const params = new URLSearchParams();
  params.set('status', _inboxState.statusFilter || 'new');
  if (_inboxState.repoFilter) params.set('repo_uuid', _inboxState.repoFilter);
  params.set('limit', '200');
  try {
    const r = await api('/api/inbox?' + params.toString());
    _inboxState.list = r.comments || [];
    document.getElementById('badgeInbox').textContent =
      _inboxState.list.length ? _inboxState.list.length : '';
    renderInboxList();
    // Preserve selection if still in list; otherwise clear detail
    if (_inboxState.selectedUuid && _inboxState.list.find(c => c.uuid === _inboxState.selectedUuid)) {
      renderInboxDetail(_inboxState.list.find(c => c.uuid === _inboxState.selectedUuid));
    } else {
      _inboxState.selectedUuid = null;
      _showInboxEmpty();
    }
  } catch (e) {
    document.getElementById('inboxList').innerHTML =
      `<div class="empty" style="color:var(--red);padding:14px;">${esc(e.message)}</div>`;
  }
}

function renderInboxList() {
  const el = document.getElementById('inboxList');
  if (!_inboxState.list.length) {
    el.innerHTML = `<div class="empty" style="padding:22px;text-align:center;color:var(--text-faint);">
      No comments match the current filter.
      <div style="font-size:10px;margin-top:8px;">
        Comments appear here when a repo with role=issue-source has open-PR comments
        (poller M5 / webhook M6).
      </div>
    </div>`;
    return;
  }
  const rowHtml = (c, threaded) => {
    const isActive = c.uuid === _inboxState.selectedUuid;
    const statusColor = c.status === 'new' ? 'var(--accent)'
      : (c.status === 'drafted_to_uc' ? 'var(--green)' : 'var(--text-faint)');
    const preview = (c.body || '').replace(/\s+/g, ' ').slice(0, 140);
    const uuidJson = attrJson(c.uuid);
    return `
      <div class="inbox-row" data-uuid="${esc(c.uuid)}"
           style="padding:10px 14px${threaded ? ' 10px 26px' : ''};border-bottom:1px solid var(--border);cursor:pointer;
                  ${isActive ? 'background:var(--bg-raised);border-left:2px solid var(--accent);' : ''}"
           onclick="selectInboxComment(${uuidJson})">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;">
          <div style="font-size:11px;font-weight:600;">
            <span style="color:var(--text-faint);">@</span>${esc(c.author_login)}
            ${threaded ? '' : `<span style="color:var(--text-faint);font-weight:400;font-size:10px;"> · PR #${c.pr_number}${c.repo_branch ? ` · ⎇ ${esc(c.repo_branch)}` : ''}</span>`}
          </div>
          <div style="display:flex;gap:5px;align-items:center;">
            ${(!threaded && c.repo_namespace) ? `<span title="Source repo namespace — drafted UCs auto-scope to spec_namespaces: [${esc(c.repo_namespace)}]" style="font-size:9px;font-family:var(--mono,monospace);color:var(--accent);background:var(--accent-bg);padding:1px 5px;border-radius:2px;">${esc(c.repo_namespace)}</span>` : ''}
            <span style="font-size:9px;color:${statusColor};text-transform:uppercase;letter-spacing:.06em;">${esc(c.status)}</span>
          </div>
        </div>
        ${threaded ? '' : `<div style="font-size:10px;color:var(--text-faint);margin:2px 0 4px;">${esc(c.pr_title || '(no title)')}</div>`}
        <div style="font-size:11px;color:var(--text-dim);line-height:1.4;">${esc(preview)}${preview.length >= 140 ? '…' : ''}</div>
        <div style="font-size:9px;color:var(--text-faint);margin-top:4px;font-family:var(--mono,monospace);">
          ${esc(c.github_comment_type)} · ${esc(fmtTs(c.github_updated_at))} · src: ${esc(c.ingestion_source)}
        </div>
      </div>`;
  };
  if (_inboxState.threadByPR) {
    // Group the conversation by PR (repo namespace + pr_number), newest PR first.
    const groups = new Map();
    _inboxState.list.forEach(c => {
      const key = `${c.repo_namespace || ''}#${c.pr_number}`;
      if (!groups.has(key)) groups.set(key, { pr: c.pr_number, title: c.pr_title, ns: c.repo_namespace, url: c.pr_url, items: [] });
      groups.get(key).items.push(c);
    });
    el.innerHTML = [...groups.values()].map(g => `
      <div style="padding:7px 14px;background:var(--bg-raised);border-bottom:1px solid var(--border);">
        <div style="font-size:11px;font-weight:600;">
          ${g.ns ? `<span style="font-family:var(--mono,monospace);color:var(--accent);font-size:9px;">${esc(g.ns)}</span> ` : ''}PR #${g.pr}
          <span style="color:var(--text-faint);font-weight:400;"> ${esc(g.title || '')}</span>
          <span style="color:var(--text-faint);font-size:9px;font-weight:400;"> · ${g.items.length} comment${g.items.length===1?'':'s'}</span>
          ${g.url ? ` <a href="${esc(g.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="color:var(--text-faint);font-size:9px;">↗</a>` : ''}
        </div>
      </div>
      ${g.items.map(c => rowHtml(c, true)).join('')}`).join('');
  } else {
    el.innerHTML = _inboxState.list.map(c => rowHtml(c, false)).join('');
  }
}

function _showInboxEmpty() {
  document.getElementById('inboxDetailEmpty').style.display = '';
  document.getElementById('inboxDetailBody').style.display = 'none';
}

function _showInboxBody() {
  document.getElementById('inboxDetailEmpty').style.display = 'none';
  document.getElementById('inboxDetailBody').style.display = 'flex';
}

async function selectInboxComment(uuid) {
  _inboxState.selectedUuid = uuid;
  renderInboxList(); // re-render to show active state
  try {
    const c = await api(`/api/inbox/${encodeURIComponent(uuid)}`);
    renderInboxDetail(c);
  } catch (e) {
    document.getElementById('inboxDetailBody').innerHTML =
      `<div style="color:var(--red);padding:14px;">${esc(e.message)}</div>`;
    _showInboxBody();
  }
}

function renderInboxDetail(c) {
  const el = document.getElementById('inboxDetailBody');
  el.style.flexDirection = 'column';
  el.style.gap = '10px';
  const ucLinks = (c.uc_links || []).map(l => `
    <div style="font-size:11px;font-family:var(--mono,monospace);color:var(--text-dim);">
      ↳ <a href="javascript:void(0)" onclick="switchView('usecases');setTimeout(()=>editUC('${esc(l.uc_uuid)}'),200)" style="color:var(--accent);">${esc(l.uc_uuid)}</a>
      <span style="color:var(--text-faint);font-family:inherit;font-size:10px;"> · ${esc(l.linked_by || '')} · ${esc(fmtTs(l.linked_at))}</span>
    </div>
  `).join('');
  el.innerHTML = `
    <div style="padding-bottom:8px;border-bottom:1px solid var(--border);">
      <div style="font-size:13px;font-weight:600;">PR #${c.pr_number}: ${esc(c.pr_title || '(no title)')}</div>
      <div style="font-size:11px;color:var(--text-dim);margin-top:2px;font-family:var(--mono,monospace);">
        ${c.repo_url ? `<a href="${esc(c.repo_url)}" target="_blank" rel="noopener" style="color:var(--accent);">${esc(c.repo_display_name || c.repo_namespace || c.repo_url)}</a>` : esc(c.repo_namespace || '—')}${c.repo_branch ? ` · <span title="Monitored branch">⎇ ${esc(c.repo_branch)}</span>` : ''}
      </div>
      <div style="font-size:11px;color:var(--text-faint);margin-top:2px;">
        @${esc(c.author_login)} ${c.author_url ? `· <a href="${esc(c.author_url)}" target="_blank" rel="noopener" style="color:var(--text-faint);">profile</a>` : ''}
        · ${esc(c.github_comment_type)}
        ${c.comment_url ? `· <a href="${esc(c.comment_url)}" target="_blank" rel="noopener" style="color:var(--text-faint);">comment on GitHub ↗</a>` : ''}
        ${c.pr_url ? `· <a href="${esc(c.pr_url)}" target="_blank" rel="noopener" style="color:var(--text-faint);">PR ↗</a>` : ''}
      </div>
      <div style="font-size:10px;color:var(--text-faint);margin-top:2px;font-family:var(--mono,monospace);">
        updated ${esc(fmtTs(c.github_updated_at))} · fetched ${esc(fmtTs(c.fetched_at))} · src: ${esc(c.ingestion_source)} · status: <span style="color:${c.status === 'new' ? 'var(--accent)' : (c.status === 'drafted_to_uc' ? 'var(--green)' : 'var(--text-faint)')};text-transform:uppercase;">${esc(c.status)}</span>
      </div>
    </div>
    <div style="padding:8px 10px;background:var(--bg-raised);border:1px solid var(--border);border-radius:2px;font-size:12px;line-height:1.5;white-space:pre-wrap;max-height:40vh;overflow-y:auto;font-family:var(--mono,monospace);">${esc(c.body)}</div>
    ${ucLinks ? `<div style="padding:8px 10px;background:var(--bg-raised);border:1px solid var(--border);border-radius:2px;">
      <div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-bottom:6px;">Drafted into UCs</div>
      ${ucLinks}
    </div>` : ''}
    <div style="display:flex;gap:8px;align-items:center;">
      <button class="btn primary" id="inboxDraftBtn" onclick="draftUCFromComment('${esc(c.uuid)}')">✦ Draft UC (LLM)</button>
      <button class="btn ghost" id="inboxDismissBtn" onclick="setInboxStatus('${esc(c.uuid)}', 'dismissed')" ${c.status === 'dismissed' ? 'disabled' : ''}>Dismiss</button>
      ${c.status !== 'new' ? `<button class="btn ghost" onclick="setInboxStatus('${esc(c.uuid)}', 'new')">Reopen</button>` : ''}
      <span id="inboxDetailMsg" style="font-size:11px;color:var(--text-faint);"></span>
    </div>
    <div id="inboxDraftOutput" style="display:none;"></div>`;
  _showInboxBody();
}

async function setInboxStatus(uuid, status, ucUuid) {
  const msgEl = document.getElementById('inboxDetailMsg');
  if (msgEl) { msgEl.textContent = '…'; msgEl.style.color = ''; }
  const body = { status };
  if (ucUuid) body.uc_uuid = ucUuid;
  try {
    await api(`/api/inbox/${encodeURIComponent(uuid)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (msgEl) { msgEl.innerHTML = `<span style="color:var(--green);">${esc(status)}</span>`; }
    // Refresh list (the comment may have moved out of the current filter)
    await loadInbox();
  } catch (e) {
    if (msgEl) msgEl.innerHTML = `<span style="color:var(--red);">${esc(e.message)}</span>`;
  }
}

async function draftUCFromComment(uuid) {
  const btn = document.getElementById('inboxDraftBtn');
  const msgEl = document.getElementById('inboxDetailMsg');
  const outEl = document.getElementById('inboxDraftOutput');
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '… drafting (this can take 30-60s)';
  if (msgEl) { msgEl.textContent = ''; msgEl.style.color = ''; }
  // Pass the operator's preferred UC Assist model if they have one
  // selected in localStorage (same key used by Config → UC Assist panel)
  const body = {};
  const ucAssistModelId = (() => { try { return localStorage.getItem('ucAssistModelId'); } catch { return null; } })();
  if (ucAssistModelId) {
    const n = parseInt(ucAssistModelId, 10);
    if (Number.isFinite(n)) body.model_config_id = n;
  }
  try {
    const r = await api(`/api/inbox/${encodeURIComponent(uuid)}/draft-uc`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    outEl.style.display = '';
    const autoScope = Array.isArray(r.auto_scoped_namespaces) ? r.auto_scoped_namespaces : [];
    const autoScopeBanner = autoScope.length
      ? `<div style="font-size:11px;background:var(--accent-bg);border-left:3px solid var(--accent);padding:6px 10px;margin-bottom:8px;color:var(--text);">
           🎯 Auto-scoped to <code style="color:var(--accent);font-family:var(--mono,monospace);">spec_namespaces: [${autoScope.map(esc).join(', ')}]</code> — drafted from a comment in the <strong>${autoScope.map(esc).join('/')}</strong> repo. Stage-2 grounding will hard-restrict to this namespace. Edit the YAML before saving if cross-namespace coverage is intended.
         </div>`
      : '';
    outEl.innerHTML = `
      <div style="padding:8px 10px;background:var(--bg-raised);border:1px solid var(--green);border-radius:2px;">
        <div style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--green);margin-bottom:6px;">LLM draft — review before saving</div>
        ${autoScopeBanner}
        ${r.explanation ? `<div style="font-size:12px;line-height:1.5;margin-bottom:8px;">${esc(r.explanation)}</div>` : ''}
        ${r.yaml_suggestion ? `<details open style="margin-bottom:8px;">
          <summary style="font-size:11px;color:var(--text-faint);cursor:pointer;">YAML (click to collapse)</summary>
          <pre style="font-family:var(--mono,monospace);font-size:11px;background:var(--bg-panel);padding:8px;border:1px solid var(--border);border-radius:2px;overflow-x:auto;max-height:50vh;">${esc(r.yaml_suggestion)}</pre>
        </details>` : '<div style="color:var(--accent);">(no YAML extracted from response — see raw output below)</div>'}
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <button class="btn primary" onclick="saveInboxDraft('${esc(uuid)}', ${attrJson(r.yaml_suggestion || '')})" ${r.yaml_suggestion ? '' : 'disabled'} title="Save this draft as a Draft use case + record the link to this comment">💾 Save as Draft</button>
          <button class="btn ghost" onclick="copyInboxDraftYaml(${attrJson(r.yaml_suggestion || '')})" ${r.yaml_suggestion ? '' : 'disabled'} title="Copy the YAML to clipboard">⎘ Copy YAML</button>
          <button class="btn ghost" onclick="openUCEditorWithDraft('${esc(uuid)}', ${attrJson(r.yaml_suggestion || '')})" ${r.yaml_suggestion ? '' : 'disabled'} title="Stash draft + switch to Use Cases tab (paste manually)">↑ Edit in Use Cases</button>
          <button class="btn ghost" onclick="document.getElementById('inboxDraftOutput').style.display='none';">Hide</button>
        </div>
        ${r.raw && !r.yaml_suggestion ? `<details style="margin-top:8px;"><summary style="font-size:10px;color:var(--text-faint);cursor:pointer;">Raw response</summary>
          <pre style="font-family:var(--mono,monospace);font-size:10px;background:var(--bg-panel);padding:8px;border:1px solid var(--border);max-height:30vh;overflow-y:auto;">${esc(r.raw)}</pre>
        </details>` : ''}
      </div>`;
  } catch (e) {
    if (msgEl) msgEl.innerHTML = `<span style="color:var(--red);">Draft failed: ${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

async function copyInboxDraftYaml(yamlText) {
  try {
    await navigator.clipboard.writeText(yamlText || '');
    toast('YAML copied to clipboard');
  } catch (e) {
    toast('Copy failed: ' + e.message, true);
  }
}

// Re-draft choice when a comment already produced a UC: replace / new / cancel.
function _redraftChoice(links) {
  const u = links[0].uc_uuid;
  if (confirm(`This comment already drafted UC ${u}.\n\nOK = REPLACE that UC with this new draft.\nCancel = choose another option…`)) return 'replace';
  if (confirm('Create a NEW separate UC instead?\n\nOK = create new.\nCancel = abort.')) return 'new';
  return 'cancel';
}

// Save the LLM draft as a real Draft use case + record the comment↔UC link. If
// the comment already drafted a UC, prompt to replace / create-new / cancel.
async function saveInboxDraft(commentUuid, yaml) {
  if (!yaml || !yaml.trim()) { toast('No YAML to save', true); return; }
  const msgEl = document.getElementById('inboxDetailMsg');
  let links = [];
  try { const c = await api(`/api/inbox/${encodeURIComponent(commentUuid)}`); links = c.uc_links || []; } catch(e){}
  let mode = 'new', targetUuid = null;
  if (links.length) {
    const choice = _redraftChoice(links);
    if (choice === 'cancel') return;
    if (choice === 'replace') { mode = 'replace'; targetUuid = links[0].uc_uuid; }
  }
  if (msgEl) { msgEl.textContent = 'saving…'; msgEl.style.color = 'var(--text-faint)'; }
  try {
    let ucUuid;
    if (mode === 'replace') {
      // PUT requires the YAML uuid to match the URL — rewrite it to the existing UC.
      const y2 = yaml.replace(/^[ \t]*uuid:\s*\S+/m, `uuid: ${targetUuid}`);
      await api(`/api/use-cases/${encodeURIComponent(targetUuid)}`, { method:'PUT', body: JSON.stringify({ yaml_content: y2 }) });
      ucUuid = targetUuid;
    } else {
      const r = await api('/api/use-cases', { method:'POST', body: JSON.stringify({ yaml_content: yaml }) });
      ucUuid = r.uuid;
    }
    await api(`/api/inbox/${encodeURIComponent(commentUuid)}/status`, { method:'POST', body: JSON.stringify({ status:'drafted_to_uc', uc_uuid: ucUuid }) });
    toast(mode === 'replace' ? `Replaced UC ${ucUuid}` : `Saved Draft UC ${ucUuid}`);
    selectInboxComment(commentUuid);   // refresh detail → shows the link
    loadInbox();
  } catch(e) {
    if (msgEl) { msgEl.innerHTML = `<span style="color:var(--red);">Save failed: ${esc(e.message)}</span>`; }
    else toast('Save failed: ' + e.message, true);
  }
}

function openUCEditorWithDraft(commentUuid, yamlDraft) {
  // Stash the draft + source comment uuid in sessionStorage so the UC
  // editor on the Use Cases tab can pre-populate. The save handler over
  // there should call setInboxStatus(commentUuid, 'drafted_to_uc', newUcUuid)
  // after a successful save to record the provenance link.
  try {
    sessionStorage.setItem('inboxDraftPayload', JSON.stringify({
      comment_uuid: commentUuid,
      yaml: yamlDraft,
      from: 'inbox',
      timestamp: new Date().toISOString(),
    }));
  } catch (e) {
    toast('Could not stash draft in sessionStorage: ' + e.message, true);
    return;
  }
  toast('Draft staged — switching to Use Cases editor');
  switchView('usecases');
  // The Use Cases tab's editor opens via newUC() / editUC(); a v1
  // operator workflow is: copy the draft YAML into the new-UC editor
  // manually. A polished pre-fill (auto-newUC + auto-paste) lands as
  // a follow-up — for v1 keeping the boundary clean between modules.
  setTimeout(() => {
    if (typeof openNewUC === 'function') openNewUC();
    else if (typeof newUC === 'function') newUC();
  }, 300);
}

// Status filter chips
document.querySelectorAll('.inbox-status-chip').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.inbox-status-chip').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _inboxState.statusFilter = btn.dataset.status;
    loadInbox();
  });
});
document.getElementById('inboxRepoFilter')?.addEventListener('change', (e) => {
  _inboxState.repoFilter = e.target.value || '';
  loadInbox();
});
document.getElementById('inboxRefreshBtn')?.addEventListener('click', () => loadInbox());

document.getElementById('addMCPBtn').addEventListener('click', () => openMCPForm(null));
document.getElementById('cancelMCPBtn').addEventListener('click', () => {
  document.getElementById('mcpFormCard').style.display = 'none';
  _mcpEditId = null;
});

document.getElementById('saveMCPBtn').addEventListener('click', async () => {
  const msgEl = document.getElementById('mcpFormMsg');
  const payload = {
    name: document.getElementById('mcpfName').value.trim(),
    sse_url: document.getElementById('mcpfUrl').value.trim(),
    description: document.getElementById('mcpfDesc').value.trim(),
    enabled: document.getElementById('mcpfEnabled').checked,
    use_uc_assist: document.getElementById('mcpfUseUCAssist').checked,
    auth_token: document.getElementById('mcpfAuthToken').value,
  };
  if (!payload.name || !payload.sse_url) {
    msgEl.textContent = 'Name and SSE URL are required.';
    msgEl.style.color = 'var(--error)';
    return;
  }
  msgEl.textContent = 'Saving…';
  msgEl.style.color = 'var(--text-faint)';
  try {
    if (_mcpEditId) {
      const updated = await api(`/api/mcp-servers/${_mcpEditId}`, { method: 'PUT', body: JSON.stringify(payload) });
      _mcpServers = _mcpServers.map(s => s.id === _mcpEditId ? updated : s);
    } else {
      const created = await api('/api/mcp-servers', { method: 'POST', body: JSON.stringify(payload) });
      _mcpServers.push(created);
    }
    renderMCPList();
    document.getElementById('mcpFormCard').style.display = 'none';
    _mcpEditId = null;
    pollMCPHealth();
  } catch (e) {
    msgEl.textContent = `Save failed: ${e.message}`;
    msgEl.style.color = 'var(--error)';
  }
});

document.getElementById('copyClaudeCodeBtn').addEventListener('click', () => {
  navigator.clipboard.writeText(_mcpSnippet('claude'))
    .then(() => toast('Claude Code config copied'))
    .catch(() => toast('Copy failed — check browser permissions', true));
});
document.getElementById('copyCursorBtn').addEventListener('click', () => {
  navigator.clipboard.writeText(_mcpSnippet('cursor'))
    .then(() => toast('Cursor / Windsurf config copied'))
    .catch(() => toast('Copy failed — check browser permissions', true));
});

// ── UC Assist config ─────────────────────────────────────────────────────────

const _ucAssistModelKey = 'ucAssistModelId';   // legacy key — no longer written

function _ucAssistStatus() {
  const sel = document.getElementById('ucAssistModelSel');
  const statusEl = document.getElementById('ucAssistCfgStatus');
  if (!statusEl) return;
  const hasModels = _reviewModels.some(m => m.enabled);
  statusEl.textContent = hasModels ? (sel && sel.value ? '✓ default set' : 'no default set') : 'no models configured';
  statusEl.style.color = (sel && sel.value) ? 'var(--ok)' : 'var(--text-faint)';
}

function _populateUCAssistModelSel() {
  // Config "UC Authoring" default selector — server-backed (uc-authoring).
  _populateDefaultSel('ucAssistModelSel', _modelDefaults['uc-authoring'] || null);
  _populateEvalDefaultSel(_evalDefaultModelId);
  // Panel picker is a default-aware override now.
  _populateOverrideSel('ucAssistPanelModelSel', 'uc-authoring');
  _ucAssistAvailable = null;
  _ucAssistStatus();
}

async function loadUCAssistConfig() {
  // model_defaults loaded by loadArchDefault(); just (re)populate the selectors.
  _populateUCAssistModelSel();
}

// The UC-Authoring default is saved server-side (model_defaults['uc-authoring'])
// and shared by the assist panel, wizard, and bulk import.
document.getElementById('saveUCAssistBtn').addEventListener('click', async () => {
  await _saveModelDefault('uc-authoring', 'ucAssistModelSel', 'uacMsg', 'saveUCAssistBtn');
  _ucAssistAvailable = null;
  _ucAssistStatus();
});

// ── Enhancement Targets (ADR-006) ────────────────────────────────────────────
// Post-ADR-006, code_repo_configs is consolidated into managed_repos with
// role=enhancement-target. The Config UI for code repositories was removed;
// operators manage these through the Managed repos panel (add the role,
// set a PAT). The `_codeRepos` cache below is populated lazily from
// /api/repos?role=enhancement-target by the two PR-creation dropdowns
// (rpPrRepoSel and rdPrRepoSel) and invalidated on config reload.
let _codeRepos = [];

async function loadCodeRepos() {
  // Lazy: populated when the PR creation form opens. Kept as a no-op
  // function so loadConfig()'s existing call site stays valid.
  try {
    const r = await api('/api/repos?role=enhancement-target');
    _codeRepos = r.repos || [];
  } catch {
    _codeRepos = [];
  }
}


// ── Config nav: scroll-spy + click-to-scroll ─────────────────────────────────
// Config is now a tabbed view (docs/ui-style-guide.md): the left scroll-spy nav was
// replaced by the canonical .tabs strip. setupConfigNav() just syncs + wires the tabs
// (idempotent; honors the per-panel role-gating from _applyAccessVisibility).
function setupConfigNav() {
  try { _syncConfigTabs(); } catch (e) { console.warn('config tab sync failed', e); }
}

// Config tab data is loaded via loadConfig() called from switchView('config').

// ── Arch review font bar ──────────────────────────────────────────────────────

init();
// ---------------------------------------------------------------------------
// Enablement — the affirmative projection.
//
// DAV is gap-rich and enablement-poor: every run records BOTH what is missing
// (uc_gaps) and what delivers the use case (uc_capabilities), and only the
// deficit was ever surfaced. There is more affirmative data than gap data. The
// Customer and Stakeholder lenses had almost nothing to read because the answer
// they come for — "what does this support, and how?" — was computed and then
// dropped on the floor. This view is that data read forward.
// ---------------------------------------------------------------------------
let _enMode = 'uc';
let _enData = null;

async function loadEnablement() {
  const st = document.getElementById('enStatus');
  if (st) st.textContent = 'Loading…';
  try {
    _enData = await api('/api/analysis/enablement');
    renderEnablement();
  } catch (e) {
    if (st) st.textContent = 'Could not load enablement: ' + (e && e.message ? e.message : e);
  }
}

function renderEnablement() {
  const body = document.getElementById('enBody');
  const st = document.getElementById('enStatus');
  const tot = document.getElementById('enTotals');
  if (!body || !_enData) return;
  const t = _enData.totals || {};

  if (tot) tot.textContent = `${t.use_cases || 0} use cases · ${t.capabilities || 0} capabilities · ${t.claims || 0} claims`;

  if (!t.claims) {
    st.textContent = 'No enablement data for this project yet — run an analysis, or check the active project.';
    body.innerHTML = '';
    return;
  }
  st.textContent = `${t.supported || 0} supported · ${t.partially_supported || 0} partially supported`;

  document.querySelectorAll('#enModeTabs [data-enmode]').forEach(b =>
    b.classList.toggle('active', b.dataset.enmode === _enMode));

  body.innerHTML = _enMode === 'uc' ? _enByUc() : _enByCap();
}

function _verdictChip(v) {
  const label = (v || 'unknown').replace(/_/g, ' ');
  const color = v === 'supported' ? 'var(--ok, #3fb950)'
              : v === 'partially_supported' ? 'var(--warn, #d29922)'
              : 'var(--text-faint)';
  return `<span style="font-size:10px;color:${color};">${esc(label)}</span>`;
}

function _enByUc() {
  return (_enData.use_cases || []).map(u => `
    <div style="border:1px solid var(--border);border-radius:4px;padding:8px 10px;margin-bottom:8px;">
      <div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;">
        <strong style="font-size:12px;">${esc(u.uc_handle || u.uc_uuid)}</strong>
        ${_verdictChip(u.verdict)}
        <span style="margin-left:auto;font-size:10px;color:var(--text-faint);">${(u.capabilities || []).length} capabilities</span>
      </div>
      <div style="margin-top:6px;display:flex;flex-direction:column;gap:4px;">
        ${(u.capabilities || []).map(c => `
          <div style="font-size:11px;">
            <span style="color:var(--text-dim);">${esc(c.capability_id)}</span>
            ${c.usage ? `<span style="color:var(--text-faint);"> · ${esc(c.usage)}</span>` : ''}
            ${c.rationale ? `<div style="color:var(--text-faint);margin-left:10px;">${esc(c.rationale)}</div>` : ''}
          </div>`).join('')}
      </div>
    </div>`).join('') || '<div style="font-size:11px;color:var(--text-faint);">No use cases in scope.</div>';
}

function _enByCap() {
  // Load-bearing first: the capabilities carrying the most use cases are the spine of
  // the architecture, and the ones whose loss would hurt most.
  return (_enData.capabilities || []).map(c => `
    <div style="border:1px solid var(--border);border-radius:4px;padding:8px 10px;margin-bottom:8px;">
      <div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;">
        <strong style="font-size:12px;">${esc(c.capability_id)}</strong>
        ${c.namespace ? `<span style="font-size:10px;color:var(--text-faint);">${esc(c.namespace)}</span>` : ''}
        <span style="margin-left:auto;font-size:10px;color:var(--text-faint);">
          delivers ${c.uc_count} · ${c.supported} supported · ${c.partial} partial</span>
      </div>
      <div style="margin-top:4px;font-size:11px;color:var(--text-faint);">${(c.use_cases || []).map(esc).join(', ')}</div>
    </div>`).join('') || '<div style="font-size:11px;color:var(--text-faint);">No capabilities in scope.</div>';
}

document.addEventListener('click', (e) => {
  const b = e.target.closest && e.target.closest('#enModeTabs [data-enmode]');
  if (b) { _enMode = b.dataset.enmode; renderEnablement(); return; }
  if (e.target && e.target.id === 'enReloadBtn') loadEnablement();
});

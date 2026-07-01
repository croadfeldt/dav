// ── Semi-automated UC fix — single-UC suggest+apply (TODO 2, docs/uc-fix-design.md) ──
// Deterministic tier: POST /api/use-cases/{uuid}/suggest-fix (dry-run) → show the proposed change
// list + remaining/semantic errors + proposed YAML → Apply (?apply=true) reuses the gated save.
// The modal DOM is created on demand (no markup partial needed).
let _ucFixState = null;   // { uuid, proposed_yaml }

const _UC_FIX_KIND = {
  relocate: ['↔', 'var(--accent)',      'moved to the dimension it belongs to'],
  default:  ['●', 'var(--text-dim)',    'set to a safe default'],
  copy:     ['⎘', 'var(--accent)',       'copied from the valid twin field'],
  derive:   ['✎', 'var(--text-dim)',    'derived (e.g. handle from title)'],
  drop:     ['✕', 'var(--red)',         'dropped an invalid optional value'],
};

function _ucFixEnsureModal() {
  let ov = document.getElementById('ucFixModal');
  if (ov) return ov;
  ov = document.createElement('div');
  ov.className = 'modal-overlay';
  ov.id = 'ucFixModal';
  ov.innerHTML = `
    <div class="modal" style="max-width:720px;width:92%;">
      <div class="modal-header">
        <div class="modal-title">✦ Suggest fix <span id="ucFixSub" style="font-size:11px;color:var(--text-faint);font-weight:400;"></span></div>
        <button class="btn ghost btn-icon" onclick="closeUCFix()">✕</button>
      </div>
      <div class="modal-body" id="ucFixBody" style="max-height:70vh;overflow-y:auto;">
        <div class="empty">loading…</div>
      </div>
      <div class="modal-footer" style="display:flex;gap:8px;justify-content:flex-end;">
        <button class="btn ghost" onclick="closeUCFix()">Close</button>
        <button class="btn primary" id="ucFixApplyBtn" data-edit-gate="project.usecases" onclick="_applyUCFix()" disabled>Apply fix</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov) closeUCFix(); });
  return ov;
}

function closeUCFix() {
  const ov = document.getElementById('ucFixModal');
  if (ov) ov.style.display = 'none';
  _ucFixState = null;
}

// Open the fix modal for one UC: fetch the deterministic suggestion (dry-run) and render it.
async function openUCFixModal(uuid) {
  const ov = _ucFixEnsureModal();
  ov.style.display = 'flex';
  const body = document.getElementById('ucFixBody');
  const sub = document.getElementById('ucFixSub');
  const applyBtn = document.getElementById('ucFixApplyBtn');
  applyBtn.disabled = true;
  _ucFixState = { uuid, proposed_yaml: null };
  body.innerHTML = '<div class="empty">analyzing…</div>';
  if (sub) sub.textContent = uuid;
  let r;
  try {
    r = await api(`/api/use-cases/${encodeURIComponent(uuid)}/suggest-fix`, { method: 'POST' });
  } catch (e) {
    body.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
    return;
  }
  _ucFixState.proposed_yaml = r.proposed_yaml;
  _renderUCFix(r);
}

function _renderUCFix(r) {
  const body = document.getElementById('ucFixBody');
  const applyBtn = document.getElementById('ucFixApplyBtn');
  const changes = r.changes || [];
  const canFix = changes.length && (r.remaining_errors || []).length < (r.errors_before || []).length;
  applyBtn.disabled = !canFix || !canEdit('project.usecases');

  const sect = (title, inner) => `<div style="margin-bottom:12px;"><div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-bottom:5px;">${title}</div>${inner}</div>`;

  // Change list
  let changesHtml;
  if (!changes.length) {
    changesHtml = `<div style="font-size:11px;color:var(--text-dim);">No deterministic fix is available — the issues need a manual (or AI-assisted) edit.</div>`;
  } else {
    changesHtml = changes.map(c => {
      const [icon, color, tip] = _UC_FIX_KIND[c.kind] || ['·', 'var(--text-dim)', c.kind];
      const from = (c.from === null || c.from === undefined || c.from === '') ? '∅' : c.from;
      const to = (c.to === null || c.to === undefined) ? '∅ (removed)' : c.to;
      return `<div style="display:flex;gap:8px;align-items:baseline;font-size:11px;padding:3px 0;border-bottom:1px solid var(--border);">
        <span title="${esc(tip)}" style="color:${color};flex:0 0 auto;font-size:10px;min-width:38px;">${esc(icon)} ${esc(c.kind)}</span>
        <code style="flex:1;font-size:10px;">${esc(c.field)}</code>
        <span style="color:var(--text-faint);font-size:10px;">${esc(String(from))}</span>
        <span style="color:var(--text-faint);">→</span>
        <span style="color:var(--text);font-size:10px;">${esc(String(to))}</span>
      </div>`;
    }).join('');
  }

  const before = r.errors_before || [], remaining = r.remaining_errors || [], semantic = r.needs_semantic || [];
  const errLine = (e, isSem) => `<div style="font-size:10px;color:${isSem ? 'var(--amber,#d79a2b)' : 'var(--red)'};padding:1px 0;">• ${esc(e)}</div>`;

  let statusHtml;
  if (r.valid_after) {
    statusHtml = `<div style="font-size:11px;color:var(--green);">✓ These changes make the use case valid.</div>`;
  } else if (semantic.length) {
    statusHtml = `<div style="font-size:11px;color:var(--amber,#d79a2b);margin-bottom:4px;">${semantic.length} issue${semantic.length === 1 ? '' : 's'} need${semantic.length === 1 ? 's' : ''} a human/AI edit (can't be auto-filled):</div>`
      + semantic.map(e => errLine(e, true)).join('');
  } else if (remaining.length) {
    statusHtml = remaining.map(e => errLine(e, false)).join('');
  }

  body.innerHTML =
      sect(`${before.length} validation error${before.length === 1 ? '' : 's'} — proposed changes`, changesHtml)
    + (statusHtml ? sect('After applying', statusHtml) : '')
    + sect('Proposed use case (YAML)',
        `<pre style="background:var(--bg);border:1px solid var(--border);border-radius:2px;padding:8px;font-size:10px;max-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-word;">${esc(r.proposed_yaml || '')}</pre>`);
}

async function _applyUCFix() {
  if (!_ucFixState || !_ucFixState.uuid) return;
  const btn = document.getElementById('ucFixApplyBtn');
  btn.disabled = true; btn.textContent = 'Applying…';
  try {
    const r = await api(`/api/use-cases/${encodeURIComponent(_ucFixState.uuid)}/suggest-fix?apply=true`, { method: 'POST' });
    toast(r.valid_after ? 'Fixed — now valid' : `Applied ${(r.changes || []).length} change(s); ${(r.remaining_errors || []).length} issue(s) still need a manual edit`);
    closeUCFix();
    await loadUCs();                 // refresh list + health flags
    if (typeof activeUCId !== 'undefined' && activeUCId === r.uuid && typeof selectUC === 'function') {
      try { selectUC(r.uuid); } catch (_) {}   // refresh the open detail pane
    }
  } catch (e) {
    toast('Apply failed: ' + e.message, true);
    btn.disabled = false; btn.textContent = 'Apply fix';
  }
}

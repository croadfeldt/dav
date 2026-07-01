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
  _ucFixState = { uuid, method: 'deterministic' };
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
  _ucFixState.method = r.method || 'deterministic';
  _renderUCFix(r);
}

// Escalate to the LLM tier (slice B): ask the project's UC-authoring model to fill the semantic
// gaps the deterministic tier left. Dry-run; re-renders the modal with the model's proposal.
async function _llmAssistFix() {
  if (!_ucFixState || !_ucFixState.uuid) return;
  const body = document.getElementById('ucFixBody');
  const applyBtn = document.getElementById('ucFixApplyBtn');
  applyBtn.disabled = true;
  body.innerHTML = '<div class="empty"><span class="llm-spinner" style="display:inline-block;margin-right:6px;"></span>asking the model to draft the missing content…</div>';
  let r;
  try {
    r = await api(`/api/use-cases/${encodeURIComponent(_ucFixState.uuid)}/suggest-fix-llm`, { method: 'POST' });
  } catch (e) {
    // Re-render the deterministic view with the error surfaced (e.g. no model configured).
    body.innerHTML = `<div class="empty" style="color:var(--red)">AI-assist unavailable: ${esc(e.message)}</div>`;
    return;
  }
  _ucFixState.proposed_yaml = r.proposed_yaml;
  _ucFixState.method = r.method || 'llm';
  _renderUCFix(r);
}

function _renderUCFix(r) {
  const body = document.getElementById('ucFixBody');
  const applyBtn = document.getElementById('ucFixApplyBtn');
  const changes = r.changes || [];
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

  const isLLM = _ucFixState && _ucFixState.method === 'llm';
  let statusHtml;
  if (r.valid_after) {
    statusHtml = `<div style="font-size:11px;color:var(--green);">✓ These changes make the use case valid.</div>`;
  } else if (semantic.length) {
    statusHtml = `<div style="font-size:11px;color:var(--amber,#d79a2b);margin-bottom:4px;">${semantic.length} issue${semantic.length === 1 ? '' : 's'} need${semantic.length === 1 ? 's' : ''} written content (can't be auto-filled):</div>`
      + semantic.map(e => errLine(e, true)).join('')
      // Slice B: offer to escalate to the LLM tier (only from the deterministic view).
      + (!isLLM && canEdit('project.usecases')
          ? `<button class="btn ghost btn-sm" style="margin-top:8px;font-size:10px;border-radius:999px;" onclick="_llmAssistFix()" title="Ask the project's UC-authoring model to draft the missing written content">✦ AI-assist the rest</button>`
          : '');
  } else if (remaining.length) {
    statusHtml = remaining.map(e => errLine(e, false)).join('');
  }

  // LLM explanation (what the model wrote + why), shown above the change list when present.
  const explHtml = (isLLM && r.explanation)
    ? sect('AI draft', `<div style="font-size:11px;color:var(--text-dim);white-space:pre-wrap;">${esc(r.explanation)}</div>`)
    : '';

  body.innerHTML =
      explHtml
    + sect(`${before.length} validation error${before.length === 1 ? '' : 's'} — ${isLLM ? 'structural changes' : 'proposed changes'}`, changesHtml)
    + (statusHtml ? sect('After applying', statusHtml) : '')
    + sect('Proposed use case (YAML)',
        `<pre style="background:var(--bg);border:1px solid var(--border);border-radius:2px;padding:8px;font-size:10px;max-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-word;">${esc(r.proposed_yaml || '')}</pre>`);

  // Apply is enabled whenever the shown proposal strictly improves validity.
  const canApply = (r.remaining_errors || []).length < before.length && (r.proposed_yaml || '').trim();
  applyBtn.disabled = !canApply || !canEdit('project.usecases');
  applyBtn.textContent = isLLM ? 'Apply AI fix' : 'Apply fix';
}

async function _applyUCFix() {
  if (!_ucFixState || !_ucFixState.uuid) return;
  const btn = document.getElementById('ucFixApplyBtn');
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = 'Applying…';
  // Apply against the tier whose proposal is currently shown (deterministic vs LLM).
  const path = _ucFixState.method === 'llm' ? 'suggest-fix-llm' : 'suggest-fix';
  try {
    const r = await api(`/api/use-cases/${encodeURIComponent(_ucFixState.uuid)}/${path}?apply=true`, { method: 'POST' });
    toast(r.valid_after ? 'Fixed — now valid' : `Applied ${(r.changes || []).length} change(s); ${(r.remaining_errors || []).length} issue(s) still need a manual edit`);
    closeUCFix();
    await loadUCs();                 // refresh list + health flags
    if (typeof activeUCId !== 'undefined' && activeUCId === r.uuid && typeof selectUC === 'function') {
      try { selectUC(r.uuid); } catch (_) {}   // refresh the open detail pane
    }
  } catch (e) {
    toast('Apply failed: ' + e.message, true);
    btn.disabled = false; btn.textContent = label;
  }
}

// ── Bulk review + apply (TODO 2, slice C) ────────────────────────────────────
// GET /api/use-cases/fix-suggestions (dry-run over all invalid UCs) → a review list; Apply loops
// the per-UC gated apply. Selection defaults to rows the deterministic fix actually improves.
let _ucBulkItems = [];          // [{uuid,title,changes,valid_after,errors_before,remaining_errors,needs_semantic,parses}]
let _ucBulkSel = new Set();     // uuids checked for apply

function _ucBulkApplyable(it) {
  return it.parses !== false && (it.changes || []).length > 0
      && (it.remaining_errors || []).length < (it.errors_before || []).length;
}

function _ucFixEnsureBulkModal() {
  let ov = document.getElementById('ucBulkFixModal');
  if (ov) return ov;
  ov = document.createElement('div');
  ov.className = 'modal-overlay';
  ov.id = 'ucBulkFixModal';
  ov.innerHTML = `
    <div class="modal" style="max-width:820px;width:94%;">
      <div class="modal-header">
        <div class="modal-title">✦ Fix invalid use cases <span id="ucBulkSub" style="font-size:11px;color:var(--text-faint);font-weight:400;"></span></div>
        <button class="btn ghost btn-icon" onclick="closeUCBulkFix()">✕</button>
      </div>
      <div class="modal-body" id="ucBulkBody" style="max-height:64vh;overflow-y:auto;">
        <div class="empty">loading…</div>
      </div>
      <div class="modal-footer" style="display:flex;gap:8px;align-items:center;">
        <label style="font-size:11px;color:var(--text-dim);display:flex;align-items:center;gap:5px;cursor:pointer;">
          <input type="checkbox" id="ucBulkSelAll" onchange="_ucBulkToggleAll(this.checked)" style="width:auto;accent-color:var(--accent);"> select all fixable
        </label>
        <span style="flex:1;"></span>
        <button class="btn ghost" onclick="closeUCBulkFix()">Close</button>
        <button class="btn primary" id="ucBulkApplyBtn" data-edit-gate="project.usecases" onclick="_applyUCBulkFix()" disabled>Apply selected</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov) closeUCBulkFix(); });
  return ov;
}

function closeUCBulkFix() {
  const ov = document.getElementById('ucBulkFixModal');
  if (ov) ov.style.display = 'none';
  _ucBulkItems = []; _ucBulkSel = new Set();
}

async function openBulkFixModal() {
  const ov = _ucFixEnsureBulkModal();
  ov.style.display = 'flex';
  const body = document.getElementById('ucBulkBody');
  body.innerHTML = '<div class="empty">analyzing invalid use cases…</div>';
  // Project-wide (NOT scoped) so this matches the "N invalid" health pill, which is unscoped.
  // Fixing invalid UCs is an authoring-hygiene task on the (unscoped) authoring list — scoping it
  // to the masthead Scope made a corpus-set scope report "0 invalid" while the pill showed 13.
  let r;
  try {
    r = await api('/api/use-cases/fix-suggestions');
  } catch (e) {
    body.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
    return;
  }
  _ucBulkItems = r.items || [];
  _ucBulkSel = new Set(_ucBulkItems.filter(_ucBulkApplyable).map(it => it.uuid));
  const sub = document.getElementById('ucBulkSub');
  if (sub) sub.textContent = `${r.total_invalid} invalid · ${r.fixable_clean} auto-fixable · ${r.partial} partial · ${r.needs_semantic} need attention`;
  _renderUCBulk();
}

function _renderUCBulk() {
  const body = document.getElementById('ucBulkBody');
  if (!_ucBulkItems.length) { body.innerHTML = '<div class="empty" style="color:var(--green)">No invalid use cases in scope.</div>'; return; }
  body.innerHTML = _ucBulkItems.map(it => {
    const applyable = _ucBulkApplyable(it);
    const checked = _ucBulkSel.has(it.uuid) ? 'checked' : '';
    const nChanges = (it.changes || []).length;
    let badge;
    if (it.valid_after) badge = `<span style="color:var(--green);font-size:10px;">✓ becomes valid</span>`;
    else if ((it.needs_semantic || []).length) badge = `<span style="color:var(--amber,#d79a2b);font-size:10px;" title="${esc((it.needs_semantic || []).join('; '))}">⚠ ${it.needs_semantic.length} need${it.needs_semantic.length === 1 ? 's' : ''} a human/AI edit</span>`;
    else if (it.parses === false) badge = `<span style="color:var(--red);font-size:10px;">✕ YAML doesn't parse</span>`;
    else badge = `<span style="color:var(--text-dim);font-size:10px;">${(it.remaining_errors || []).length} issue(s) remain</span>`;
    return `<div style="display:flex;gap:8px;align-items:center;padding:6px 4px;border-bottom:1px solid var(--border);">
      <input type="checkbox" data-uuid="${esc(it.uuid)}" ${checked} ${applyable ? '' : 'disabled'} onchange="_ucBulkToggle('${esc(it.uuid)}',this.checked)" style="width:auto;accent-color:var(--accent);${applyable ? '' : 'opacity:.4;'}">
      <div style="flex:1;min-width:0;">
        <div style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(it.title || it.uuid)}</div>
        <div style="font-size:9px;color:var(--text-faint);">${nChanges} change${nChanges === 1 ? '' : 's'} · ${badge}</div>
      </div>
      <button class="btn ghost btn-sm" style="font-size:9px;flex:0 0 auto;" onclick="openUCFixModal('${esc(it.uuid)}')" title="Review this fix in detail">details</button>
    </div>`;
  }).join('');
  _ucBulkSyncFooter();
}

function _ucBulkToggle(uuid, on) {
  if (on) _ucBulkSel.add(uuid); else _ucBulkSel.delete(uuid);
  _ucBulkSyncFooter();
}
function _ucBulkToggleAll(on) {
  _ucBulkSel = on ? new Set(_ucBulkItems.filter(_ucBulkApplyable).map(it => it.uuid)) : new Set();
  _renderUCBulk();
}
function _ucBulkSyncFooter() {
  const btn = document.getElementById('ucBulkApplyBtn');
  if (btn) {
    btn.disabled = !_ucBulkSel.size || !canEdit('project.usecases');
    btn.textContent = _ucBulkSel.size ? `Apply ${_ucBulkSel.size} selected` : 'Apply selected';
  }
  const all = document.getElementById('ucBulkSelAll');
  const applyableN = _ucBulkItems.filter(_ucBulkApplyable).length;
  if (all) all.checked = applyableN > 0 && _ucBulkSel.size === applyableN;
}

async function _applyUCBulkFix() {
  const uuids = [..._ucBulkSel];
  if (!uuids.length) return;
  const btn = document.getElementById('ucBulkApplyBtn');
  btn.disabled = true;
  let ok = 0, skipped = 0, fail = 0;
  for (let i = 0; i < uuids.length; i++) {
    btn.textContent = `Applying ${i + 1}/${uuids.length}…`;
    try {
      await api(`/api/use-cases/${encodeURIComponent(uuids[i])}/suggest-fix?apply=true`, { method: 'POST' });
      ok++;
    } catch (e) {
      // 409 = the fix wouldn't improve validity (nothing safely applyable) → skip, not a hard fail.
      if (/\b409\b|not improve/i.test(e.message || '')) skipped++; else fail++;
    }
  }
  toast(`Fixed ${ok}${skipped ? `, ${skipped} skipped` : ''}${fail ? `, ${fail} failed` : ''}`, fail > 0);
  closeUCBulkFix();
  await loadUCs();
}

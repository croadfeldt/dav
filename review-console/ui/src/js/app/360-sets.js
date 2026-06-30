// ══════════════════════════ SETS ══════════════════════════

async function loadSets() {
  // In the merged UC/Scoping Sets view, "loadSets" populates the left-rail filter.
  // It's also called by openNewRun → _getDefaultSet() to find the project default.
  const rail = document.getElementById('setFilterRail');
  if (rail) rail.innerHTML = '<div class="empty">loading…</div>';
  try {
    const resp = await api('/api/sets');
    allSets = resp.sets || [];
    renderSetFilterRail();
    renderUCListSetBanner();
    // Keep the masthead scope selector current; drop a stale scope if its Set vanished.
    if (_activeScope && !(allSets || []).some(s => String(s.id) === String(_activeScope))) {
      _activeScope = '';
      try { localStorage.setItem('davScope', ''); } catch (_) {}
    }
    populateScopeSel();
  } catch (e) {
    if (rail) rail.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
  }
}

function renderSetFilterRail() {
  const el = document.getElementById('setFilterRail');
  if (!el) return;
  // Compute counts from allUCs (loaded by loadUCs). May be empty on first render —
  // loadUCs() calls renderUCList which re-renders independently; the rail just shows
  // counts from whatever's currently in allUCs.
  const totalUCs = (allUCs || []).length;
  const noSetUCs = (allUCs || []).filter(u => !u.set_ids || !u.set_ids.length).length;
  const items = [];
  items.push({key: '__all__',   label: 'All Use Cases', count: totalUCs, isAll: true});
  items.push({key: '__none__',  label: '(No set)', count: noSetUCs});
  // The synthetic "All Use Cases" set (id '__all__') is represented by the __all__ item
  // above in the rail — skip the duplicate from /api/sets here.
  (allSets || []).filter(s => s.id !== ALL_SET_ID).forEach(s => items.push({
    key: s.id, label: s.name, count: s.member_count, isDefault: s.is_default,
  }));

  el.innerHTML = '';
  items.forEach(it => {
    const isActive =
      (it.key === '__all__'  && activeSetId === null) ||
      (it.key === '__none__' && activeSetId === '__none__') ||
      (typeof it.key === 'number' && activeSetId === it.key);
    const item = document.createElement('div');
    item.className = 'list-item' + (isActive ? ' active' : '');
    item.style.cssText = 'padding:6px 10px;cursor:pointer;display:flex;align-items:center;gap:6px;';
    const defaultBadge = it.isDefault
      ? '<span style="font-size:8px;color:var(--accent);border:1px solid var(--accent-soft);padding:0 4px;border-radius:2px;">DEF</span>'
      : '';
    item.innerHTML = `
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;${it.isAll ? 'font-weight:500;' : ''}">
        ${esc(it.label)}
      </span>
      ${defaultBadge}
      <span style="font-size:10px;color:var(--text-faint);min-width:24px;text-align:right">${it.count}</span>
    `;
    item.addEventListener('click', () => selectSet(it.key));
    // Real Set rail items (numeric key) are drop targets for UC drag-add
    if (typeof it.key === 'number') {
      const setId = it.key;
      item.addEventListener('dragover', e => {
        if (!e.dataTransfer.types.includes('application/x-dav-uc')) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        item.style.background = 'var(--accent-bg)';
        item.style.boxShadow = 'inset 0 0 0 1px var(--accent)';
      });
      item.addEventListener('dragleave', () => {
        item.style.background = '';
        item.style.boxShadow = '';
      });
      item.addEventListener('drop', async e => {
        e.preventDefault();
        item.style.background = '';
        item.style.boxShadow = '';
        const raw = e.dataTransfer.getData('application/x-dav-uc');
        if (!raw) return;
        let uc;
        try { uc = JSON.parse(raw); } catch { return; }
        await _addUCToSet(setId, uc, it.label);
      });
    }
    el.appendChild(item);
  });
}

// ── Push to corpus (managed UC → PR in corpus repo) ──────────────────────────

let _corpusPushStatus = null;  // {configured, corpus_url, host, env_var}

async function _loadCorpusPushStatus() {
  try { _corpusPushStatus = await api('/api/corpus-push/status'); }
  catch { _corpusPushStatus = null; }
}

function _renderManagedTestBtn(data, titleText) {
  // Managed-UC test eval (v0.9.29+): the engine fetches the UC YAML from
  // the console API at run start, so no push or branch is required. Two
  // states presented:
  //   1. Direct test (always available for managed UCs) — engine
  //      materializes the UC from the console API and runs gap analysis.
  //   2. Push & test (offered as a secondary action if push is configured)
  //      — push to a PR branch AND test against that branch. Useful when
  //      the reviewer wants the test recorded against a corpus path.
  const uuid = data.uuid;
  const direct = `<button class="btn primary" title="Ingest just this managed UC now — engine fetches the YAML from the console API at ingestion start, no push required; jumps to the ingestion" onclick="testRunUC('${esc(uuid)}', '', ${attrJson(titleText)})">▶ Ingest this UC</button>`;
  const st = _corpusPushStatus || {configured:false, host:'unknown'};
  const pushReady = st.host === 'github' && st.configured;
  if (data.corpus_synced_path && data.corpus_branch) {
    // Already pushed — surface the PR-branch test path as the secondary
    const branchBtn = `<button class="btn ghost btn-sm" title="Test on the existing PR branch '${esc(data.corpus_branch)}'" onclick="testRunUC('${esc(uuid)}', ${attrJson(data.corpus_synced_path)}, ${attrJson(titleText)}, ${attrJson(data.corpus_branch)})">▶ on PR branch</button>`;
    return direct + branchBtn;
  }
  if (pushReady) {
    return direct + `<button class="btn ghost btn-sm" title="Push to corpus AND test the PR branch — useful when you want the test recorded against a corpus path" onclick="pushAndTestUC('${esc(uuid)}')">↑↦▶ Push &amp; test</button>`;
  }
  return direct;
}

function _renderPushToCorpusBtn(data) {
  // Called inside renderUCDetail for managed UCs.
  // _corpusPushStatus is loaded lazily on UC tab init; treat null as "unknown — show but disabled with hint".
  const uuid = data.uuid;
  const prUrl = data.corpus_pr_url || '';
  const prState = data.corpus_pr_state || '';
  const lcState = data.lifecycle_state || 'draft';

  // Already pushed → show the PR link + a re-push action (re-push allowed
  // for any state since the PR already exists; updating the YAML is fine)
  if (prUrl) {
    const stateLabel = prState === 'merged' ? '✓ merged' :
                       prState === 'closed' ? '⊘ closed' : '⇡ PR open';
    return `<a href="${esc(prUrl)}" target="_blank" rel="noopener" class="btn ghost" title="Open PR in GitHub" style="text-decoration:none;">${stateLabel}</a>
            <button class="btn primary" title="Push the latest YAML to the existing PR branch" onclick="pushUCToCorpus('${esc(uuid)}', true, false)">↻ Update PR</button>`;
  }

  // Not pushed yet — gate on host + token + lifecycle state
  const st = _corpusPushStatus || {configured:false, host:'unknown'};
  let disabled = '', tip = '', overrideHint = '';
  if (st.host === 'none')        { disabled = 'disabled'; tip = 'No corpus repo URL configured (Config → Sources)'; }
  else if (st.host === 'unsupported') { disabled = 'disabled'; tip = `Corpus host ${st.corpus_url} not supported yet (only GitHub today)`; }
  else if (!st.configured)       { disabled = 'disabled'; tip = `Push token not set — add ${st.env_var || 'DAV_CORPUS_PUSH_TOKEN'} to the consumer Secret`; }
  else if (lcState !== 'approved') {
    // Lifecycle gate: show as warning (clickable to override path), not hard-disabled
    tip = `UC is in '${lcState}'. Push requires 'approved'. Move it through ready → in_review → approved, or use force-push (Shift-click) to override.`;
    overrideHint = `<button class="btn ghost btn-sm" title="Force-push this UC despite not being approved (will be noted in the PR body)" onclick="pushUCToCorpus('${esc(uuid)}', false, true)" style="font-size:9px;">⚠ Force push</button>`;
    return `<button class="btn primary" title="${esc(tip)}" disabled style="opacity:0.55;cursor:not-allowed;">↑ Push to corpus</button>
            ${overrideHint}`;
  }
  else                           { tip = 'Open a PR adding this UC to the corpus repo'; }
  return `<button class="btn primary" title="${esc(tip)}" ${disabled} style="${disabled?'opacity:0.55;cursor:not-allowed;':''}" onclick="${disabled?'':`pushUCToCorpus('${esc(uuid)}', false, false)`}">↑ Push to corpus</button>`;
}

async function pushUCToCorpus(uuid, isRepush, override) {
  if (!isRepush) {
    const msg = override
      ? '⚠ Force-push this UC even though it is not in "approved" state? This will be noted in the PR body.'
      : 'Open a PR adding this UC to the corpus repo?';
    if (!confirm(msg)) return;
  }
  toast(isRepush ? 'Updating PR…' : 'Opening PR…');
  try {
    const resp = await api(`/api/use-cases/${encodeURIComponent(uuid)}/push-to-corpus`, {
      method: 'POST',
      body: JSON.stringify({override: !!override}),
    });
    toast(`PR ${resp.action} — ${resp.pr_url}`, false);
    // Refresh the UC so the button flips to PR-link / Update mode
    if (activeUCId === uuid) selectUC(uuid);
  } catch (e) {
    toast('Push failed: ' + e.message, true);
  }
}

// ── Add-to-Set picker (popover on UC detail's Scoping Sets section) ───────────────────
function _openAddSetPicker(ucUuid, ucSource, ucHandle, ucPath, anchorEl) {
  const pop = document.getElementById('ucDetailAddSetPopover');
  if (!pop) return;
  if (pop.style.display === 'block') { pop.style.display = 'none'; return; }
  if (!allSets || !allSets.length) {
    pop.innerHTML = '<div style="padding:10px 12px;font-size:11px;color:var(--text-faint);">No Scoping Sets yet. Use + New in the left rail to create one.</div>';
    pop.style.display = 'block';
    return;
  }
  // Figure out which sets this UC is already in (from the loaded UC detail's `sets` array)
  const uc = (allUCs || []).find(u => u.uuid === ucUuid);
  const memberOf = new Set((uc && uc.set_ids) || []);
  let h = '<div style="padding:4px 0;">';
  allSets.filter(s => s.id !== ALL_SET_ID).forEach(s => {
    const isMember = memberOf.has(s.id);
    h += `<div style="padding:6px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;${isMember ? 'background:var(--accent-bg);' : ''}"
             onmouseover="this.style.background='var(--bg-raised)'" onmouseout="this.style.background='${isMember ? 'var(--accent-bg)' : ''}'"
             onclick="_toggleUCSetMembership(${s.id}, '${esc(ucUuid)}', '${esc(ucSource)}', '${esc(ucHandle)}', '${esc(ucPath)}', ${isMember}, ${attrJson(s.name)})">
      <span style="font-family:var(--mono,monospace);color:${isMember ? 'var(--green)' : 'var(--text-faint)'};min-width:14px;">${isMember ? '✓' : ''}</span>
      <span style="flex:1;">${esc(s.name)}</span>
      ${s.is_default ? '<span style="font-size:9px;color:var(--accent);">DEFAULT</span>' : ''}
      <span style="font-size:10px;color:var(--text-faint);">${s.member_count}</span>
    </div>`;
  });
  h += '</div>';
  pop.innerHTML = h;
  pop.style.display = 'block';
  // Close on outside click
  setTimeout(() => {
    const close = e => {
      if (!pop.contains(e.target) && e.target !== anchorEl) {
        pop.style.display = 'none';
        document.removeEventListener('click', close);
      }
    };
    document.addEventListener('click', close);
  }, 0);
}

async function _toggleUCSetMembership(setId, ucUuid, ucSource, ucHandle, ucPath, isMember, setName) {
  try {
    if (isMember) {
      await api(`/api/sets/${setId}/members/${encodeURIComponent(ucUuid)}`, {method: 'DELETE'});
      toast(`Removed from "${setName}"`);
    } else {
      await api(`/api/sets/${setId}/members`, {
        method: 'POST',
        body: JSON.stringify({
          uc_uuid: ucUuid,
          uc_source: ucSource || 'managed',
          uc_handle: ucHandle || null,
          uc_path: ucPath || null,
        }),
      });
      toast(`Added to "${setName}"`);
    }
    document.getElementById('ucDetailAddSetPopover').style.display = 'none';
    await loadSets();
    await loadUCs();
    // Re-render the UC detail to refresh the chip strip
    if (activeUCId === ucUuid) selectUC(ucUuid);
    _refreshSetMgmt();
  } catch (e) {
    if (/already/i.test(e.message)) toast(`Already in "${setName}"`);
    else toast('Failed: ' + e.message, true);
  }
}

async function _removeUCFromSet(setId, ucUuid, setName) {
  try {
    await api(`/api/sets/${setId}/members/${encodeURIComponent(ucUuid)}`, {method: 'DELETE'});
    toast(`Removed from "${setName}"`);
    await loadSets();
    await loadUCs();
    if (activeUCId === ucUuid) selectUC(ucUuid);
    _refreshSetMgmt();
  } catch (e) { toast('Remove failed: ' + e.message, true); }
}

async function _addUCToSet(setId, uc, setLabel) {
  try {
    await api(`/api/sets/${setId}/members`, {
      method: 'POST',
      body: JSON.stringify({
        uc_uuid:   uc.uuid,
        uc_source: uc.source || 'managed',
        uc_handle: uc.handle || null,
        uc_path:   uc.path || null,
      }),
    });
    toast(`Added to "${setLabel}"`);
    await loadSets();
    await loadUCs();
    _refreshSetMgmt();
  } catch (e) {
    // Idempotent-ish — duplicate adds return 409
    if (/already/i.test(e.message)) toast(`Already in "${setLabel}"`);
    else toast('Add failed: ' + e.message, true);
  }
}

function _getDefaultSet() {
  return (allSets || []).find(s => s.is_default) || null;
}

// In the merged view, selectSet sets the UC-list filter rather than opening
// a Scoping Set detail pane. Accepts a numeric set id, '__none__' (no set), or null/'__all__'.
function selectSet(key) {
  if (key === '__all__' || key === null || key === undefined) activeSetId = null;
  else activeSetId = key;
  renderSetFilterRail();
  renderUCListSetBanner();
  renderUCList();
}

function renderUCListSetBanner() {
  const banner = document.getElementById('ucListSetBanner');
  const label  = document.getElementById('ucListSetBannerLabel');
  if (!banner || !label) return;
  if (activeSetId === null) {
    // "All Use Cases" — the synthetic set. Runnable (run/arch-review against
    // everything), but not editable: no Manage, no Clear (it IS the cleared state).
    banner.style.display = 'flex';
    const n = (allUCs || []).length;
    label.textContent = `All Use Cases (${n} UC${n !== 1 ? 's' : ''})`;
    document.getElementById('ucListSetBannerRunBtn').style.display = '';
    document.getElementById('ucListSetBannerManageBtn').style.display = 'none';
    document.getElementById('ucListSetBannerClearBtn').style.display = 'none';
    return;
  }
  // Real sets (and No-set) show the clear (×) control.
  document.getElementById('ucListSetBannerClearBtn').style.display = '';
  if (activeSetId === '__none__') {
    banner.style.display = 'flex';
    label.textContent = 'Filtered: (No set)';
    document.getElementById('ucListSetBannerRunBtn').style.display = 'none';
    document.getElementById('ucListSetBannerManageBtn').style.display = 'none';
    return;
  }
  const set = (allSets || []).find(s => s.id === activeSetId);
  if (!set) { banner.style.display = 'none'; return; }
  banner.style.display = 'flex';
  const defBit = set.is_default ? ' · DEFAULT' : '';
  label.textContent = `Scoping Set: ${set.name} (${set.member_count} UC${set.member_count !== 1 ? 's' : ''})${defBit}`;
  document.getElementById('ucListSetBannerRunBtn').style.display = '';
  document.getElementById('ucListSetBannerManageBtn').style.display = '';
}

// ── Manage Scoping Sets modal ─────────────────────────────────────────────────────────

// ── Scoping Set management — shared by the Authoring → Scoping Sets tab (primary) and the
// legacy ⚙ modal. The tab is now canonical; the modal trigger redirects to it so the row
// ids (setMembers-N, exportSetMenu-N) only ever live in one DOM subtree.

function openManageSetsModal() {
  // Legacy trigger (⚙ in the UC view) — now opens the Scoping Sets tab.
  switchView('scopingsets');
}
function closeManageSetsModal() {
  const m = document.getElementById('manageSetsModal');
  if (m) m.classList.remove('open');
}

// Authoring → Scoping Sets tab — TWO PANES. Left: a static, filterable full Use Case list
// (drag source, "rows of use cases" format + the Use Cases-tab filters incl. an Unassigned
// filter). Right: the vertical Scoping Set accordion (_renderSetMgmtInto), each set a drop
// target. Drag a UC from the left onto a set to ADD it (a UC can be in many sets).
let _ssAllUcs = [];          // full, source-filter-independent UC list for the palette
let _ssFiltersWired = false;
async function _ssLoadAll() {
  await loadSets();
  try {
    const r = await api('/api/use-cases');
    _ssAllUcs = r.use_cases || [];
    allUCs = _ssAllUcs;   // keep the shared list current so accordion member-title lookup works
  } catch (_) {}
}
async function loadScopingSets() {
  const list = document.getElementById('scopingSetsList');
  if (list) list.innerHTML = '<div class="empty" style="padding:24px;">loading…</div>';
  _ssWirePaletteFilters();
  await _ssLoadAll();
  renderScopingSetsBoard();
}
// Aliases so the shared mutation path (_refreshSetMgmt) + legacy callers land here.
function renderScopingSetsList() { renderScopingSetsBoard(); }
function renderManageSetsList()  { _renderSetMgmtInto('manageSetsList'); }
function _refreshSetMgmt() {
  const m = document.getElementById('manageSetsModal');
  if (m && m.classList.contains('open')) { renderManageSetsList(); return; }
  // Scoping Sets tab: reload the full UC list (membership badges) + re-render both panes.
  loadScopingSets();
}
// Render both panes (called after data load).
function renderScopingSetsBoard() {
  _ssRenderPalette();
  _renderSetMgmtInto('scopingSetsList');
  _ssWireSetDrops();
}
// One-time: wire palette filter inputs + drag-source delegation (the shell is static markup).
function _ssWirePaletteFilters() {
  if (_ssFiltersWired) return;
  const pal = document.getElementById('ssUcPalette');
  if (!pal) return;
  _ssFiltersWired = true;
  ['ssUcSearch', 'ssUcAssign', 'ssUcSource', 'ssUcState'].forEach(id => {
    const e = document.getElementById(id);
    if (e) e.addEventListener(id === 'ssUcSearch' ? 'input' : 'change', _ssRenderPalette);
  });
  pal.addEventListener('dragstart', e => {
    const row = e.target.closest('.ss-uc'); if (!row) return;
    e.dataTransfer.effectAllowed = 'copy';
    e.dataTransfer.setData('application/x-dav-uc', JSON.stringify({
      uuid: row.dataset.uuid, source: row.dataset.source,
      handle: row.dataset.handle || null, path: row.dataset.path || null }));
    row.classList.add('ss-dragging');
  });
  pal.addEventListener('dragend', e => {
    const row = e.target.closest('.ss-uc'); if (row) row.classList.remove('ss-dragging');
  });
}
function _ssRenderPalette() {
  const el = document.getElementById('ssUcPalette');
  if (!el) return;
  const q = (document.getElementById('ssUcSearch')?.value || '').toLowerCase();
  const assign = document.getElementById('ssUcAssign')?.value || '';
  const source = document.getElementById('ssUcSource')?.value || '';
  const state = document.getElementById('ssUcState')?.value || '';
  const setName = {};
  (allSets || []).forEach(s => { setName[s.id] = s.name; });
  let list = (_ssAllUcs || []).filter(u => {
    const nSets = (u.set_ids || []).length;
    if (assign === 'unassigned' && nSets) return false;
    if (assign === 'assigned' && !nSets) return false;
    if (source && (u.source || 'managed') !== source) return false;
    if (state && u.source !== 'corpus' && u.lifecycle_state !== state) return false;
    if (q) {
      const hay = `${u.title || ''} ${u.uuid || ''} ${u.handle || ''} ${(u.tags || []).join(' ')}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const cnt = document.getElementById('ssUcCount');
  if (cnt) cnt.textContent = `${list.length} / ${(_ssAllUcs || []).length}`;
  if (!list.length) { el.innerHTML = '<div class="empty" style="padding:18px;">No use cases match.</div>'; return; }
  el.innerHTML = list.map(u => {
    const title = u.title || u.handle || u.uuid || '?';
    const sub = (u.handle && u.handle !== title) ? u.handle : u.uuid;
    const ids = u.set_ids || [];
    const sets = ids.length
      ? ids.map(id => `<span class="ss-uc-setchip">${esc(setName[id] || ('#' + id))}</span>`).join('')
      : '<span class="ss-uc-unassigned">unassigned</span>';
    return `<div class="ss-uc" draggable="true" data-uuid="${esc(u.uuid)}" data-source="${esc(u.source || 'managed')}"
                 data-handle="${esc(u.handle || '')}" data-path="${esc(u.path || '')}" title="Drag onto a Scoping Set to add">
      <span class="ss-uc-grip" aria-hidden="true">⋮⋮</span>
      <div class="ss-uc-main">
        <div class="ss-uc-title">${esc(title)}</div>
        <div class="ss-uc-sub">${esc(sub || '')}</div>
        <div class="ss-uc-sets">${sets}</div>
      </div>
      <span class="src-badge src-${esc(u.source || 'managed')}" style="font-size:8px;flex-shrink:0;">${esc(u.source || 'managed')}</span>
    </div>`;
  }).join('');
}
// Make each set row in the accordion a drop target for UC drags (application/x-dav-uc).
function _ssWireSetDrops() {
  const list = document.getElementById('scopingSetsList');
  if (!list) return;
  list.querySelectorAll('[data-set-row]').forEach(row => {
    const setId = parseInt(row.getAttribute('data-set-row'), 10);
    if (!Number.isFinite(setId)) return;
    row.addEventListener('dragover', e => {
      if (!e.dataTransfer.types.includes('application/x-dav-uc')) return;
      e.preventDefault(); e.dataTransfer.dropEffect = 'copy';
      row.classList.add('ss-drop');
    });
    row.addEventListener('dragleave', e => { if (e.target === row) row.classList.remove('ss-drop'); });
    row.addEventListener('drop', async e => {
      e.preventDefault(); row.classList.remove('ss-drop');
      const raw = e.dataTransfer.getData('application/x-dav-uc');
      if (!raw) return;
      let uc; try { uc = JSON.parse(raw); } catch (_) { return; }
      const name = (allSets || []).find(s => s.id === setId)?.name || `set ${setId}`;
      await _ssDropAddUC(setId, uc, name);
    });
  });
}
async function _ssDropAddUC(setId, uc, label) {
  try {
    await api(`/api/sets/${setId}/members`, {
      method: 'POST',
      body: JSON.stringify({ uc_uuid: uc.uuid, uc_source: uc.source || 'managed',
                             uc_handle: uc.handle || null, uc_path: uc.path || null }),
    });
    toast(`Added to "${label}"`);
  } catch (e) {
    if (/already/i.test(e.message)) toast(`Already in "${label}"`);
    else { toast('Add failed: ' + e.message, true); return; }
  }
  await _ssLoadAll();
  renderScopingSetsBoard();
}

function _renderSetMgmtInto(elId) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!allSets || !allSets.length) {
    el.innerHTML = '<div class="empty" style="padding:24px;">No Scoping Sets yet. Use + New Scoping Set to create one.</div>';
    return;
  }
  let h = '';
  allSets.filter(s => s.id !== ALL_SET_ID).forEach(s => {
    const defBadge = s.is_default
      ? '<span style="font-size:9px;color:var(--accent);border:1px solid var(--accent-soft);padding:1px 5px;border-radius:2px;margin-left:8px;">DEFAULT</span>'
      : '';
    const defBtn = s.is_default
      ? `<button class="btn ghost btn-sm" title="Clear default" onclick="clearDefaultSet(${s.id}).then(_refreshSetMgmt)">Clear default</button>`
      : `<button class="btn ghost btn-sm" title="Mark as project default" onclick="setDefaultSet(${s.id}).then(_refreshSetMgmt)">★ Default</button>`;
    h += `<div data-set-row="${s.id}" style="border-top:1px solid var(--border);">
      <div style="display:flex;align-items:center;gap:8px;padding:10px 16px;">
        <button class="btn ghost btn-icon" title="Show / hide members" onclick="_toggleSetMembers(${s.id}, this)" style="min-width:18px;padding:2px 6px;">▶</button>
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;font-weight:500;">${esc(s.name)}${defBadge}</div>
          <div style="font-size:11px;color:var(--text-faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
            ${esc(s.description || '—')} · ${s.member_count} UC${s.member_count !== 1 ? 's' : ''}
          </div>
          ${(s.created_at || s.updated_at) ? `<div style="font-size:9px;color:var(--text-faint);margin-top:2px;" title="${esc((s.created_by ? 'created by ' + s.created_by + ' · ' : '') + (s.created_at ? new Date(s.created_at).toLocaleString() : '') + (s.updated_at ? ' · updated ' + new Date(s.updated_at).toLocaleString() : ''))}">
            ${s.created_at ? 'created ' + esc(fmtTs(s.created_at)) : ''}${s.updated_at ? ' · updated ' + esc(fmtTs(s.updated_at)) : ''}
          </div>` : ''}
        </div>
        <button class="btn primary btn-sm" onclick="runSet(${s.id}, ${attrJson(s.name)})">▶ Ingest</button>
        ${defBtn}
        <button class="btn ghost btn-sm" onclick="openSetModal(${s.id})">Edit</button>
        <button class="btn ghost btn-sm" onclick="openPromoteModal(${s.id}, ${attrJson(s.name)})">↑ Promote</button>
        <div style="position:relative;display:inline-block;">
          <button class="btn ghost btn-sm" onclick="toggleExportSetMenu(${s.id}, event)">↓ Export</button>
          <div id="exportSetMenu-${s.id}" style="display:none;position:absolute;right:0;top:100%;margin-top:4px;background:var(--bg-panel);border:1px solid var(--border-bright);border-radius:2px;z-index:50;min-width:140px;">
            <button class="dropdown-item" onclick="exportSet(${s.id},'tar.gz')">tar.gz (gzip)</button>
            <button class="dropdown-item" onclick="exportSet(${s.id},'zip')">zip</button>
            <button class="dropdown-item" onclick="exportSet(${s.id},'tar')">tar (uncompressed)</button>
          </div>
        </div>
        <button class="btn ghost btn-sm" onclick="openAddMember(${s.id})" title="Add a UC to this Scoping Set">+ UC</button>
        <button class="btn danger btn-sm" onclick="deleteSet(${s.id}, this)">×</button>
      </div>
      <div id="setMembers-${s.id}" style="display:none;padding:0 0 10px 50px;"></div>
    </div>`;
  });
  el.innerHTML = h;
}

async function _toggleSetMembers(setId, btn) {
  const box = document.getElementById(`setMembers-${setId}`);
  if (!box) return;
  if (box.style.display !== 'none') {
    box.style.display = 'none';
    btn.textContent = '▶';
    return;
  }
  btn.textContent = '▼';
  box.style.display = 'block';
  box.innerHTML = '<div style="padding:4px 8px;color:var(--text-faint);font-size:11px;">loading…</div>';
  try {
    const data = await api(`/api/sets/${setId}`);
    const members = data.members || [];
    if (!members.length) {
      box.innerHTML = '<div style="padding:4px 8px;color:var(--text-faint);font-size:11px;">No members. Use + UC to add.</div>';
      return;
    }
    const rows = members.map(m => {
      // Best-effort title: look up in allUCs to get the human-readable name
      const uc = (allUCs || []).find(u => u.uuid === m.uc_uuid);
      const title = (uc && uc.title) || m.uc_handle || m.uc_uuid;
      return `<div style="display:flex;align-items:center;gap:6px;padding:3px 8px;border-bottom:1px solid var(--border);font-size:11px;">
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;color:var(--accent);"
              onclick="closeManageSetsModal();selectUC('${esc(m.uc_uuid)}');"
              title="${esc(m.uc_uuid)}">${esc(title)}</span>
        <span class="src-badge src-${m.uc_source}" style="font-size:9px;">${m.uc_source}</span>
        <button class="btn danger btn-icon" style="font-size:11px;padding:0 6px;" title="Remove from set"
                onclick="_removeMemberFromManage(${setId}, '${esc(m.uc_uuid)}', ${attrJson(data.name)})">×</button>
      </div>`;
    }).join('');
    box.innerHTML = `<div style="background:var(--bg-input);border:1px solid var(--border);border-radius:2px;">${rows}</div>`;
  } catch (e) {
    box.innerHTML = `<div style="padding:4px 8px;color:var(--red);font-size:11px;">Failed to load: ${esc(e.message)}</div>`;
  }
}

async function _removeMemberFromManage(setId, ucUuid, setName) {
  try {
    await api(`/api/sets/${setId}/members/${encodeURIComponent(ucUuid)}`, {method:'DELETE'});
    toast(`Removed from "${setName}"`);
    await loadSets();
    await loadUCs();
    _refreshSetMgmt();
    // Auto-re-expand the same set so the change is visible
    const btn = document.querySelector(`[data-set-row="${setId}"] button[onclick^="_toggleSetMembers"]`);
    if (btn) _toggleSetMembers(setId, btn);
  } catch (e) { toast('Remove failed: ' + e.message, true); }
}

async function setDefaultSet(setId) {
  try {
    await api(`/api/sets/${setId}/default`, {method: 'PUT'});
    toast('Marked as default Scoping Set');
    await loadSets();
  } catch (e) { toast('Failed: ' + e.message, true); }
}
async function clearDefaultSet(setId) {
  try {
    await api(`/api/sets/${setId}/default`, {method: 'DELETE'});
    toast('Default cleared');
    await loadSets();
  } catch (e) { toast('Failed: ' + e.message, true); }
}

async function runSet(setId, setName) {
  try {
    const info = await api(`/api/sets/${setId}/corpus-subpath`);
    const set  = await api(`/api/sets/${setId}`);
    const filter = _filterFromSetMembers(set.members || []);
    const total = (set.members || []).length;
    if (!total) {
      toast(`"${setName}" is empty — add UCs first (drag from the UC list, or use + UC in the Manage Scoping Sets modal).`, true);
      return;
    }
    const bits = [];
    if (info.corpus_count) bits.push(`${info.corpus_count} corpus`);
    if (info.managed_count) bits.push(`${info.managed_count} managed (fetched from API)`);
    const banner = `Running set "${setName}" — ${bits.join(' + ')} · engine-filtered to exactly these UCs`;
    openNewRun(banner, info.subpath || '', filter, undefined,
               { set_id: setId, set_name: setName, selection_mode: 'set' });
  } catch (e) { toast('Could not compute set scope: ' + e.message, true); }
}

function openSetModal(id) {
  editingSetId = id || null;
  const existing = id ? allSets.find(s => s.id===id) : null;
  document.getElementById('setModalTitle').textContent = id ? 'Edit set' : 'New set';
  document.getElementById('setModalName').value  = existing ? existing.name        : '';
  document.getElementById('setModalDesc').value  = existing ? existing.description : '';
  document.getElementById('setModalStatus').textContent = '';
  document.getElementById('saveSetModal').disabled = false;
  document.getElementById('setModal').classList.add('open');
}
function closeSetModal() { document.getElementById('setModal').classList.remove('open'); editingSetId = null; }

async function saveSet() {
  const btn = document.getElementById('saveSetModal'), status = document.getElementById('setModalStatus');
  btn.disabled = true; status.textContent = 'saving…';
  const payload = {
    name:        document.getElementById('setModalName').value.trim(),
    description: document.getElementById('setModalDesc').value.trim(),
  };
  if (!payload.name) { status.innerHTML = '<span style="color:var(--red)">name required</span>'; btn.disabled = false; return; }
  try {
    let resp;
    if (editingSetId)
      resp = await api(`/api/sets/${editingSetId}`, {method:'PUT', body:JSON.stringify(payload)});
    else
      resp = await api('/api/sets', {method:'POST', body:JSON.stringify(payload)});
    toast(editingSetId ? 'Set updated' : 'Set created');
    closeSetModal();
    await loadSets();
    _refreshSetMgmt();
    // For brand-new sets, filter the UC list to show "what's in it" (empty initially)
    const newId = resp.id || editingSetId;
    if (newId && !editingSetId) selectSet(newId);
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; btn.disabled = false;
  }
}

async function deleteSet(setId, btn) {
  if (!btn) return;
  const s = allSets.find(x => x.id===setId);
  _armDeleteBtn(btn, async () => {
    if (!(await _confirmDeleteImpact('set', setId, s ? s.name : ('set ' + setId)))) return;
    try {
      await api(`/api/sets/${setId}`, {method:'DELETE'});
      toast('Set deleted');
      if (activeSetId === setId) activeSetId = null;
      await loadSets();
      await loadUCs();  // set_ids on UCs may have changed
      _refreshSetMgmt();
    } catch (e) { toast('Delete failed: ' + e.message, true); }
  });
}

async function removeMember(setId, ucUuid) {
  try {
    await api(`/api/sets/${setId}/members/${encodeURIComponent(ucUuid)}`, {method:'DELETE'});
    toast('Removed from set');
    await loadSets();
    await loadUCs();
    _refreshSetMgmt();
  } catch (e) { toast('Remove failed: ' + e.message, true); }
}

// ── Add member modal ─────────────────────────────────────────
function openAddMember(setId) {
  addMemberSetId = setId; selectedMember = null;
  document.getElementById('memberSearchInput').value = '';
  document.getElementById('memberDropdown').style.display = 'none';
  document.getElementById('memberDropdown').innerHTML = '';
  document.getElementById('selectedMemberPreview').style.display = 'none';
  document.getElementById('addMemberStatus').textContent = '';
  document.getElementById('saveAddMember').disabled = true;
  document.getElementById('addMemberModal').classList.add('open');
  setTimeout(() => document.getElementById('memberSearchInput').focus(), 80);
}
function closeAddMember() { document.getElementById('addMemberModal').classList.remove('open'); }

let memberSearchTimer = null;
function onMemberSearch(val) {
  clearTimeout(memberSearchTimer);
  if (!val.trim()) { document.getElementById('memberDropdown').style.display = 'none'; return; }
  memberSearchTimer = setTimeout(() => renderMemberDropdown(val.toLowerCase()), 180);
}

function renderMemberDropdown(query) {
  const dd = document.getElementById('memberDropdown');
  const matches = allUCs.filter(u =>
    (u.uuid||'').toLowerCase().includes(query) ||
    (u.title||'').toLowerCase().includes(query) ||
    (u.handle||'').toLowerCase().includes(query)
  ).slice(0, 12);

  if (!matches.length) { dd.style.display = 'none'; return; }
  dd.innerHTML = '';
  matches.forEach(u => {
    const item = document.createElement('div');
    item.className = 'uc-dropdown-item';
    item.innerHTML = `
      <span class="uid">${esc(u.uuid||u.handle)}</span>
      <span class="uhandle">${esc(u.title||u.handle||'')}</span>
      <span class="src-badge src-${u.source}">${u.source}</span>`;
    item.addEventListener('mousedown', (e) => { e.preventDefault(); selectMember(u); });
    dd.appendChild(item);
  });
  dd.style.display = '';
}

function selectMember(u) {
  selectedMember = u;
  document.getElementById('memberSearchInput').value = u.uuid || u.handle;
  document.getElementById('memberDropdown').style.display = 'none';
  document.getElementById('selectedMemberLabel').textContent = (u.uuid||u.handle) + (u.title ? ' — '+u.title : '');
  document.getElementById('selectedMemberPreview').style.display = '';
  document.getElementById('saveAddMember').disabled = false;
}

async function saveAddMember() {
  if (!selectedMember || !addMemberSetId) return;
  const btn = document.getElementById('saveAddMember'), status = document.getElementById('addMemberStatus');
  btn.disabled = true; status.textContent = 'adding…';
  const payload = {
    uc_uuid:   selectedMember.uuid || selectedMember.handle,
    uc_source: selectedMember.source || 'corpus',
    uc_handle: selectedMember.handle || null,
    uc_path:   selectedMember.path || null,
  };
  try {
    await api(`/api/sets/${addMemberSetId}/members`, {method:'POST', body:JSON.stringify(payload)});
    toast('Added to set');
    closeAddMember();
    await loadSets();
    await loadUCs();
    _refreshSetMgmt();
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; btn.disabled = false;
  }
}

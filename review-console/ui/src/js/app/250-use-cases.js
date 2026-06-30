// ══════════════════════════ USE CASES ══════════════════════════

// #243 — populate the corpus repo/branch filter from the namespaces present in the loaded UCs.
// Merge-in (never drop) so options persist once a single-repo filter is applied; preserve selection.
function _populateRepoFilter(ucs) {
  const sel = document.getElementById('ucRepoFilter');
  if (!sel) return;
  const seen = new Map();   // namespace -> branch (for the label)
  for (const u of (ucs || [])) {
    if (u && u.namespace) seen.set(u.namespace, u.branch || '');
  }
  if (!seen.size) return;
  const cur = sel.value;
  const have = new Set(Array.from(sel.options).map(o => o.value));
  for (const [ns, br] of Array.from(seen.entries()).sort()) {
    if (have.has(ns)) continue;
    const o = document.createElement('option');
    o.value = ns;
    o.textContent = br ? `${ns} (${br})` : ns;
    sel.appendChild(o);
  }
  if (cur && Array.from(sel.options).some(o => o.value === cur)) sel.value = cur;
}
async function loadUCs() {
  document.getElementById('ucList').innerHTML = '<div class="empty">loading…</div>';
  const src = document.getElementById('ucSourceFilter').value;
  const repoNs = (document.getElementById('ucRepoFilter') || {}).value || '';  // #243 repo/branch
  const scope = (document.getElementById('ucScopeFilter') || {}).value || '';
  _ucPoolMode = (scope === 'pool');
  // Lazy-fetch corpus-push status once per tab session so the Push button
  // can render with the right disabled/tooltip state.
  if (_corpusPushStatus === null) _loadCorpusPushStatus();
  try {
    const qp = [];
    if (src) qp.push('source=' + src);
    if (repoNs) qp.push('namespace=' + encodeURIComponent(repoNs));   // #243 corpus repo/branch filter
    // #43 "available to apply" pool = managed UCs from other projects in this tenant, not referenced
    // here. Force source=managed (corpus is repo-driven, not appliable) and request applied=0.
    if (_ucPoolMode) { qp.length = 0; qp.push('source=managed', 'applied=0'); }
    if (_activeCustomer) qp.push(customerQuery());   // matrix #130: filter to this customer's requested UCs
    const q = qp.length ? '?' + qp.join('&') : '';
    const resp = await api('/api/use-cases' + q);
    allUCs = resp.use_cases || [];
    _populateRepoFilter(allUCs);   // #243: refresh repo/branch options from the loaded corpus UCs
    // Badge = active UCs (deprecated are hidden by default), so it reconciles with the list + masthead.
    document.getElementById('badgeUC').textContent = allUCs.filter(u => u.lifecycle_state !== 'deprecated').length;
    renderUCList();
    // Counts in the rail depend on allUCs — refresh whenever UCs reload
    renderSetFilterRail();
    _loadUCHealth();   // #122: per-UC validity → flag invalid UCs in the list
  } catch (e) {
    document.getElementById('ucList').innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`;
  }
}
// #43 — reference a managed UC into the active project (use_case_projects M:N). From the
// "available to apply" pool; on success it leaves the pool and appears in the project list.
async function applyUCToProject(uuid) {
  try {
    await api('/api/use-case-projects', { method: 'POST', body: JSON.stringify({ uc_uuids: [uuid] }) });
    toast('Applied to this project');
    await loadUCs(); loadFreshness();
  } catch (e) { toast('Apply failed: ' + e.message, true); }
}
// Remove a referenced UC from the active project (un-apply the M:N; the UC itself is untouched).
async function removeUCFromProject(uuid) {
  if (!confirm('Remove this referenced use case from the active project? The use case itself is not deleted.')) return;
  try {
    await api('/api/use-case-projects/remove', { method: 'POST', body: JSON.stringify({ uc_uuids: [uuid] }) });
    toast('Removed from this project');
    await loadUCs(); loadFreshness();
  } catch (e) { toast('Remove failed: ' + e.message, true); }
}

// #122 — UC health: per-UC validity (managed UCs), used to flag invalid UCs in the list + offer repair.
let _ucHealth = {};   // uuid -> {valid, errors[], repairable}
async function _loadUCHealth() {
  try {
    const h = await api('/api/use-cases/health');
    _ucHealth = {};
    (h.ucs || []).forEach(x => { _ucHealth[x.uuid] = x; });
    renderUCList();
    const pill = document.getElementById('ucHealthPill');
    if (pill) {
      const repairable = (h.ucs || []).filter(u => u.repairable).length;
      if (!h.invalid) { pill.style.display = 'none'; pill.innerHTML = ''; }
      else {
        pill.style.display = '';
        pill.innerHTML = `<span title="Use cases failing engine validation" style="color:var(--red);font-size:10px;">⚠ ${h.invalid} invalid</span>`
          + (repairable ? ` <button class="btn ghost btn-sm" style="font-size:10px;padding:0 6px;border-radius:999px;" onclick="_repairAllUCs()" title="Backfill a missing handle (from the title) + save, for each repairable UC">⚕ Repair ${repairable}</button>` : '');
      }
    }
  } catch (_) {}
}
async function _repairAllUCs() {
  const repairable = Object.values(_ucHealth).filter(u => u.repairable);
  if (!repairable.length) return;
  if (!confirm(`Auto-repair ${repairable.length} use case(s)? This backfills a handle derived from each title (e.g. "managed/standard/secure-model-training") and saves it.`)) return;
  let ok = 0, fail = 0;
  for (const u of repairable) {
    try { const r = await api(`/api/use-cases/${encodeURIComponent(u.uuid)}/repair`, { method: 'POST' });
          ((r.repaired || []).length) ? ok++ : fail++; }
    catch (_) { fail++; }
  }
  toast(`Repaired ${ok}${fail ? `, ${fail} unchanged/failed` : ''}`);
  await loadUCs();
}

function renderUCList() {
  const el = document.getElementById('ucList');
  _ucRenderChips();   // #244: keep the filter chips in sync with the backing controls

  // Set-membership filter from the left rail (skipped in the #43 "available to apply" pool —
  // pool UCs belong to other projects, so set-rail filtering would spuriously empty it).
  let setFiltered = allUCs;
  if (!_ucPoolMode) {
    if (activeSetId === '__none__') {
      setFiltered = allUCs.filter(u => !u.set_ids || !u.set_ids.length);
    } else if (typeof activeSetId === 'number') {
      setFiltered = allUCs.filter(u => (u.set_ids || []).includes(activeSetId));
    }
  }
  const filter = (document.getElementById('ucFilter').value||'').toLowerCase();
  let filtered = setFiltered.filter(u =>
    !filter ||
    (u.uuid||'').toLowerCase().includes(filter) ||
    (u.title||'').toLowerCase().includes(filter) ||
    (u.handle||'').toLowerCase().includes(filter) ||
    (u.tags||[]).some(t => t.toLowerCase().includes(filter))
  );
  // '__all__' shows every state (incl. deprecated); a specific state filters to it; the default
  // ('active') hides deprecated UCs — they're sunset (often imported under a corpus deprecated/ dir).
  if (ucStateFilter === '__all__') { /* no state filter */ }
  else if (ucStateFilter) filtered = filtered.filter(u => u.source==='corpus' || u.lifecycle_state===ucStateFilter);
  else filtered = filtered.filter(u => u.source==='corpus' || u.lifecycle_state !== 'deprecated');
  if (ucPriorityFilter) filtered = filtered.filter(u => u.priority === ucPriorityFilter);
  // Assignment filter (unified with the Scoping Sets palette): unassigned = in no Scoping Set.
  if (ucAssignFilter === 'unassigned') filtered = filtered.filter(u => !u.set_ids || !u.set_ids.length);
  else if (ucAssignFilter === 'assigned') filtered = filtered.filter(u => (u.set_ids || []).length);
  // Health filter (#122): invalid = managed UC failing engine validation (from _ucHealth).
  if (ucHealthFilter === 'invalid') filtered = filtered.filter(u => _ucHealth[u.uuid] && !_ucHealth[u.uuid].valid);
  else if (ucHealthFilter === 'valid') filtered = filtered.filter(u => !_ucHealth[u.uuid] || _ucHealth[u.uuid].valid);
  // #244 tag facet: EXACT tag match (the free-text search above already does substring tag match).
  if (ucTagFilter) { const _tf = ucTagFilter.toLowerCase(); filtered = filtered.filter(u => (u.tags||[]).some(t => t.toLowerCase() === _tf)); }
  _lastVisibleUUIDs = filtered.map(u => u.uuid);   // for Select-all (operates on the visible/filtered set)

  // Roadmap-weight ordering (DCM feature #1): highest priority.score first,
  // unranked UCs last. Sorts within each source group below.
  if (ucSortByPriority) {
    const w = u => (u.priority_score == null ? -1 : u.priority_score);
    filtered = filtered.slice().sort((a, b) => w(b) - w(a));
  }
  const _ucCnt = document.getElementById('ucCount');
  if (_ucCnt) _ucCnt.textContent = `${filtered.length} / ${(allUCs || []).length}`;

  if (!filtered.length) { el.innerHTML = `<div class="empty">${_ucPoolMode ? 'No other-project use cases available to apply.' : 'No use cases match.'}</div>`; return; }
  el.innerHTML = '';
  if (_ucPoolMode) {
    const b = document.createElement('div');
    b.style.cssText = 'font-size:10px;color:var(--text-dim);padding:4px 8px;border-bottom:1px solid var(--border);margin-bottom:4px;';
    b.textContent = 'Available to apply — managed use cases from other projects in this tenant. Apply to reference one into this project.';
    el.appendChild(b);
  }

  const managed = filtered.filter(u => u.source==='managed');
  const corpus  = filtered.filter(u => u.source==='corpus');

  const renderGroup = (items, label) => {
    if (!items.length) return;
    const gl = document.createElement('div');
    gl.className = 'list-group-label'; gl.textContent = label;
    el.appendChild(gl);
    items.forEach(u => {
      const item = document.createElement('div');
      item.className = 'list-item' + (activeUCId===u.uuid ? ' active' : '');
      item.draggable = true;
      item.title = 'Drag to a Scoping Set in the left rail · Checkbox selects for batch ops';
      item.dataset.ucUuid = u.uuid;
      item.dataset.ucSource = u.source;
      item.dataset.ucHandle = u.handle || '';
      item.dataset.ucPath = u.path || '';
      const tags = (u.tags||[]).slice(0,2).map(t => `<span class="tag">${esc(t)}</span>`).join('');
      const titleText = u.title || u.handle || u.uuid || '?';
      const subText = (u.handle && u.handle !== titleText) ? u.handle : u.uuid;
      const isChecked = _selectedUCs.has(u.uuid);
      // When the list is filtered to a single Set, show a small × to remove
      // this UC from THAT set in-place (no need to leave the list).
      const setRemoveBtn = (typeof activeSetId === 'number')
        ? `<button class="li-set-remove" title="Remove from this Scoping Set"
                   style="background:none;border:none;color:var(--red);font-size:14px;line-height:1;padding:0 4px;cursor:pointer;opacity:0.6;align-self:flex-start;margin-top:1px;"
                   onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.6'"
           >×</button>`
        : '';
      // #43 cross-project reference: Apply (pool mode) / Remove (a UC referenced from another project).
      const applyCtl = (canEdit('project.usecases') && u.source === 'managed')
        ? (_ucPoolMode
            ? `<button class="btn ghost btn-sm" style="font-size:9px;padding:0 7px;border-radius:999px;" onclick="event.stopPropagation();applyUCToProject('${esc(u.uuid)}')" title="Reference this use case into the active project">+ Apply</button>`
            : (u.referenced
                ? `<span title="Referenced from another project in this tenant — not authored here" style="font-size:8px;background:var(--surface-2,#2a2a2a);color:var(--text-dim);border-radius:2px;padding:0 4px;">↪ ref</span>
                   <button class="btn ghost btn-sm" style="font-size:9px;padding:0 7px;border-radius:999px;color:var(--red);" onclick="event.stopPropagation();removeUCFromProject('${esc(u.uuid)}')" title="Remove this referenced UC from the active project (the use case itself is untouched)">Remove</button>`
                : ''))
        : '';
      item.innerHTML = `
        <input type="checkbox" class="uc-sel-cb" ${isChecked ? 'checked' : ''}
               style="margin:2px 6px 0 0;width:auto;height:auto;accent-color:var(--accent);flex-shrink:0;align-self:flex-start;cursor:pointer;"
               title="Select for batch test / add-to-set" />
        <div class="li-main">
          <div class="li-title">${esc(titleText)}</div>
          <div class="li-sub" style="font-family:var(--mono,monospace);font-size:10px;opacity:0.7;">${esc(subText || '')}</div>
          ${tags ? `<div style="margin-top:3px">${tags}</div>` : ''}
        </div>
        <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;flex-shrink:0;">
          ${u.lifecycle_state
              ? (canEdit('project.usecases')
                  ? `<span onclick="event.stopPropagation();_lcMenu(event,'${esc(u.uuid)}','${esc(u.lifecycle_state)}')" title="Change status" style="cursor:pointer;">${lcHtml(u.lifecycle_state)} <span style="font-size:8px;opacity:.55;">▾</span></span>`
                  : lcHtml(u.lifecycle_state))
              : `<span class="src-badge src-${u.source}">${u.source}</span>`}
          ${(_ucHealth[u.uuid] && !_ucHealth[u.uuid].valid)
              ? `<span title="Fails engine validation: ${esc((_ucHealth[u.uuid].errors || []).join('; '))}${_ucHealth[u.uuid].repairable ? ' — auto-repairable (open + ⚕ Repair)' : ''}" style="font-size:8px;background:var(--red);color:#fff;border-radius:2px;padding:0 4px;cursor:help;">⚠ invalid</span>`
              : ''}
          ${prioHtml(u.priority, u.priority_score)}
          ${readinessHtml(u.readiness_score)}
          ${demandHtml(u)}
          ${dupHtml(u)}
          ${applyCtl}
        </div>
        ${setRemoveBtn}`;
      // Row click selects/opens the UC; checkbox toggles batch selection
      // (checkbox click stops propagation so it doesn't also open the UC)
      const cb = item.querySelector('input.uc-sel-cb');
      cb.addEventListener('click', e => e.stopPropagation());
      cb.addEventListener('change', e => {
        if (e.target.checked) _selectedUCs.add(u.uuid);
        else _selectedUCs.delete(u.uuid);
        _renderUCSelectionToolbar();
      });
      const removeBtn = item.querySelector('button.li-set-remove');
      if (removeBtn) {
        removeBtn.addEventListener('click', e => {
          e.stopPropagation();   // don't trigger row select
          const setName = (allSets.find(s => s.id === activeSetId) || {}).name || '';
          _removeUCFromSet(activeSetId, u.uuid, setName);
        });
      }
      item.addEventListener('click', () => selectUC(u.uuid));
      item.addEventListener('dragstart', e => {
        e.dataTransfer.effectAllowed = 'copy';
        e.dataTransfer.setData('application/x-dav-uc', JSON.stringify({
          uuid: u.uuid, source: u.source, handle: u.handle || null, path: u.path || null,
        }));
        item.style.opacity = '0.55';
      });
      item.addEventListener('dragend', () => { item.style.opacity = ''; });
      el.appendChild(item);
    });
  };

  renderGroup(managed, 'Managed');
  renderGroup(corpus,  'Corpus');
  _renderUCSelectionToolbar();
}

// ── Multi-select batch operations on the UC list ─────────────────────────────

function _renderUCSelectionToolbar() {
  const bar   = document.getElementById('ucSelectionToolbar');
  const count = document.getElementById('ucSelectionCount');
  if (!bar || !count) return;
  // Stale selection cleanup: drop UUIDs no longer in allUCs
  const present = new Set(allUCs.map(u => u.uuid));
  for (const uuid of [..._selectedUCs]) {
    if (!present.has(uuid)) _selectedUCs.delete(uuid);
  }
  const n = _selectedUCs.size;
  if (!n) { bar.style.display = 'none'; return; }
  const selected = allUCs.filter(u => _selectedUCs.has(u.uuid));
  const managedCount = selected.filter(u => u.source === 'managed').length;
  const corpusCount  = selected.length - managedCount;
  bar.style.display = 'flex';
  // All UCs are testable now — managed via console-API fetch, corpus via the
  // usual handle filter. Surface the split so reviewers understand the scope.
  let label = `${n} selected`;
  if (managedCount && corpusCount)       label += ` (${corpusCount} corpus + ${managedCount} managed — fetched from API)`;
  else if (managedCount && !corpusCount) label += ` (managed — fetched from API)`;
  count.textContent = label;
  document.getElementById('ucSelTestBtn').disabled = false;
  document.getElementById('ucSelTestBtn').style.opacity = '';
}

function _clearUCSelection() {
  _selectedUCs.clear();
  renderUCList();   // re-render to uncheck boxes + hide toolbar
}

async function _batchTestSelectedUCs() {
  const selected = allUCs.filter(u => _selectedUCs.has(u.uuid));
  if (!selected.length) {
    toast('Nothing selected to test', true);
    return;
  }
  const handles = [], uuids = [], managed = [], paths = [];
  for (const u of selected) {
    if (u.source === 'managed') {
      managed.push(u.uuid);
    } else {
      if (u.handle) handles.push(u.handle);
      else          uuids.push(u.uuid);
      if (u.path)   paths.push(u.path);
    }
  }
  const subpath = paths.length ? _narrowestSubpath(paths) : '';
  const counts = [
    handles.length || uuids.length ? `${handles.length+uuids.length} corpus` : null,
    managed.length ? `${managed.length} managed (fetched from API)` : null,
  ].filter(Boolean).join(' + ');
  const banner = `Batch test eval: ${counts} · engine-filtered`;
  // R2: if the user filtered the list to a Scoping Set and then ran the selection,
  // record that Scoping Set as lineage; otherwise mark as ad-hoc selection.
  const lineage = (typeof activeSetId === 'number')
    ? { set_id: activeSetId, set_name: (allSets.find(s => s.id===activeSetId)||{}).name, selection_mode: 'selection' }
    : { selection_mode: 'selection' };
  await openNewRun(banner, subpath, {handles, uuids, managed}, undefined, lineage);
  const sn = document.getElementById('nrSessionName');
  if (sn && !sn.value) sn.value = `batch test: ${selected.length} UCs`.slice(0, 120);
}

async function _openBatchAddSetPopover(anchor) {
  const pop = document.getElementById('ucSelAddSetPopover');
  if (!pop) return;
  if (pop.style.display === 'block') { pop.style.display = 'none'; return; }
  if (!allSets || !allSets.length) {
    pop.innerHTML = '<div style="padding:10px 12px;font-size:11px;color:var(--text-faint);">No Scoping Sets. Create one with + New in the left rail.</div>';
    pop.style.display = 'block';
    return;
  }
  let h = '<div style="padding:4px 0;font-size:12px;">';
  allSets.filter(s => s.id !== ALL_SET_ID).forEach(s => {
    h += `<div style="padding:6px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;"
             onmouseover="this.style.background='var(--bg-raised)'" onmouseout="this.style.background=''"
             onclick="_batchAddSelectedToSet(${s.id}, ${attrJson(s.name)})">
      <span style="flex:1;">${esc(s.name)}</span>
      ${s.is_default ? '<span style="font-size:9px;color:var(--accent);">DEFAULT</span>' : ''}
      <span style="font-size:10px;color:var(--text-faint);">${s.member_count}</span>
    </div>`;
  });
  h += '</div>';
  pop.innerHTML = h;
  pop.style.display = 'block';
  setTimeout(() => {
    const close = e => {
      if (!pop.contains(e.target) && e.target !== anchor) {
        pop.style.display = 'none';
        document.removeEventListener('click', close);
      }
    };
    document.addEventListener('click', close);
  }, 0);
}

async function _batchAddSelectedToSet(setId, setName) {
  document.getElementById('ucSelAddSetPopover').style.display = 'none';
  const selected = allUCs.filter(u => _selectedUCs.has(u.uuid));
  let added = 0, dup = 0, fail = 0;
  for (const u of selected) {
    try {
      await api(`/api/sets/${setId}/members`, {
        method: 'POST',
        body: JSON.stringify({
          uc_uuid:   u.uuid,
          uc_source: u.source || 'managed',
          uc_handle: u.handle || null,
          uc_path:   u.path || null,
        }),
      });
      added++;
    } catch (e) {
      if (/already/i.test(e.message)) dup++;
      else { fail++; console.warn('add to set failed', u.uuid, e.message); }
    }
  }
  const summary = [
    added ? `${added} added` : null,
    dup   ? `${dup} already in set` : null,
    fail  ? `${fail} failed` : null,
  ].filter(Boolean).join(' · ');
  toast(`"${setName}": ${summary || 'no-op'}`, fail > 0);
  await loadSets();
  await loadUCs();
  _refreshSetMgmt();
}

async function selectUC(uuid) {
  activeUCId = uuid; renderUCList();
  renderUCDetailLoading();
  try {
    const data = await api(`/api/use-cases/${encodeURIComponent(uuid)}`);
    let lifecycle = null;
    if (data.source === 'managed') {
      try { lifecycle = await api(`/api/use-cases/${encodeURIComponent(uuid)}/lifecycle`); } catch {}
    }
    renderUCDetail(data, lifecycle);
  } catch (e) {
    document.getElementById('ucDetail').innerHTML =
      `<div class="detail-pane"><div style="color:var(--red);padding-top:40px">${esc(e.message)}</div></div>`;
  }
}

function renderUCDetailLoading() {
  document.getElementById('ucDetail').innerHTML =
    '<div class="detail-pane"><div class="detail-empty">loading…</div></div>';
}

function renderUCDetail(data, lifecycle) {
  const el = document.getElementById('ucDetail');
  if (!data) { el.innerHTML = '<div class="detail-pane"><div class="detail-empty">Select a use case.</div></div>'; return; }

  const isManaged = data.source === 'managed';
  const parsed = data.parsed || {};
  const scenario = parsed.scenario || {};
  const tags = data.tags || parsed.tags || [];
  const uuid = data.uuid;
  const state = data.lifecycle_state || 'draft';
  const sets = data.sets || [];
  const titleText = parsed.title || data.title || parsed.handle || uuid;
  const handleText = parsed.handle || data.handle || '';
  // Priority (DCM feature #1): prefer the projected column (managed UCs), fall
  // back to the raw YAML value (corpus UCs / shorthand or nested form).
  const _pr = parsed.priority;
  const prioLabel = data.priority || (typeof _pr === 'string' ? _pr : (_pr && _pr.label)) || null;
  const prioScore = (data.priority_score != null) ? data.priority_score
                    : (_pr && typeof _pr === 'object' ? _pr.score : null);

  let html = `<div class="detail-pane">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:4px">
      <div style="min-width:0;flex:1">
        <div class="detail-title" style="font-family:var(--serif);font-size:18px;line-height:1.25;">${esc(titleText)}</div>
        <div class="detail-sub" style="font-family:var(--mono,monospace);font-size:11px;color:var(--text-faint);margin-top:4px;">
          ${handleText ? `<span title="handle">⌘ ${esc(handleText)}</span> · ` : ''}<span title="UUID">id ${esc(uuid)}</span>
        </div>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0;margin-top:4px">
        ${isManaged
          ? `${_renderPushToCorpusBtn(data)}
             ${_renderManagedTestBtn(data, titleText)}
             <button class="btn ghost" onclick="editUC('${esc(uuid)}')">Edit</button>
             <button class="btn danger" onclick="deleteUC('${esc(uuid)}',this)">Delete</button>`
          : `<button class="btn primary" title="Ingest just this UC now (project defaults) and jump to the ingestion" onclick="testRunUC('${esc(uuid)}', ${attrJson(data.path||'')}, ${attrJson(titleText)})">▶ Ingest this UC</button>
             <button class="btn ghost" onclick="cloneUC('${esc(uuid)}')">Clone to managed</button>`}
      </div>
    </div>
    <div style="margin-bottom:14px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
      ${prioLabel ? prioHtml(prioLabel, prioScore) : ''}
      ${readinessHtml(data.readiness_score)}
      ${tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}
    </div>`;

  // ── Lifecycle state machine (managed only)
  if (isManaged) {
    const transitions = LC_TRANSITIONS[state] || [];
    html += `<div class="lc-machine">
      <div class="lc-machine-row">
        <span class="lc-machine-label">State</span>
        ${lcHtml(state)}
      </div>
      ${transitions.length ? `<div class="lc-machine-row">
        <span class="lc-machine-label">Transition</span>
        <div class="lc-actions">
          ${transitions.map(t =>
            `<button class="${t.cls}" onclick="openLCModal('${esc(uuid)}','${t.to}','${esc(t.label)}')">${esc(t.label)}</button>`
          ).join('')}
        </div></div>` : ''}
    </div>`;

    // Lifecycle history
    if (lifecycle && lifecycle.events && lifecycle.events.length) {
      html += `<div class="detail-section">
        <div class="detail-section-title">Lifecycle history</div>
        <div class="analysis-block"><div class="analysis-block-body" style="padding:4px 0">`;
      lifecycle.events.forEach(e => {
        html += `<div class="lc-history-row">
          ${e.from_state ? lcHtml(e.from_state) : '<span style="color:var(--text-faint);font-size:10px">created</span>'}
          <span class="lc-history-arrow">→</span>
          ${lcHtml(e.to_state)}
          <span class="lc-history-who">${esc(e.actor)}</span>
          ${e.notes ? `<span style="color:var(--text-dim);font-size:11px">${esc(e.notes)}</span>` : ''}
          <span class="lc-history-ts">${esc(fmtTs(e.created_at))}</span>
        </div>`;
      });
      html += '</div></div></div>';
    }

    // Set memberships — interactive chips (click chip = filter list; × = remove)
    const ucPathEsc = (data.path || '').replace(/'/g,"\\'");
    const ucHandleEsc = (parsed.handle || data.handle || '').replace(/'/g,"\\'");
    const chipsHtml = sets.length
      ? sets.map(s => `<span class="set-chip" style="cursor:pointer;display:inline-flex;align-items:center;gap:4px;" title="Click to filter list to this Scoping Set">
            <span onclick="selectSet(${s.id})">⊞ ${esc(s.name)}</span>
            <span style="cursor:pointer;color:var(--red);font-weight:600;padding:0 2px;" title="Remove from Set" onclick="event.stopPropagation();_removeUCFromSet(${s.id},'${esc(uuid)}','${esc(s.name)}')">×</span>
          </span>`).join('')
      : '<span style="color:var(--text-faint);font-size:11px">Not in any set yet</span>';
    html += `<div class="detail-section">
      <div class="detail-section-title">Scoping Sets</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;position:relative;">
        ${chipsHtml}
        <span class="set-chip" id="ucDetailAddSetBtn" style="cursor:pointer;background:var(--bg-input);border:1px dashed var(--border-bright);color:var(--text-faint);"
              onclick="_openAddSetPicker('${esc(uuid)}', '${esc(data.source||'managed')}', '${ucHandleEsc}', '${ucPathEsc}', this)">+ Add to Set</span>
        <div id="ucDetailAddSetPopover" style="display:none;position:absolute;top:100%;left:0;margin-top:6px;z-index:60;background:var(--bg-panel);border:1px solid var(--border-bright);border-radius:3px;box-shadow:0 4px 12px rgba(0,0,0,0.35);min-width:240px;max-height:280px;overflow-y:auto;"></div>
      </div>
      <div style="font-size:10px;color:var(--text-faint);margin-top:6px;">Tip: drag a UC from the list onto a Scoping Set in the left rail to add it there too.</div>
    </div>`;
  }

  if (scenario.description) {
    html += `<div class="detail-section">
      <div class="detail-section-title">Scenario</div>
      <div style="font-size:13px;font-family:var(--serif);line-height:1.7;color:var(--text)">${esc(scenario.description)}</div>
    </div>`;
  }

  const dims = scenario.dimensions || {};
  if (Object.keys(dims).length) {
    html += `<div class="detail-section"><div class="detail-section-title">Dimensions</div><div class="kv-grid">`;
    Object.entries(dims).forEach(([k,v]) => {
      html += `<div class="kv-label">${esc(k.replace(/_/g,' '))}</div><div class="kv-val">${esc(v)}</div>`;
    });
    html += '</div></div>';
  }

  const criteria = scenario.success_criteria || [];
  if (criteria.length) {
    html += `<div class="detail-section"><div class="detail-section-title">Success criteria</div><ul style="list-style:none;padding:0">`;
    criteria.forEach(c => {
      html += `<li style="font-size:12px;color:var(--text-dim);padding:3px 0;padding-left:14px;position:relative">
        <span style="position:absolute;left:0;color:var(--accent)">·</span>${esc(c)}</li>`;
    });
    html += '</ul></div>';
  }

  const di = scenario.expected_domain_interactions || [];
  if (di.length) {
    html += `<div class="detail-section"><div class="detail-section-title">Domain interactions</div><div class="analysis-block">`;
    di.forEach(d => {
      html += `<div class="finding-row">
        <div style="font-size:10px;color:var(--accent)">${esc(d.domain||'')}</div>
        <div style="font-size:12px;color:var(--text-dim)">${esc(d.interaction||'')}</div>
        <div></div></div>`;
    });
    html += '</div></div>';
  }

  if (scenario.actor) {
    const a = scenario.actor;
    html += `<div class="detail-section"><div class="detail-section-title">Actor</div><div class="kv-grid">
      ${a.persona ? `<div class="kv-label">persona</div><div class="kv-val">${esc(a.persona)}</div>` : ''}
      ${a.profile ? `<div class="kv-label">profile</div><div class="kv-val">${esc(a.profile)}</div>` : ''}
    </div></div>`;
  }

  const genBy = parsed.generated_by || {};
  if (data.created_by || genBy.source) {
    html += `<div class="detail-section"><div class="detail-section-title">Provenance</div><div class="kv-grid">
      ${data.created_by ? `<div class="kv-label">created by</div><div class="kv-val">${esc(data.created_by)}</div>` : ''}
      ${data.created_at ? `<div class="kv-label">created</div><div class="kv-val">${esc(fmtTs(data.created_at))}</div>` : ''}
      ${data.updated_by && data.updated_by!==data.created_by ? `<div class="kv-label">updated by</div><div class="kv-val">${esc(data.updated_by)}</div>` : ''}
      ${data.updated_at ? `<div class="kv-label">updated</div><div class="kv-val">${esc(fmtTs(data.updated_at))}</div>` : ''}
      ${genBy.source ? `<div class="kv-label">gen source</div><div class="kv-val">${esc(genBy.source)}</div>` : ''}
      ${genBy.mode   ? `<div class="kv-label">gen mode</div><div class="kv-val">${esc(genBy.mode)}</div>` : ''}
    </div></div>`;
  }

  // Customer demand placeholder — populated async by _loadUCDemand() (managed only).
  // Demand = distinct customers (multi-tenant importance); avoids one customer's
  // repeat asks poisoning priority. Foundation for compatibility-aware dedup-on-ingest.
  if (isManaged) {
    html += `<div class="detail-section" id="ucDemandSection">
      <div class="detail-section-title">Customer demand</div>
      <div id="ucDemandBody" class="analysis-block">
        <div class="analysis-block-body" style="padding:8px 12px;color:var(--text-faint);font-size:11px">loading…</div>
      </div>
    </div>`;
  }

  // Test history placeholder — populated async by loadUCTestHistory()
  html += `<div class="detail-section" id="ucTestHistorySection">
    <div class="detail-section-title">Test history</div>
    <div id="ucTestHistoryBody" class="analysis-block">
      <div class="analysis-block-body" style="padding:8px 12px;color:var(--text-faint);font-size:11px">loading…</div>
    </div>
  </div>`;

  if (data.yaml_content) {
    html += `<div class="detail-section">
      <div class="detail-section-title" style="cursor:pointer" onclick="toggleRaw(this)">
        Raw YAML <span style="color:var(--text-faint);font-size:9px">(click to toggle)</span>
      </div>
      <div class="raw-yaml-block" style="display:none">
        <div class="analysis-block"><div class="analysis-block-body">
          <pre style="white-space:pre-wrap;word-break:break-word;font-size:11px">${esc(data.yaml_content)}</pre>
        </div></div>
      </div></div>`;
  }

  html += '</div>';
  el.innerHTML = html;
  loadUCTestHistory(uuid);
  if (isManaged) _loadUCDemand(uuid);
}

// ── Customer demand (async; fills the placeholder after detail renders) ───────
// Log/view per-customer requests for a UC. Importance = distinct customers, so
// re-logging the same customer is fine (real repeat-demand signal) but doesn't
// inflate the multi-tenant count. Refreshes the list badge after a change.
async function _loadUCDemand(uuid) {
  const body = document.getElementById('ucDemandBody');
  if (!body) return;
  let d;
  try { d = await api(`/api/use-cases/${encodeURIComponent(uuid)}/customer-requests`); }
  catch (e) { body.innerHTML = `<div style="padding:8px 12px;color:var(--red);font-size:11px">${esc(e.message)}</div>`; return; }
  const reqs = d.requests || [];
  const mt = d.multi_tenant;
  let h = `<div style="padding:8px 12px;">
    <div style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px;">
      <span style="font-size:20px;font-weight:600;color:${mt ? 'var(--blue)' : 'var(--text)'};">${d.distinct_customers}</span>
      <span style="font-size:11px;color:var(--text-dim);">distinct customer${d.distinct_customers === 1 ? '' : 's'}${mt ? ' · multi-tenant 🏢' : ''}</span>
      <span style="font-size:11px;color:var(--text-faint);">${d.total_requests} total request${d.total_requests === 1 ? '' : 's'}</span>
    </div>`;
  if ((d.by_customer || []).length) {
    h += `<div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px;">`
      + d.by_customer.map(c => `<span class="tag" title="${c.count} request${c.count === 1 ? '' : 's'}">${esc(c.customer)}${c.count > 1 ? ` ×${c.count}` : ''}</span>`).join('')
      + `</div>`;
  }
  h += `<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
      <input id="ucDemandCustomer" placeholder="customer / tenant name" style="flex:1;min-width:160px;font-size:12px;" />
      <button class="btn ghost btn-sm" id="ucDemandLogBtn" type="button">+ Log request</button>
      <span id="ucDemandMsg" style="font-size:10px;color:var(--text-faint);"></span>
    </div>`;
  if (reqs.length) {
    h += `<div style="font-size:9px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:2px;">Request log</div>`
      + reqs.slice(0, 25).map(r => `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;border-bottom:1px solid var(--border);padding:3px 0;font-size:11px;">
          <span style="min-width:0;flex:1;"><strong>${esc(r.customer)}</strong>${r.note ? ` — <span style="color:var(--text-dim);">${esc(r.note)}</span>` : ''}</span>
          <span style="color:var(--text-faint);white-space:nowrap;">${esc(r.source || '')} · ${esc(fmtTs(r.requested_at))}</span>
          <button class="btn ghost btn-sm" title="Remove this request" data-rid="${r.id}" style="color:var(--red);padding:0 5px;">✕</button>
        </div>`).join('');
  }
  h += `</div>`;
  body.innerHTML = h;
  const logReq = async () => {
    const inp = document.getElementById('ucDemandCustomer');
    const msg = document.getElementById('ucDemandMsg');
    const customer = (inp.value || '').trim();
    if (!customer) { msg.style.color = 'var(--red)'; msg.textContent = 'enter a customer'; return; }
    try {
      await api(`/api/use-cases/${encodeURIComponent(uuid)}/customer-requests`,
        { method: 'POST', body: JSON.stringify({ customer, source: 'manual' }) });
      _loadUCDemand(uuid); loadUCs();   // refresh the list badge too
    } catch (e) { msg.style.color = 'var(--red)'; msg.textContent = e.message; }
  };
  document.getElementById('ucDemandLogBtn')?.addEventListener('click', logReq);
  document.getElementById('ucDemandCustomer')?.addEventListener('keydown', e => { if (e.key === 'Enter') logReq(); });
  body.querySelectorAll('button[data-rid]').forEach(b => b.addEventListener('click', async () => {
    try {
      await api(`/api/use-cases/${encodeURIComponent(uuid)}/customer-requests/${b.dataset.rid}`, { method: 'DELETE' });
      _loadUCDemand(uuid); loadUCs();
    } catch (e) { toast(e.message, true); }
  }));
}

// ── UC test history (async, fills the placeholder section after detail renders) ─

const _verdictColor = {
  supported:             'var(--green)',
  partially_supported:   'var(--accent)',
  not_supported:         'var(--red)',
  error:                 'var(--red)',
};

async function _openUCRunResult(runId, ucUuid) {
  // Switch to Results, select the run, then open the per-UC analysis.
  // selectRunResult is async and sets activeRunResultId before selectUCResult
  // tries to read it.
  switchView('results');
  await selectRunResult(runId);
  await selectUCResult(ucUuid);
}

async function loadUCTestHistory(uuid) {
  const body = document.getElementById('ucTestHistoryBody');
  if (!body) return;
  try {
    const resp = await api(`/api/use-cases/${encodeURIComponent(uuid)}/runs?limit=10`);
    const runs = resp.runs || [];
    if (!runs.length) {
      body.innerHTML = '<div class="analysis-block-body" style="padding:8px 12px;color:var(--text-faint);font-size:11px">No ingestions have processed this UC yet.</div>';
      return;
    }
    let h = '<div class="analysis-block-body" style="padding:4px 0">';
    runs.forEach(r => {
      const when = r.analyzed_at || r.ingested_at;
      const verdictColor = _verdictColor[r.verdict] || 'var(--text-faint)';
      const verdictTxt = r.verdict ? esc(r.verdict.replace(/_/g,' ')) : (r.status || '—');
      const wt = r.wall_time_seconds ? `${r.wall_time_seconds.toFixed(1)}s` : '';
      const gaps = r.gap_count ? `${r.gap_count} gap${r.gap_count!==1?'s':''}` : '';
      // Infrastructure confidence chip (Phase B of A+C+D+E follow-on).
      // Distinct from analytical verdict — answers "was the analysis run
      // under clean infrastructure conditions?" rather than "is the answer
      // right?". Tooltip carries the engine's explanation + recommendations.
      const ic = r.infrastructure_confidence;
      let icChip = '';
      if (ic && ic.label) {
        const colors = {
          high:        ['var(--green)','rgba(80,180,80,0.12)'],
          medium:      ['var(--accent)','var(--accent-bg)'],
          low:         ['#cf7416','rgba(207,116,22,0.15)'],
          compromised: ['var(--red)','rgba(217,101,58,0.18)'],
        };
        const [fg, bg] = colors[ic.label] || ['var(--text-faint)','transparent'];
        const tip = [
          `infrastructure confidence: ${ic.label} (${ic.score}/100)`,
          ic.explanation || '',
          (ic.signals && ic.signals.length) ? 'signals: ' + ic.signals.join(', ') : '',
          (ic.recommendations && ic.recommendations.length) ? 'recommendations:\n - ' + ic.recommendations.join('\n - ') : '',
        ].filter(Boolean).join('\n\n');
        icChip = `<span title="${esc(tip)}" style="font-size:9px;padding:1px 6px;background:${bg};color:${fg};border-radius:8px;text-transform:uppercase;letter-spacing:.04em;font-weight:600;cursor:help;">infra: ${esc(ic.label)}</span>`;
      }
      h += `<div style="display:flex;align-items:center;gap:10px;padding:5px 12px;font-size:11px;border-top:1px solid var(--border);cursor:pointer"
              onclick="_openUCRunResult('${esc(r.run_id)}','${esc(uuid)}')"
              title="Open in Results tab">
        <span style="color:${verdictColor};font-weight:500;min-width:120px;">${verdictTxt}</span>
        ${icChip}
        <span style="font-family:var(--mono,monospace);font-size:10px;color:var(--text-dim);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.run_id)}</span>
        <span style="color:var(--text-faint);font-size:10px;min-width:60px;text-align:right">${esc(wt)}</span>
        <span style="color:var(--text-faint);font-size:10px;min-width:60px;text-align:right">${esc(gaps)}</span>
        <span style="color:var(--text-faint);font-size:10px;min-width:140px;text-align:right">${esc(fmtTs(when))}</span>
      </div>`;
    });
    h += '</div>';
    body.innerHTML = h;
  } catch (e) {
    body.innerHTML = `<div class="analysis-block-body" style="padding:8px 12px;color:var(--red);font-size:11px">Failed to load: ${esc(e.message)}</div>`;
  }
}

// ── Test-run-from-UC: open New Ingestion scoped to the narrowest dir containing this UC ─

function _narrowestSubpath(ucPaths) {
  const cleaned = ucPaths.filter(Boolean);
  if (!cleaned.length) return '';
  // For a single UC, the subpath is the file's parent dir.
  // For multiple, fall back to the common ancestor.
  const dirs = cleaned.map(p => {
    const i = p.lastIndexOf('/');
    return i >= 0 ? p.slice(0, i) : '';
  });
  let common = dirs[0];
  for (let i = 1; i < dirs.length; i++) {
    while (common && dirs[i].indexOf(common) !== 0) {
      const j = common.lastIndexOf('/');
      common = j > 0 ? common.slice(0, j) : '';
    }
  }
  return common;
}

async function testRunUC(uuid, ucPath, title, branchOverride) {
  // Three paths converge here:
  //   • Corpus UC: ucPath is the corpus file path → engine filters within
  //     that directory.
  //   • Managed UC, never pushed: ucPath empty → engine fetches the YAML
  //     from the console API via the managed_uc_uuids materialize path.
  //   • Managed UC pushed: ucPath = corpus_synced_path + branchOverride =
  //     the PR branch → engine clones at that branch and filters to the UC.
  const uc = (allUCs || []).find(u => u.uuid === uuid);
  const isManagedDirect = !ucPath && uc && uc.source === 'managed';

  let subpath = '';
  let filter = null;
  if (isManagedDirect) {
    // Managed direct: no corpus filter, no subpath — engine fetches the UC
    // from the console API via managed_uc_uuids.
    filter = {handles: [], uuids: [], managed: [uuid]};
  } else {
    subpath = _narrowestSubpath([ucPath]);
    filter = (uc && uc.handle)
      ? {handles: [uc.handle], uuids: [], managed: []}
      : {handles: [], uuids: [uuid], managed: []};
  }
  // Run THIS UC directly — submit immediately with project defaults and jump to
  // the run. No New-Run config page. Null model/repo are resolved server-side
  // from the project's configured defaults (see /api/runs trigger_run).
  const payload = {
    mode: 'verification',
    corpus_subpath:   subpath || null,
    corpus_repo_branch: branchOverride || null,
    halt_on_error:    false,
    name:             `test: ${title}`.slice(0, 120),
    category:         'ad-hoc',
    selection_mode:   'individual',
    uc_handles:       filter.handles?.length ? filter.handles : null,
    uc_uuids:         filter.uuids?.length   ? filter.uuids   : null,
    managed_uc_uuids: filter.managed?.length ? filter.managed : null,
  };
  toast(`Running ${title}…`);
  try {
    const resp = await api('/api/runs', { method: 'POST', body: JSON.stringify(payload) });
    const name = resp.run?.name || '?';
    toast(`Ingestion triggered: ${name}`);
    switchView('runs');
    try { await loadRuns(); } catch (_) {}
    if (name && name !== '?') selectRun(name);
  } catch (e) {
    toast('Ingestion failed: ' + e.message, true);
  }
}

// Push a managed UC to the corpus repo, then immediately open a Test
// evaluation scoped to the resulting PR branch. Unblocks "test before merge"
// for managed UCs without requiring two round-trips.
async function pushAndTestUC(uuid) {
  if (!confirm('Push this UC to the corpus repo and then test it on the PR branch?')) return;
  toast('Pushing…');
  let pushResult;
  try {
    pushResult = await api(`/api/use-cases/${encodeURIComponent(uuid)}/push-to-corpus`, {
      method: 'POST',
      body: JSON.stringify({override: true}),  // bypass lifecycle gate for the test loop
    });
  } catch (e) {
    toast('Push failed: ' + e.message, true);
    return;
  }
  toast(`Push ${pushResult.action} — branch ${pushResult.branch}. Running this UC…`);
  // Re-fetch the UC to get the canonical corpus_synced_path / corpus_branch
  // (push response already has these but reloading the UC re-renders the detail).
  let data;
  try { data = await api(`/api/use-cases/${encodeURIComponent(uuid)}`); }
  catch (e) { toast('Reload failed: ' + e.message, true); return; }
  const titleText = data.parsed?.title || data.title || data.parsed?.handle || uuid;
  // testRunUC now submits directly and navigates to the run — don't re-select
  // the UC afterward (that would yank the view back). The PR badge refreshes
  // next time the UC is opened.
  await testRunUC(uuid, pushResult.path, titleText, pushResult.branch);
}

function toggleRaw(el) {
  const b = el.nextElementSibling; if (b) b.style.display = b.style.display==='none' ? '' : 'none';
}

// ── Lifecycle transition modal ──────────────────────────────
async function openLCModal(uuid, toState, label) {
  lcPendingUCId = uuid; lcPendingTo = toState;
  document.getElementById('lcModalTitle').textContent = label;
  document.getElementById('lcModalDesc').textContent =
    `Move "${uuid}" to "${toState.replace(/_/g,' ')}"`;
  document.getElementById('lcModalNotes').value = '';
  document.getElementById('lcModalStatus').textContent = '';
  document.getElementById('confirmLCModal').disabled = false;
  // Reset gate UI
  const gate = document.getElementById('lcApprovalGate');
  const overrideRow = document.getElementById('lcOverrideRow');
  const overrideBox = document.getElementById('lcOverrideCheckbox');
  const notesLabel  = document.getElementById('lcModalNotesLabel');
  gate.style.display = 'none';
  overrideRow.style.display = 'none';
  overrideBox.checked = false;
  notesLabel.textContent = 'Notes (optional)';
  document.getElementById('lcModal').classList.add('open');
  // For 'approved' transitions, surface the passing-run status before the
  // user commits, so they understand whether they need an override.
  if (toState === 'approved') {
    gate.style.display = 'block';
    gate.style.background = 'var(--bg-input)';
    gate.style.border = '1px solid var(--border)';
    gate.innerHTML = '<span style="color:var(--text-faint);">Checking for passing ingestions…</span>';
    try {
      const resp = await api(`/api/use-cases/${encodeURIComponent(uuid)}/runs?limit=50`);
      const passing = (resp.runs || []).filter(r =>
        r.status === 'success' && (r.verdict === 'supported' || r.verdict === 'partially_supported')
      );
      if (passing.length) {
        gate.style.background = 'rgba(146,194,92,0.10)';
        gate.style.border = '1px solid var(--green)';
        gate.innerHTML = `<strong style="color:var(--green);">✓ ${passing.length} passing ingestion${passing.length>1?'s':''} on file</strong> — approval is unblocked.`;
      } else {
        gate.style.background = 'rgba(224,122,79,0.10)';
        gate.style.border = '1px solid var(--red)';
        gate.innerHTML = `<strong style="color:var(--red);">⚠ No passing ingestions on file.</strong> Approval is gated. Either run a test evaluation first (push to corpus → run a Scoping Set containing this UC), or tick the override below and explain why.`;
        overrideRow.style.display = '';
        notesLabel.textContent = 'Notes (REQUIRED when overriding)';
      }
    } catch (e) {
      gate.innerHTML = `<span style="color:var(--red);">Could not check test history: ${esc(e.message)}</span>`;
    }
  }
}
function closeLCModal() { document.getElementById('lcModal').classList.remove('open'); }

async function confirmLCTransition() {
  if (!lcPendingUCId || !lcPendingTo) return;
  const btn = document.getElementById('confirmLCModal'), status = document.getElementById('lcModalStatus');
  btn.disabled = true; status.textContent = 'saving…';
  const notes = document.getElementById('lcModalNotes').value.trim();
  const override = document.getElementById('lcOverrideCheckbox').checked;
  try {
    await api(`/api/use-cases/${encodeURIComponent(lcPendingUCId)}/transition`, {
      method:'POST', body:JSON.stringify({to_state:lcPendingTo, notes, override})
    });
    toast(`UC moved to ${lcPendingTo.replace(/_/g,' ')}`);
    closeLCModal();
    // Refresh the UC in-place
    const data = await api(`/api/use-cases/${encodeURIComponent(lcPendingUCId)}`);
    let lifecycle = null;
    try { lifecycle = await api(`/api/use-cases/${encodeURIComponent(lcPendingUCId)}/lifecycle`); } catch {}
    renderUCDetail(data, lifecycle);
    // Update list badge
    const uc = allUCs.find(u => u.uuid === lcPendingUCId);
    if (uc) { uc.lifecycle_state = lcPendingTo; renderUCList(); }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    btn.disabled = false;
  }
}

// ── UC CRUD ──────────────────────────────────────────────────

// Top-level `title:` extraction / injection. Regex-based to avoid a
// YAML-parsing dependency in the browser. Matches only at column 0 so
// nested `title:` keys inside block scalars or sub-mappings are left alone.
function _extractTitleFromYaml(yaml) {
  const m = /^title:[ \t]*(.+?)[ \t]*$/m.exec(yaml || '');
  if (!m) return '';
  let v = m[1];
  // strip inline comment
  const hash = v.indexOf(' #');
  if (hash !== -1) v = v.slice(0, hash).trim();
  // strip wrapping quotes (single or double)
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    v = v.slice(1, -1);
  }
  return v;
}
function _injectTitleIntoYaml(yaml, title) {
  const safe = title ? JSON.stringify(title) : '""';  // double-quoted, escapes embedded quotes/newlines
  const line = `title: ${safe}`;
  const re = /^title:[ \t]*.*$/m;
  if (re.test(yaml)) return yaml.replace(re, line);
  // No existing title — prepend after any leading comment block
  const lines = (yaml || '').split('\n');
  let i = 0;
  while (i < lines.length && (lines[i].trimStart().startsWith('#') || lines[i].trim() === '')) i++;
  lines.splice(i, 0, line);
  return lines.join('\n');
}

function openUCModal(uuid, yamlContent, tags) {
  editingUCId = uuid || null;
  document.getElementById('ucModalTitle').textContent = uuid ? 'Edit use case' : 'New use case';
  let content = yamlContent;
  if (!content) {
    // Engine requires UC uuid to start with `uc-`. The template carries the
    // `uc-` prefix as static text; only the placeholder body is replaced so
    // we don't risk dropping or doubling it.
    const rawUuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2, 10);
    content = UC_TEMPLATE.replace('<your-uuid-here>', rawUuid);
  }
  document.getElementById('ucYamlEditor').value = content;
  document.getElementById('ucNameInput').value = _extractTitleFromYaml(content);
  document.getElementById('ucTagsInput').value = (tags||[]).join(', ');
  document.getElementById('ucModalStatus').textContent = '';
  document.getElementById('saveUCModal').disabled = false;
  document.getElementById('ucModal').classList.add('open');
  // Focus the Name field on new UC; on edit, leave focus on the modal
  if (!uuid) setTimeout(() => document.getElementById('ucNameInput').focus(), 50);
}
async function editUC(uuid) {
  try { const d = await api(`/api/use-cases/${encodeURIComponent(uuid)}`); openUCModal(uuid, d.yaml_content, d.tags); }
  catch (e) { toast('Load failed: ' + e.message, true); }
}
async function cloneUC(uuid) {
  try { const d = await api(`/api/use-cases/${encodeURIComponent(uuid)}`); openUCModal(null, d.yaml_content, d.tags); }
  catch (e) { toast('Load failed: ' + e.message, true); }
}
async function deleteUC(uuid, btn) {
  if (!btn) return;
  _armDeleteBtn(btn, async () => {
    const choice = await _confirmDeleteImpact('uc', uuid, uuid);
    if (!choice) return;
    try {
      await api(`/api/use-cases/${encodeURIComponent(uuid)}?purge_analyses=${choice.purge ? 'true' : 'false'}`, {method:'DELETE'});
      toast(`Deleted ${uuid}${choice.purge ? ' + analyses purged' : ''}`); activeUCId = null;
      document.getElementById('ucDetail').innerHTML = '<div class="detail-pane"><div class="detail-empty">Deleted.</div></div>';
      loadUCs();
    } catch (e) { toast('Delete failed: ' + e.message, true); }
  });
}
function closeUCModal() {
  document.getElementById('ucModal').classList.remove('open');
  editingUCId = null;
  // Close assist panel too so next open starts fresh
  _ucAssistClose();
}

// ══════════════════════════ EXPORT / IMPORT / PROMOTE ══════════════════════════

function exportUCs(format) {
  document.getElementById('exportUCMenu').style.display = 'none';
  const url = `/api/export?format=${encodeURIComponent(format)}`;
  _triggerDownload(url);
}

function exportSet(setId, format) {
  const menuId = `exportSetMenu-${setId}`;
  const m = document.getElementById(menuId); if (m) m.style.display = 'none';
  const url = `/api/export?format=${encodeURIComponent(format)}&set_id=${setId}`;
  _triggerDownload(url);
}

function _triggerDownload(url) {
  const a = document.createElement('a');
  a.href = url; a.download = '';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function toggleExportSetMenu(setId, event) {
  event.stopPropagation();
  const menuId = `exportSetMenu-${setId}`;
  const m = document.getElementById(menuId);
  if (!m) return;
  const visible = m.style.display !== 'none';
  // Close any other open menus
  document.querySelectorAll('[id^="exportSetMenu-"],[id="exportUCMenu"]').forEach(el => el.style.display = 'none');
  if (!visible) m.style.display = '';
}

// Close dropdowns on outside click
document.addEventListener('click', () => {
  document.querySelectorAll('[id^="exportSetMenu-"],[id="exportUCMenu"]').forEach(el => el.style.display = 'none');
});

function openImportModal() {
  document.getElementById('importFile').value = '';
  document.getElementById('importResult').style.display = 'none';
  document.getElementById('importResult').innerHTML = '';
  document.getElementById('submitImportBtn').disabled = false;
  document.getElementById('importModal').classList.add('open');
}
function closeImportModal() { document.getElementById('importModal').classList.remove('open'); }

async function submitImport() {
  const fileInput = document.getElementById('importFile');
  if (!fileInput.files.length) { toast('No file selected', true); return; }
  const btn = document.getElementById('submitImportBtn');
  btn.disabled = true;
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  try {
    const res = await fetch('/api/import', {method: 'POST', body: formData});
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`${res.status}: ${err}`);
    }
    const result = await res.json();
    const resultEl = document.getElementById('importResult');
    resultEl.style.display = '';
    const hasErrors = result.errors && result.errors.length;
    resultEl.innerHTML = `<div style="color:var(--green);margin-bottom:${hasErrors?'8px':'0'}">
      ✓ Created: ${result.created} · Updated: ${result.updated} · Transitioned: ${result.transitioned}${result.skipped ? ` · Skipped: ${result.skipped}` : ''}
    </div>${hasErrors ? `<div style="color:var(--red)">${result.errors.map(e => `<div>${esc(e)}</div>`).join('')}</div>` : ''}`;
    toast(`Import done: ${result.created} new, ${result.updated} updated`);
    await loadUCs(); await loadSets();
  } catch (e) {
    toast('Import failed: ' + e.message, true);
  } finally { btn.disabled = false; }
}

// ── Promote set modal ─────────────────────────────────────────
function openPromoteModal(setId, setName) {
  promoteSetId = setId;
  document.getElementById('promoteModalSetName').textContent = setName;
  document.getElementById('promoteNotes').value = '';
  document.getElementById('promoteStatus').textContent = '';
  document.getElementById('confirmPromoteModal').disabled = false;
  document.getElementById('promoteModal').classList.add('open');
}
function closePromoteModal() { document.getElementById('promoteModal').classList.remove('open'); promoteSetId = null; }

async function confirmPromote() {
  if (!promoteSetId) return;
  const fromState = document.getElementById('promoteFromState').value;
  const toState   = document.getElementById('promoteToState').value;
  const notes     = document.getElementById('promoteNotes').value.trim();
  const btn = document.getElementById('confirmPromoteModal'), status = document.getElementById('promoteStatus');
  btn.disabled = true; status.textContent = 'promoting…';
  try {
    const resp = await api(`/api/sets/${promoteSetId}/promote`, {
      method: 'POST',
      body: JSON.stringify({from_state: fromState, to_state: toState, notes}),
    });
    toast(`Promoted ${resp.promoted} UC${resp.promoted!==1?'s':''}: ${fromState} → ${toState}`);
    closePromoteModal();
    await loadUCs();
    await loadSets();
    _refreshSetMgmt();
  } catch (e) {
    status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; btn.disabled = false;
  }
}

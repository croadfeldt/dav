// ══════════════ REVIEW & PLAN TAB ════════════════════════════

const _rpRevRaw = { text: '' };
const _rpEnhRaw = { text: '' };

async function loadReviewTab() {
  // Populate run dropdown from allResults; prefer the human-readable session name
  // (falls back to the workspace run_id if no session is correlated).
  const sel = document.getElementById('rpRunSel');
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = '<option value="">— select an analysis —</option>';
  allResults.forEach(r => {
    const o = document.createElement('option');
    o.value = r.run_id;
    const label = r.session_name
      ? `${r.session_name} (${r.run_id.slice(0, 16)}…)`
      : r.run_id;
    o.textContent = label;
    o.title = r.run_id;
    sel.appendChild(o);
  });
  if (prev) sel.value = prev;
  // Arch-review model comes from the Config default — just show which one.
  _updateArchModelInfo();
  _loadStageContext();
  _populateGlobalRunSel();
  // Roadmap scope = the masthead Scoping Set; reflect its name + load any cached output.
  _rpUpdateScopeName();
  _rpLoadCached();
  // If results not yet loaded, load them
  if (!allResults.length) {
    try {
      const resp = await api('/api/results');
      allResults = resp.results || [];
      loadReviewTab(); // re-run
    } catch(e) {}
  }
}

// Per-stage LLM context (DCM), saved to the project and injected into the
// arch-review + enhancement prompts. Stage key 'arch_review' (Track-1 design stage).
async function _loadStageContext() {
  const el = document.getElementById('rpStageCtx');
  if (!el) return;
  try { const r = await api('/api/stage-context/arch_review'); el.value = r.content || ''; }
  catch(e) {}
}
document.getElementById('rpStageCtxSave')?.addEventListener('click', async () => {
  const el = document.getElementById('rpStageCtx');
  const msg = document.getElementById('rpStageCtxMsg');
  if (!el) return;
  try {
    await api('/api/stage-context/arch_review', { method:'PUT', body: JSON.stringify({ content: el.value }) });
    if (msg) { msg.textContent = 'saved to project'; msg.style.color = 'var(--green)'; setTimeout(()=>{ if(msg) msg.textContent=''; }, 2500); }
  } catch(e) {
    if (msg) { msg.textContent = 'error: ' + e.message; msg.style.color = 'var(--red)'; }
  }
});

async function _rpPopulateUCs(runId) {
  const sel = document.getElementById('rpUCSel');
  if (!sel) return;
  if (!runId) { sel.innerHTML = '<option value="">— select an analysis first —</option>'; sel.disabled = true; return; }
  const summary = activeRunSummary?.run_id === runId ? activeRunSummary : null;
  if (summary) {
    sel.innerHTML = '<option value="">— full analysis —</option>';
    (summary.ucs||[]).forEach(u => {
      const o = document.createElement('option');
      o.value = u.uc_uuid; o.textContent = u.uc_handle || u.uc_uuid;
      sel.appendChild(o);
    });
    sel.disabled = false;
    return;
  }
  try {
    const s = await api(`/api/results/${encodeURIComponent(runId)}`);
    activeRunSummary = s;
    sel.innerHTML = '<option value="">— full analysis —</option>';
    (s.ucs||[]).forEach(u => {
      const o = document.createElement('option');
      o.value = u.uc_uuid; o.textContent = u.uc_handle || u.uc_uuid;
      sel.appendChild(o);
    });
    sel.disabled = false;
  } catch(e) { sel.innerHTML = `<option value="">(${esc(e.message)})</option>`; }
}

function _rpGetContext() {
  // The Architecture roadmap is scoped by the masthead Scoping Set (the run/UC picker is
  // retired). `setId` is the masthead scope ('' = all, '__unassigned__', or a set id);
  // `runId` is a synthetic `set:<id>` token so the cache/generation API keys it like a run.
  const setId  = _activeScope || '';
  const scope  = 'set';
  const runId  = 'set:' + (setId || '__all__');
  // Model is NOT chosen here — arch-review uses the Config "Default Arch Review
  // model" (model_defaults key='arch-review'); the API falls back to it when the
  // request carries no model. Single source of truth, set in the Config view.
  return { runId, scope, ucUuid: null, setId };
}
// Reflect the active masthead scope name in the Architecture controls panel.
function _rpUpdateScopeName() {
  const el = document.getElementById('rpScopeName');
  if (!el) return;
  const v = _activeScope || '';
  let name = 'all use cases';
  if (v === '__unassigned__') name = 'unassigned use cases';
  else if (v) { const s = (allSets || []).find(x => String(x.id) === String(v)); name = s ? s.name : `Set ${v}`; }
  el.textContent = name;
}

document.getElementById('rpRunSel')?.addEventListener('change', function() {
  activeRunResultId = this.value;
  _rpPopulateUCs(this.value);
  _rpLoadCached();
});

document.getElementById('rpScopeRun')?.addEventListener('change', () => {
  const sel = document.getElementById('rpUCSel');
  if (sel) sel.disabled = true;
  _rpLoadCached();
});
document.getElementById('rpScopeUC')?.addEventListener('change', () => {
  const runId = activeRunResultId;   // single system-wide current run
  const sel = document.getElementById('rpUCSel');
  if (sel) { sel.disabled = false; _rpPopulateUCs(runId); }
  _rpLoadCached();
});
document.getElementById('rpUCSel')?.addEventListener('change', _rpLoadCached);

// ── Cached Review / Enhancement output (Phase B) ─────────────────────────────
// On run/scope/UC change, show the stored generation (if any) instead of a
// blank pane, with a chip noting when + which model produced it, and whether
// it's stale (the run was re-ingested since). The Run buttons regenerate and
// the API re-caches; a cache present prompts a replace-confirm.
const _RP_CACHE = {
  review:      { streamEl:'rpRevStream', raw:()=>_rpRevRaw, mode:'markdown',    copy:'rpRevCopyBtn', reason:'rpRevReasoningBtn', chip:'rpRevCacheChip', reasonKey:'rpRevShowReasoning' },
  enhancement: { streamEl:'rpEnhStream', raw:()=>_rpEnhRaw, mode:'enhancement', copy:'rpEnhCopyBtn', reason:'rpEnhReasoningBtn', chip:'rpEnhCacheChip', reasonKey:'rpEnhShowReasoning' },
};
// Real "is there a cached result?" per kind — the Run replace-confirm keys on
// THIS, not chip text (the empty-state chip text is non-empty but means no cache).
const _rpCached = { review:false, enhancement:false };
function _rpCacheChip(kind, data){
  const chip = document.getElementById(_RP_CACHE[kind].chip);
  if (!chip) return;
  if (!data || !data.cached) { chip.textContent = ''; return; }
  const when  = data.created_at ? new Date(data.created_at).toLocaleString() : '';
  const model = data.model_label ? ` · ${esc(data.model_label)}` : '';
  if (data.stale) {
    chip.innerHTML = `⚠ cached ${esc(when)}${model} — run re-ingested; re-run to refresh`;
    chip.style.color = 'var(--red)';
  } else {
    chip.innerHTML = `↻ cached ${esc(when)}${model}`;
    chip.style.color = 'var(--text-faint)';
  }
}
async function _rpLoadCached(){
  const { runId, scope, ucUuid } = _rpGetContext();
  for (const kind of ['review','enhancement']) {
    const cfg = _RP_CACHE[kind];
    const el  = document.getElementById(cfg.streamEl);
    const chip = document.getElementById(cfg.chip);
    if (!el) continue;
    let data = null;
    if (runId && !(scope === 'uc' && !ucUuid)) {
      const qs = `run_id=${encodeURIComponent(runId)}&kind=${kind}&scope=${scope}`
               + (scope === 'uc' && ucUuid ? `&uc_uuid=${encodeURIComponent(ucUuid)}` : '');
      try { data = await api(`/api/analysis/output?${qs}`); } catch(e){ data = null; }
    }
    _rpCached[kind] = !!(data && data.cached);
    if (data && data.cached) {
      const holder = cfg.raw(); holder.text = data.content || '';
      el.dataset.renderMode = cfg.mode;
      el.style.display = '';                 // reveal BEFORE rendering — a render
      const cp = document.getElementById(cfg.copy);   if (cp) cp.style.display = '';   // throw can never leave it hidden
      const rb = document.getElementById(cfg.reason); if (rb) rb.style.display = '';
      try {
        const showReason = localStorage.getItem(cfg.reasonKey) === '1';
        _applyStreamRender(el, showReason ? holder.text : _stripThink(holder.text), cfg.mode);
      } catch(e){ el.textContent = holder.text; }
      _rpCacheChip(kind, data);
    } else {
      const holder = cfg.raw(); holder.text = '';
      el.style.display = 'none'; el.innerHTML = '';
      const cp = document.getElementById(cfg.copy); if (cp) cp.style.display = 'none';
      if (chip) {
        const haveCtx = runId && !(scope === 'uc' && !ucUuid);
        chip.textContent = haveCtx ? 'not generated for this analysis yet' : '';
        chip.style.color = 'var(--text-faint)';
      }
    }
    // The "Create PR →" row was only revealed by a fresh generation, so revisiting a CACHED
    // enhancement plan lost the ability to issue a PR. Reveal it whenever the plan is present.
    if (kind === 'enhancement') {
      const nr = document.getElementById('rpEnhNextRow');
      if (nr) nr.style.display = (data && data.cached) ? '' : 'none';
    }
  }
}
// Update ONLY the cache chip for one kind — never touches the output pane, so it
// is safe to call right after a generation (the pane already holds the fresh
// output; this just stamps "cached <when>" once the write has committed).
async function _rpRefreshChip(kind){
  const { runId, scope, ucUuid } = _rpGetContext();
  if (!runId || (scope === 'uc' && !ucUuid)) return;
  const qs = `run_id=${encodeURIComponent(runId)}&kind=${kind}&scope=${scope}`
           + (scope === 'uc' && ucUuid ? `&uc_uuid=${encodeURIComponent(ucUuid)}` : '');
  try { const d = await api(`/api/analysis/output?${qs}`); _rpCached[kind] = !!(d && d.cached); _rpCacheChip(kind, d); } catch(e){}
}

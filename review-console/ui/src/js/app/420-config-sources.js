// ══════════════════════════ CONFIG / SOURCES ══════════════════════════

const sourcesPollers = {spec:null,corpus:null};
const sourcesState   = {spec:null,corpus:null,inference:null};

const SOURCE_UI = {
  spec:   {repo:'srcSpecRepo',branch:'srcSpecBranch',when:'srcSpecWhen',who:'srcSpecWho',status:'srcSpecStatus',
           repoInput:'srcSpecRepoInput',branchSelect:'srcSpecBranchSelect',branchInput:'srcSpecBranchInput',
           refreshBranchesBtn:'srcSpecRefreshBranches',applyBtn:'srcSpecApplyBtn',msg:'srcSpecMsg'},
  corpus: {repo:'srcCorpusRepo',branch:'srcCorpusBranch',when:'srcCorpusWhen',who:'srcCorpusWho',status:'srcCorpusStatus',
           repoInput:'srcCorpusRepoInput',branchSelect:'srcCorpusBranchSelect',branchInput:'srcCorpusBranchInput',
           refreshBranchesBtn:'srcCorpusRefreshBranches',applyBtn:'srcCorpusApplyBtn',msg:'srcCorpusMsg'},
};

async function loadConfig() {
  // Load review models, MCP, code repos, UC assist, repos registry, and setup config nav
  loadAccessPanels();          // Users & Access (admin-only; no-op otherwise)
  await loadCredentials();     // Shared credentials (M9) — load first so the
                               // Repos form's PAT/webhook-secret dropdowns can
                               // hydrate from the freshest list
  await loadRepos();           // Managed repos registry (M3) — load before sources panels
                               // since sources are now projections over the registry
  await loadReviewModels();
  renderModelList();
  _updateArchModelInfo();
  _populateOverrideSel('nrModelSel', '__engine__');
  await loadUCAssistConfig();  // populates ucAssistModelSel from _reviewModels
  await loadEvalDefault();    // populates evalDefaultModelSel from DB
  await loadArchDefault();    // populates archDefaultModelSel from DB
  await loadMCPServers();
  await loadCodeRepos();
  await loadMCPRefreshStatus();   // populates configMCPRefreshPanel
  loadCorpusCacheStatus();        // corpus-files cache freshness
  setupConfigNav();
  try {
    const resp = await api('/api/sources');
    const data = resp.sources || {};
    for (const kind of ['spec','corpus']) { sourcesState[kind] = data[kind]||null; renderSourcePanel(kind); }
  } catch (e) {
    for (const kind of ['spec','corpus'])
      document.getElementById(SOURCE_UI[kind].msg).innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  }
  // Surface detected UC subpath on the corpus panel
  try {
    const detect = await api('/api/sources/corpus/uc-subpath');
    const el = document.getElementById('srcCorpusSubpath');
    if (detect.detected) el.textContent = detect.detected + '/';
    else if (detect.corpus_dir_exists === false) {
      el.innerHTML = '<span style="color:var(--red)">corpus not cloned yet</span>';
    } else {
      el.innerHTML = '<span style="color:var(--text-faint)">no dav/ or use-cases/ found</span>';
    }
  } catch {}
}


function renderSourcePanel(kind) {
  const s = sourcesState[kind]; if (!s) return;
  // Spec (M4) and corpus (M11) both read-only views projected from the
  // managed_repos registry.
  if (kind === 'spec')   return _renderSpecPanelReadOnly(s);
  if (kind === 'corpus') return _renderCorpusPanelReadOnly(s);

  const ui = SOURCE_UI[kind];
  document.getElementById(ui.repo).textContent   = s.repo_url    || '—';
  document.getElementById(ui.branch).textContent = s.repo_branch || '—';
  document.getElementById(ui.when).textContent   = fmtTs(s.last_applied_at);
  document.getElementById(ui.who).textContent    = s.last_applied_by || '—';
  const statusEl = document.getElementById(ui.status);
  if (s.rollout) {
    const r = s.rollout;
    statusEl.innerHTML = r.rolled_out
      ? `<span style="color:var(--green)">rolled out</span> · ${r.replicas_ready}/${r.replicas_desired} ready`
      : `<span style="color:var(--blue)">applying</span> · ${r.replicas_ready}/${r.replicas_desired} ready`;
  } else statusEl.textContent = '(deployment not found)';
  if (s.deployment_annotations) {
    const da = s.deployment_annotations;
    if ((da.source_repo_url && da.source_repo_url!==s.repo_url) || (da.source_repo_branch && da.source_repo_branch!==s.repo_branch))
      statusEl.innerHTML += ' <span style="color:var(--red)" title="Annotations lag ConfigMap">⚠ drift</span>';
  }
  const repoInput = document.getElementById(ui.repoInput);
  if (!repoInput.value && !repoInput.dataset.userEdited) {
    repoInput.value = s.repo_url || '';
    loadBranches(kind, s.repo_url).then(() => {
      const sel = document.getElementById(ui.branchSelect);
      for (const opt of sel.options) { if (opt.value===s.repo_branch) { sel.value=s.repo_branch; break; } }
    });
  }
}

// Spec is multi-source and managed via the registry (ADR-003). The panel
// renders a read-only summary; the source list comes from sourcesState.spec
// which the API populates with the parsed `sources` field of the ConfigMap.
function _renderSpecPanelReadOnly(s) {
  const listEl = document.getElementById('srcSpecSourceList');
  if (s.multi_source && Array.isArray(s.sources) && s.sources.length) {
    listEl.innerHTML = s.sources.map(src => `
      <div style="display:flex;gap:6px;align-items:baseline;font-size:11px;margin-bottom:3px;">
        <span style="font-weight:600;min-width:60px;">${esc(src.namespace || '')}</span>
        <span style="font-family:var(--mono,monospace);color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">${esc(src.repo_url || '')}</span>
        <span style="color:var(--accent);font-family:var(--mono,monospace);">${esc(src.repo_branch || '')}</span>
        ${src.root_path ? `<span style="color:var(--text-faint);font-family:var(--mono,monospace);font-size:10px;">/${esc(src.root_path)}</span>` : ''}
      </div>`).join('');
  } else if (s.repo_url) {
    // Legacy single-source ConfigMap shape. Should be rare after the M2
    // projection has run, but render it so operators on older configs
    // still see meaningful state.
    listEl.innerHTML = `
      <div style="display:flex;gap:6px;align-items:baseline;font-size:11px;">
        <span style="color:var(--text-faint);font-style:italic;">legacy single-source:</span>
        <span style="font-family:var(--mono,monospace);color:var(--text-dim);">${esc(s.repo_url)}</span>
        <span style="color:var(--accent);font-family:var(--mono,monospace);">${esc(s.repo_branch || '')}</span>
      </div>
      <div style="font-size:10px;color:var(--accent);margin-top:4px;">
        ConfigMap is in legacy shape — run <strong>↻ Project</strong> on the Managed repos panel to convert to multi-source.
      </div>`;
  } else {
    listEl.innerHTML = '<span style="color:var(--text-faint);font-style:italic;">no spec sources configured</span>';
  }
  document.getElementById('srcSpecWhen').textContent = fmtTs(s.last_applied_at);
  document.getElementById('srcSpecWho').textContent  = s.last_applied_by || '—';
  const statusEl = document.getElementById('srcSpecStatus');
  if (s.rollout) {
    const r = s.rollout;
    statusEl.innerHTML = r.rolled_out
      ? `<span style="color:var(--green)">rolled out</span> · ${r.replicas_ready}/${r.replicas_desired} ready`
      : `<span style="color:var(--blue)">applying</span> · ${r.replicas_ready}/${r.replicas_desired} ready`;
  } else {
    statusEl.textContent = '(deployment not found)';
  }
}

// Corpus is multi-source post-M11 (ADR-007), projected from managed_repos
// rows with role=corpus. Same shape as the spec read-only renderer; the
// only differences are no Deployment-rollout (Tekton reads ConfigMap
// fresh per run) and the namespace-keyed UC subpath display.
function _renderCorpusPanelReadOnly(s) {
  const listEl = document.getElementById('srcCorpusSourceList');
  if (s.multi_source && Array.isArray(s.sources) && s.sources.length) {
    listEl.innerHTML = s.sources.map(src => `
      <div style="display:flex;gap:6px;align-items:baseline;font-size:11px;margin-bottom:3px;">
        <span style="font-weight:600;min-width:60px;">${esc(src.namespace || '')}</span>
        <span style="font-family:var(--mono,monospace);color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">${esc(src.repo_url || '')}</span>
        <span style="color:var(--accent);font-family:var(--mono,monospace);">${esc(src.repo_branch || '')}</span>
        ${src.root_path ? `<span style="color:var(--text-faint);font-family:var(--mono,monospace);font-size:10px;">/${esc(src.root_path)}</span>` : ''}
      </div>`).join('');
  } else if (s.repo_url) {
    listEl.innerHTML = `
      <div style="display:flex;gap:6px;align-items:baseline;font-size:11px;">
        <span style="color:var(--text-faint);font-style:italic;">legacy single-source:</span>
        <span style="font-family:var(--mono,monospace);color:var(--text-dim);">${esc(s.repo_url)}</span>
        <span style="color:var(--accent);font-family:var(--mono,monospace);">${esc(s.repo_branch || '')}</span>
      </div>
      <div style="font-size:10px;color:var(--accent);margin-top:4px;">
        ConfigMap is in legacy shape — run <strong>↻ Project all</strong> on the Managed repos panel to convert to multi-source.
      </div>`;
  } else {
    listEl.innerHTML = '<span style="color:var(--text-faint);font-style:italic;">no corpus sources configured</span>';
  }
  document.getElementById('srcCorpusWhen').textContent = fmtTs(s.last_applied_at);
  document.getElementById('srcCorpusWho').textContent  = s.last_applied_by || '—';
  const statusEl = document.getElementById('srcCorpusStatus');
  // Corpus has no Deployment to roll; show static "n/a"
  statusEl.innerHTML = '<span style="color:var(--text-faint);">no rollout — Tekton reads ConfigMap fresh per analysis</span>';
}

async function loadBranches(kind, repoUrl) {
  const sel = document.getElementById(SOURCE_UI[kind].branchSelect);
  if (!repoUrl) { sel.innerHTML = '<option value="">(enter a repo URL first)</option>'; return; }
  sel.innerHTML = '<option value="">(loading…)</option>';
  try {
    const resp = await api('/api/sources/branches?repo_url=' + encodeURIComponent(repoUrl));
    const branches = resp.branches || [];
    if (!branches.length) { sel.innerHTML = '<option value="">(no branches — use free-text)</option>'; return; }
    sel.innerHTML = '<option value="">(select a branch)</option>';
    branches.forEach(b => { const o=document.createElement('option'); o.value=b; o.textContent=b; sel.appendChild(o); });
  } catch { sel.innerHTML = '<option value="">(GitHub API failed — use free-text)</option>'; }
}

async function applySource(kind) {
  const ui = SOURCE_UI[kind];
  const repoUrl = document.getElementById(ui.repoInput).value.trim();
  const branch  = document.getElementById(ui.branchInput).value.trim() || document.getElementById(ui.branchSelect).value;
  const msg = document.getElementById(ui.msg), btn = document.getElementById(ui.applyBtn);
  if (!repoUrl) { msg.innerHTML = '<span style="color:var(--red)">repo URL required</span>'; return; }
  if (!branch)  { msg.innerHTML = '<span style="color:var(--red)">branch required</span>';   return; }
  const current = sourcesState[kind];
  if (current && current.repo_url===repoUrl && current.repo_branch===branch) {
    msg.innerHTML = '<span style="color:var(--text-faint)">same as current — nothing to do</span>'; return;
  }
  btn.disabled = true; msg.innerHTML = '<span style="color:var(--blue)">applying…</span>';
  try {
    const resp = await api(`/api/sources/${kind}`, {method:'POST', body:JSON.stringify({repo_url:repoUrl,repo_branch:branch})});
    sourcesState[kind] = resp.state; renderSourcePanel(kind);
    msg.innerHTML = '<span style="color:var(--green)">applied · rolling out</span>';
    toast(`${kind}: applied ${repoUrl}#${branch}`); startSourcePoll(kind);
    document.getElementById(ui.repoInput).dataset.userEdited = '';
    document.getElementById(ui.branchInput).value = '';
  } catch (e) {
    msg.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; toast('Apply failed: '+e.message, true);
  } finally { btn.disabled = false; }
}

function startSourcePoll(kind) {
  if (sourcesPollers[kind]) clearInterval(sourcesPollers[kind]);
  sourcesPollers[kind] = setInterval(async () => {
    try {
      const resp = await api('/api/sources/'+kind);
      sourcesState[kind] = resp.state; renderSourcePanel(kind);
      if (resp.state.rollout?.rolled_out) {
        clearInterval(sourcesPollers[kind]); sourcesPollers[kind] = null;
        document.getElementById(SOURCE_UI[kind].msg).innerHTML = '<span style="color:var(--green)">rolled out</span>';
        toast(`${kind}: rollout complete`);
      }
    } catch {}
  }, 3000);
  setTimeout(() => { if (sourcesPollers[kind]) { clearInterval(sourcesPollers[kind]); sourcesPollers[kind]=null; } }, 5*60*1000);
}

function wireSourcesPanel(kind) {
  const ui = SOURCE_UI[kind];
  document.getElementById(ui.repoInput).addEventListener('change', () => {
    document.getElementById(ui.repoInput).dataset.userEdited = '1';
    loadBranches(kind, document.getElementById(ui.repoInput).value.trim());
  });
  document.getElementById(ui.branchSelect).addEventListener('change', () => {
    if (document.getElementById(ui.branchSelect).value) document.getElementById(ui.branchInput).value = '';
  });
  document.getElementById(ui.branchInput).addEventListener('input', () => {
    if (document.getElementById(ui.branchInput).value) document.getElementById(ui.branchSelect).value = '';
  });
  document.getElementById(ui.refreshBranchesBtn).addEventListener('click', () =>
    loadBranches(kind, document.getElementById(ui.repoInput).value.trim()));
  document.getElementById(ui.applyBtn).addEventListener('click', () => applySource(kind));
}

// ── Wire-up ──────────────────────────────────────────────────

// Sidebar nav: .pf-nav-item clicks drive switchView. Items with data-cfg also
// jump to a specific Config panel (Users & roles / Projects nav shortcuts).
// Rail clicks → switch domain. Event-delegated on the container so it survives
// renderDomainRail() re-renders and binds once (the anchors are created dynamically).
document.querySelector('.pf-nav-items')?.addEventListener('click', (e) => {
  const a = e.target.closest('.pf-nav-item[data-domain]');
  if (a) switchDomain(a.dataset.domain);
});

// Workspace focus switcher (Architecture ⇄ Assessment) + read-only View-mode toggle.
document.getElementById('personaSel')?.addEventListener('change', (e) => setPersona(e.target.value));
document.getElementById('viewModeToggle')?.addEventListener('click', toggleViewMode);

// ── Analysis freshness chip (#112 / uc-scoped-evaluation-design.md step 4) ────
// Coverage (evaluated/total) + content staleness for the active project. Status, not selection.
async function loadFreshness() {
  const sumEl = document.getElementById('freshSummary');
  const dot = document.getElementById('freshDot');
  if (!sumEl) return;
  let f;
  // #239 / TODO3: Coverage follows the masthead Scope (Scoping Set). scopeQuery() → ?set_id=…
  const _scopeQ = (typeof scopeQuery === 'function') ? scopeQuery() : '';
  try { f = await api('/api/freshness' + _scopeQ); }
  catch { sumEl.textContent = '—'; if (dot) dot.style.background = 'var(--text-faint)'; return; }
  const _scoped = !!_scopeQ;   // coverage is scoped to a Scoping Set (not the whole project)
  const total = f.total || 0, ingested = f.ingested || 0, stale = f.stale || 0;
  const managed = (f.managed != null ? f.managed : total), corpus = f.corpus || 0;
  // Pill = evaluated / total-available (managed + corpus, regardless of ingest status).
  sumEl.textContent = total ? `${ingested}/${total}${stale ? ` · ${stale} stale` : ''}` : 'no UCs';
  if (dot) {
    // green = managed UCs all evaluated + fresh; amber = managed stale/uncovered; faint = nothing
    // evaluated. Corpus (defined-but-not-ingested) isn't evaluatable, so it doesn't force amber.
    const attn = !!(ingested && (stale || ingested < managed));   // needs attention
    let c = 'var(--text-faint)';
    if (ingested) c = attn ? 'var(--amber, #d79a2b)' : 'var(--ok, var(--green))';
    dot.style.background = c;
    dot.classList.toggle('pulse', attn);   // pulse only when attention is warranted
  }
  const pop = document.getElementById('freshnessPopover');
  if (pop) {
    const row = (l, v) => `<div style="display:flex;justify-content:space-between;gap:16px;padding:2px 0;"><span style="color:var(--text-dim);">${l}</span><span>${v}</span></div>`;
    pop.innerHTML =
      `<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-bottom:5px;">Analysis freshness${_scoped ? ' · <span style="color:var(--accent);">scoped</span>' : ''}</div>`
      + row(_scoped ? 'Use cases (in scope)' : 'Use cases (this project)', total)
      // Breakdown: managed (ingested into the DB) + corpus from the project's corpus repos (#199).
      // Both count toward the total — the pill is the complete story (drift in either re-runs).
      + row('<span style="color:var(--text-faint);">├ Managed (ingested)</span>', `<span style="color:var(--text-faint);">${managed}</span>`)
      + (corpus ? row('<span style="color:var(--text-faint);">├ Corpus (from repos)</span>', `<span style="color:var(--text-faint);">${corpus}</span>`) : '')
      + (f.deprecated ? row('<span style="color:var(--text-faint);">Deprecated (excluded)</span>', `<span style="color:var(--text-faint);">${f.deprecated}</span>`) : '')
      + row('Evaluated', `${ingested} / ${managed}${f.uncovered ? ` <span style="color:var(--text-faint);">(${f.uncovered} unevaluated)</span>` : ''}`)
      + (f.failed ? row('Failed', `<span style="color:var(--red);">${f.failed}</span>`) : '')
      + row('Stale', `${stale}${stale ? ` <span style="color:var(--text-faint);">(${f.stale_edited || 0} edited · ${f.stale_drifted || 0} code-drifted)</span>` : ''}`)
      + row('Last evaluation', f.last_eval ? _ago(f.last_eval) : '—')
      + ((stale + (f.uncovered || 0)) > 0
          ? `<button class="btn primary btn-sm" style="margin-top:8px;width:100%;" onclick="analyzeStaleUCs()" title="Start a new analysis scoped to the un-evaluated / stale use cases">▶ Analyze ${stale + (f.uncovered || 0)} un-evaluated / stale</button>`
          : `<div style="margin-top:6px;font-size:10px;color:var(--green);">All use cases evaluated &amp; fresh.</div>`);
  }
}
// One-click "ingest what's missing" from the masthead freshness popover (mirrors the
// Ingestions-tab audit button, but self-contained so it works from anywhere).
async function analyzeStaleUCs() {
  // Open New Ingestion pre-selected to the Stale / un-ingested scope (UCs needing evaluation).
  const pop = document.getElementById('freshnessPopover'); if (pop) pop.style.display = 'none';
  openNewRun(undefined, undefined, undefined, undefined, { set_id: '__stale__', selection_mode: 'selection' });
}
document.getElementById('freshnessChip')?.addEventListener('click', () => {
  const pop = document.getElementById('freshnessPopover');
  if (pop) pop.style.display = pop.style.display === 'none' ? '' : 'none';
});
document.getElementById('freshnessPopover')?.addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', (e) => {
  const chip = document.getElementById('freshnessChip');
  const pop = document.getElementById('freshnessPopover');
  if (pop && pop.style.display !== 'none' && chip && !chip.contains(e.target)) pop.style.display = 'none';
});

// ── Live run-progress chip (#112) — aggregate in-progress runs from allRuns ───
function _runIsActive(r) {
  return r && !r.archived && !['Succeeded','Failed','Cancelled','TimedOut'].includes(r.phase);
}
// Denominator for "<done> of <total> use cases".
//
// uc_total counts INGESTED results, so while a run is in flight it equals the
// number finished — dividing by it made every live run read "N/N done". A 6-UC
// run showed "4/4 UC ✓4" at its halfway point, indistinguishable from a
// finished run, while the log-derived progress panel correctly read
// "3 / 6 · 50% done" (observed on dav-stage2-console-114714).
//
// uc_scope_total is the scope declared at trigger and never moves. It is NULL
// for full-corpus runs and for any run created before migration t007, hence the
// fallback — which is only ever wrong in the way the old behaviour always was.
function _runScopeTotal(r) {
  if (!r) return 0;
  return (typeof r.uc_scope_total === 'number' && r.uc_scope_total > 0)
    ? r.uc_scope_total
    : (r.uc_total || 0);
}
function _fmtEta(ms) {
  if (!ms || !isFinite(ms) || ms < 0) return '';
  const s = Math.round(ms / 1000);
  if (s < 90) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m}m`;
  const h = Math.floor(m / 60); return `${h}h${m % 60}m`;
}
// The masthead Analysis pill is ALWAYS the aggregate stats — never the name of a run in
// progress (no flipping). Format: <#runs> · <#done>/<#UCs across all active runs> · ✓<succ> ✗<fail>.
function _renderRunChipLive() {
  const active = (allRuns || []).filter(_runIsActive);
  const dot = document.getElementById('rccDot');
  const nm = document.getElementById('rccName');
  const lbl = document.getElementById('rccLabel');
  if (!nm) return;
  if (!active.length) {
    if (lbl) lbl.textContent = 'Analysis';
    nm.textContent = '— none —';              // idle: neutral, NOT a run name
    if (dot) dot.classList.remove('pulse');
    return;
  }
  if (lbl) lbl.textContent = 'Analysis';      // label stays "Analysis"; the dot pulses + stats lead with "# active"
  let ucs = 0, succ = 0, fail = 0;
  // Prefer the server-attached LIVE progress (run-progress.yaml — same source as
  // the detail panel). Session columns only populate at finalize/ingest, which
  // made the pill read "0/0 UC" for the whole life of an in-flight run.
  active.forEach(r => {
    if (r.progress && r.progress.total_ucs) {
      ucs += r.progress.total_ucs; succ += (r.progress.succeeded || 0); fail += (r.progress.failed || 0);
    } else {
      ucs += _runScopeTotal(r); succ += (r.uc_succeeded || 0); fail += (r.uc_failed || 0);
    }
  });
  const done = succ + fail;
  // "<N> active · <done>/<total> UC · ✓ ok ✗ failed".
  nm.textContent = `${active.length} active · ${done}/${ucs} UC · ✓${succ} ✗${fail}`;
  const chip = document.getElementById('runContextChip');
  if (chip) chip.title = `${active.length} analysis run${active.length > 1 ? 's' : ''} active · ` +
    `${done} of ${ucs} use cases processed (✓${succ} succeeded, ✗${fail} failed) · click for per-run progress`;
  if (dot) dot.classList.add('pulse');
}
function _renderRunChipPopover() {
  const pop = document.getElementById('runChipPopover');
  if (!pop) return;
  const active = (allRuns || []).filter(_runIsActive);
  if (!active.length) { pop.innerHTML = '<div style="color:var(--text-faint);">No analyses in progress.</div>'; return; }
  pop.innerHTML =
    `<div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-bottom:5px;">Running (${active.length}) — click to open</div>`
    + active.map(r => {
        const t = _runScopeTotal(r), s = r.uc_succeeded || 0, f = r.uc_failed || 0;
        return `<div class="rcc-pop-run" data-run="${esc(r.name)}" style="display:flex;flex-direction:column;gap:3px;padding:5px 4px;border-radius:3px;cursor:pointer;">
          <div style="display:flex;justify-content:space-between;gap:10px;"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:190px;">${esc(r.session_name || r.name)}</span><span style="color:var(--text-faint);">${s + f}/${t}</span></div>
          <div class="uc-progress-bar" style="margin:0;"><span class="seg-success" style="width:${t ? s / t * 100 : 0}%"></span><span class="seg-failed" style="width:${t ? f / t * 100 : 0}%"></span></div>
        </div>`;
      }).join('');
  pop.querySelectorAll('.rcc-pop-run').forEach(el => el.addEventListener('click', () => {
    pop.style.display = 'none';
    switchView('runs');
    setTimeout(() => { try { openRunDrawer(el.dataset.run); } catch {} }, 60);
  }));
}
document.getElementById('runContextChip')?.addEventListener('click', () => {
  const pop = document.getElementById('runChipPopover');
  if (!pop) return;
  if (pop.style.display === 'none') { _renderRunChipPopover(); pop.style.display = ''; }
  else pop.style.display = 'none';
});
document.getElementById('runChipPopover')?.addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', (e) => {
  const chip = document.getElementById('runContextChip');
  const pop = document.getElementById('runChipPopover');
  if (pop && pop.style.display !== 'none' && chip && !chip.contains(e.target)) pop.style.display = 'none';
});
// Persistent, adaptive heartbeat so the masthead pill stays live on EVERY tab (and while a
// run is watched via the drawer, which only refreshes the single run, not allRuns). Fast
// while any ingestion is active; a slow heartbeat when idle so a newly-started run is picked
// up without visiting the Ingestions tab. Self-reschedules; guarded against double-arming.
let _runChipPollTimer = null;
let _prevActiveRuns = new Set();   // #178: names of runs that were active last tick (finish-edge detect)
function _ensureRunChipPoll() {
  if (_runChipPollTimer) return;            // already scheduled — the tick reschedules itself
  // NB: tick must NOT null _runChipPollTimer before awaiting loadRuns — loadRuns() itself calls
  // _ensureRunChipPoll(), and a null would let it arm a SECOND timer → the chain doubles every
  // cycle (a /api/runs flood). Keep the (expired) id truthy so that re-entrant call short-circuits;
  // tick is the sole re-armer.
  const _activeNames = () => new Set((allRuns || []).filter(_runIsActive).map(r => r.name));
  const tick = async () => {
    // Hygiene: skip our own loadRuns when (a) the page is hidden — nothing to paint,
    // no reason to hammer /api/runs from a background tab — or (b) the Runs-view poller
    // (_startRunsListPoll) is already refreshing allRuns, which would double the request.
    const runsPollActive = !!_runsListPollTimer
      && document.getElementById('view-runs')?.classList.contains('active');
    if (document.visibilityState === 'visible' && !runsPollActive) {
      try { await loadRuns({ silent: true }); } catch {}   // refreshes allRuns + _renderRunChipLive
    }
    const nowActive = _activeNames();
    // #178: when a run that WAS active is no longer active, it just finished → its evaluations were
    // ingested, so the masthead Coverage pill's ingested/total is stale. Refresh it on that edge
    // only (not every tick) so /api/freshness isn't polled while runs merely progress.
    let finished = false;
    _prevActiveRuns.forEach(n => { if (!nowActive.has(n)) finished = true; });
    if (finished) { try { loadFreshness(); } catch {} }
    _prevActiveRuns = nowActive;
    _runChipPollTimer = setTimeout(tick, nowActive.size ? 7000 : 30000);
  };
  _prevActiveRuns = _activeNames();
  _runChipPollTimer = setTimeout(tick, _prevActiveRuns.size ? 7000 : 30000);
}
try { _applyViewMode(); } catch (e) { console.warn('view-mode init failed', e); }

// Masthead hamburger toggle
document.getElementById('navToggleBtn').addEventListener('click', toggleNav);

// Masthead run STATUS (read-only): reflect the active run. The run is *working context*,
// not global chrome — selection happens in Execution → Runs (ux-paradigm-design.md). Kept
// as a function so its existing callers (runs-list refresh, run selection) keep the label fresh.
function _populateGlobalRunSel(){
  // Retained for its many callers (runs-list refresh, run selection) — the masthead pill is
  // now always the aggregate stats, so just refresh those rather than writing a run name.
  _renderRunChipLive();
}
// Loading indicator while a run change pulls fresh data for the shown page: the run-chip
// spinner plus a light, non-blocking overlay over the content area.
function _runLoading(on) {
  const sp = document.getElementById('runLoadSpinner');
  if (sp) sp.style.display = on ? '' : 'none';
  let ov = document.getElementById('runLoadOverlay');
  if (on) {
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'runLoadOverlay';
      ov.style.cssText = 'position:fixed;left:0;right:0;top:48px;bottom:0;z-index:60;display:flex;align-items:flex-start;justify-content:center;padding-top:12vh;background:rgba(0,0,0,0.12);pointer-events:none;';
      ov.innerHTML = '<div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-dim);background:var(--bg-panel);border:1px solid var(--border);border-radius:3px;padding:8px 14px;box-shadow:0 4px 14px rgba(0,0,0,0.3);"><span class="llm-spinner"></span>Loading analysis…</div>';
      document.body.appendChild(ov);
    }
    ov.style.display = '';
  } else if (ov) { ov.style.display = 'none'; }
}

// (The masthead run selector is retired — run selection lives in Execution → Runs, which
// calls selectRunResult directly. The masthead shows read-only run status; see #rccName.)

// View Results button in run detail header
document.getElementById('rdViewResultsBtn')?.addEventListener('click', () => {
  if (activeRunResultId) {
    switchView('results');
    // loadResults will auto-select the active run
  } else {
    switchView('results');
  }
});

// "Review this analysis →" — IA slice 3: review/enhancement/PR generation has ONE home
// (Roadmaps). This scopes Roadmaps to the analysis's originating Scoping Set (runs record
// set_id) and opens Arch Review. No set_id (corpus/ad-hoc run) → scope to all use cases.
document.getElementById('rdReviewBtn')?.addEventListener('click', () => {
  const setId = activeRunSummary?.set_id;
  try { setScope(setId != null ? String(setId) : ''); } catch (_) {}
  switchView('review');
});

// Diagnose button in run detail header — jump to the Improve tab, preselect
// this run, and run the diagnoser.
document.getElementById('rdDiagnoseBtn')?.addEventListener('click', async () => {
  const run = _rdName;
  if (!run) return;
  switchView('improve');
  await loadImproveQueue();
  const sel = document.getElementById('improveRunSelect');
  if (sel) {
    if (![...sel.options].some(o => o.value === run)) {
      sel.add(new Option(run, run), 0);
    }
    sel.value = run;
  }
  diagnoseSelectedRun();
});

document.getElementById('newRunBtn').addEventListener('click', () => openNewRun());
document.getElementById('closeNewRun').addEventListener('click', closeNewRun);
document.getElementById('cancelNewRun').addEventListener('click', closeNewRun);
document.getElementById('submitNewRun').addEventListener('click', submitNewRun);
document.getElementById('nrReloadDefaults').addEventListener('click', () => loadNewRunDefaults());
document.getElementById('nrModelSel').addEventListener('change', e => localStorage.setItem('nrLastModel', e.target.value));
document.getElementById('refreshRunsBtn').addEventListener('click', loadRuns);

document.getElementById('refreshResultsBtn').addEventListener('click', loadResults);
document.getElementById('resultFilter').addEventListener('input', renderResultList);
document.getElementById('ucVerdictFilter').addEventListener('change', () => {
  if (activeRunSummary) renderUCResultList(activeRunSummary);
  else _renderScopedUCList();   // scoped Results list (no run summary)
});
document.getElementById('ucGroupBy').addEventListener('change', () => {
  try { localStorage.setItem('ucGroupByMode', document.getElementById('ucGroupBy').value); } catch(e) {}
  if (activeRunSummary) renderUCResultList(activeRunSummary);
  else _renderScopedUCList();
});
// Restore group-by preference on load
try {
  const saved = localStorage.getItem('ucGroupByMode');
  if (saved !== null) document.getElementById('ucGroupBy').value = saved;
} catch(e) {}

// ── Generic split-resizer: any .split-resizer[data-rs-left=<panelId>][data-rs-storage=<key>]
// resizes the left-of-it panel. Width persisted in localStorage if storage key given.
(function() {
  function attachResizer(handle) {
    const targetId = handle.dataset.rsLeft;
    const storeKey = handle.dataset.rsStorage;
    const target = document.getElementById(targetId);
    if (!target) return;
    // Restore saved width
    if (storeKey) {
      try {
        const saved = localStorage.getItem(storeKey);
        if (saved) target.style.width = saved + 'px';
      } catch(e) {}
    }
    let dragging = false; let startX = 0; let startW = 0;
    handle.addEventListener('mousedown', e => {
      dragging = true; startX = e.clientX; startW = target.getBoundingClientRect().width;
      handle.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!dragging) return;
      const newW = Math.max(160, Math.min(900, startW + (e.clientX - startX)));
      target.style.width = newW + 'px';
    });
    document.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (storeKey) {
        try { localStorage.setItem(storeKey, parseInt(target.style.width, 10)); } catch(e) {}
      }
    });
  }
  document.querySelectorAll('.split-resizer').forEach(attachResizer);
})();

document.getElementById('newUCBtn').addEventListener('click', () => openUcWizard());
document.getElementById('bulkImportUCBtn').addEventListener('click', () => openBulkImport());
document.getElementById('closeBulkImport').addEventListener('click', closeBulkImport);
document.getElementById('cancelBulkImport').addEventListener('click', closeBulkImport);
document.getElementById('closeUcWizard').addEventListener('click', closeUcWizard);
document.getElementById('cancelUcWizard').addEventListener('click', closeUcWizard);
document.getElementById('wzBackBtn').addEventListener('click', wzBack);
document.getElementById('wzPrimaryBtn').addEventListener('click', wzNext);
document.getElementById('wzRefineBtn').addEventListener('click', wzRefine);
document.getElementById('wzValidateBtn').addEventListener('click', wzValidate);
document.getElementById('wzAdvancedBtn').addEventListener('click', wzToAdvanced);
document.getElementById('closeUCModal').addEventListener('click', closeUCModal);
document.getElementById('cancelUCModal').addEventListener('click', closeUCModal);
document.getElementById('saveUCModal').addEventListener('click', saveUC);
document.getElementById('ucFilter').addEventListener('input', renderUCList);
document.getElementById('ucSourceFilter').addEventListener('change', () => loadUCs());
document.getElementById('ucPriorityFilter').addEventListener('change', e => {
  ucPriorityFilter = e.target.value;
  renderUCList();
});
document.getElementById('ucSortPriority').addEventListener('click', e => {
  ucSortByPriority = !ucSortByPriority;
  e.currentTarget.style.borderColor = ucSortByPriority ? 'var(--accent)' : 'var(--border-bright)';
  e.currentTarget.style.color = ucSortByPriority ? 'var(--accent)' : '';
  renderUCList();
});

// Lifecycle state filter chips
// Lifecycle-state + assignment filters (unified select style, matching the Scoping Sets palette).
document.getElementById('ucStateFilter')?.addEventListener('change', e => {
  ucStateFilter = e.target.value; renderUCList();
});
document.getElementById('ucAssignFilter')?.addEventListener('change', e => {
  ucAssignFilter = e.target.value; renderUCList();
});
document.getElementById('ucHealthFilter')?.addEventListener('change', e => {
  ucHealthFilter = e.target.value; renderUCList();
});

// LC transition modal
document.getElementById('closeLCModal').addEventListener('click', closeLCModal);
document.getElementById('cancelLCModal').addEventListener('click', closeLCModal);
document.getElementById('confirmLCModal').addEventListener('click', confirmLCTransition);

// Scoping Sets (now merged into the UC tab)
document.getElementById('newSetBtn').addEventListener('click', () => openSetModal());
document.getElementById('closeSetModal').addEventListener('click', closeSetModal);
document.getElementById('cancelSetModal').addEventListener('click', closeSetModal);
document.getElementById('saveSetModal').addEventListener('click', saveSet);
// Manage Scoping Sets modal
document.getElementById('manageSetsBtn').addEventListener('click', openManageSetsModal);
document.getElementById('closeManageSetsModal').addEventListener('click', closeManageSetsModal);
document.getElementById('manageSetsDoneBtn').addEventListener('click', closeManageSetsModal);
document.getElementById('manageSetsNewBtn').addEventListener('click', () => { closeManageSetsModal(); openSetModal(); });
// Active-set banner buttons
document.getElementById('ucListSetBannerRunBtn').addEventListener('click', () => {
  if (activeSetId === null) { runSet(0, 'All Use Cases'); return; }  // synthetic All set
  if (typeof activeSetId !== 'number') return;
  const s = (allSets || []).find(x => x.id === activeSetId);
  if (s) runSet(s.id, s.name);
});
document.getElementById('ucListSetBannerManageBtn').addEventListener('click', openManageSetsModal);
document.getElementById('ucListSetBannerClearBtn').addEventListener('click', () => selectSet('__all__'));
// Multi-select toolbar
document.getElementById('ucSelTestBtn').addEventListener('click', _batchTestSelectedUCs);
document.getElementById('ucSelAddSetBtn').addEventListener('click', e => _openBatchAddSetPopover(e.currentTarget));
document.getElementById('ucSelClearBtn').addEventListener('click', _clearUCSelection);

// Import / export
document.getElementById('importUCBtn').addEventListener('click', openImportModal);
document.getElementById('exportUCBtn').addEventListener('click', e => {
  e.stopPropagation();
  const m = document.getElementById('exportUCMenu');
  const visible = m.style.display !== 'none';
  document.querySelectorAll('[id^="exportSetMenu-"],[id="exportUCMenu"]').forEach(el => el.style.display = 'none');
  if (!visible) m.style.display = '';
});
document.getElementById('closeImportModal').addEventListener('click', closeImportModal);
document.getElementById('cancelImportModal').addEventListener('click', closeImportModal);
document.getElementById('submitImportBtn').addEventListener('click', submitImport);

// Promote set
document.getElementById('closePromoteModal').addEventListener('click', closePromoteModal);
document.getElementById('cancelPromoteModal').addEventListener('click', closePromoteModal);
document.getElementById('confirmPromoteModal').addEventListener('click', confirmPromote);

// Add member modal
document.getElementById('closeAddMember').addEventListener('click', closeAddMember);
document.getElementById('cancelAddMember').addEventListener('click', closeAddMember);
document.getElementById('saveAddMember').addEventListener('click', saveAddMember);
document.getElementById('memberSearchInput').addEventListener('input', e => onMemberSearch(e.target.value));
document.getElementById('memberSearchInput').addEventListener('blur', () => {
  setTimeout(() => { document.getElementById('memberDropdown').style.display = 'none'; }, 200);
});

// Close modals on overlay click
['newRunModal','ucModal','setModal','addMemberModal','lcModal','importModal','promoteModal','bulkImportModal','ucWizardModal'].forEach(id => {
  document.getElementById(id).addEventListener('click', e => {
    if (e.target===document.getElementById(id)) document.getElementById(id).classList.remove('open');
  });
});

// Post-M11: both spec and corpus are read-only views projected from
// managed_repos. wireSourcesPanel is no longer called for either —
// inference endpoint is the only remaining editable Sources panel.

// Jump-to-Repos buttons on the read-only spec + corpus panels
for (const btnId of ['srcSpecJumpToRepos', 'srcCorpusJumpToRepos']) {
  document.getElementById(btnId)?.addEventListener('click', () => {
    const target = document.getElementById('configReposPanel');
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

// ── Review Models ────────────────────────────────────────────────────────────

let _reviewModels = [];   // cached from /api/models
let _mfEditId = null;     // ID of model being edited (null = create)

// Two-click arm/confirm for delete buttons (replaces confirm() which is
// suppressed by the OCP OAuth proxy). First click: shows "Sure?" + red outline.
// Second click on the same button fires action(). Auto-resets after 4 s.
function _armDeleteBtn(btn, action) {
  if (btn.dataset.armed) {
    delete btn.dataset.armed;
    btn.textContent = btn.dataset.origText;
    btn.style.outline = '';
    action();
    return;
  }
  btn.dataset.origText = btn.textContent;
  btn.dataset.armed = '1';
  btn.textContent = 'Sure?';
  btn.style.outline = '1px solid var(--red)';
  setTimeout(() => {
    if (btn.dataset.armed) {
      delete btn.dataset.armed;
      btn.textContent = btn.dataset.origText;
      btn.style.outline = '';
    }
  }, 4000);
}

// Delete-propagation warning: fetch the server's impact preview and show what a delete cascades to,
// so deletion (allowed for sovereignty/right-to-erase) is informed. The server also audits it.
// Returns null if cancelled, else {purge:bool} (purge = also erase a UC's historical analyses).
// Falls back to a plain confirm if the preview can't be fetched.
async function _confirmDeleteImpact(kind, id, label) {
  let impact;
  try {
    const path = kind === 'uc'
      ? `/api/use-cases/${encodeURIComponent(id)}/delete-impact`
      : `/api/sets/${id}/delete-impact`;
    impact = (await api(path)).impact;
  } catch (_) {
    return confirm(`Delete ${label}? This cannot be undone (and is audited).`) ? { purge: false } : null;
  }
  const lines = [];
  if (kind === 'uc') {
    const r = impact.removed || {}, k = impact.retained || {};
    lines.push(`Deleting use case "${label}" also removes:`);
    lines.push(`  • ${r.set_memberships || 0} scoping-set membership(s)`);
    lines.push(`  • ${r.project_refs || 0} project reference(s)`);
    lines.push(`  • ${r.customer_requests || 0} customer-demand record(s)`);
    lines.push(`  • ${r.lifecycle_events || 0} lifecycle event(s)`);
    if (k.past_analyses)
      lines.push(`\n${k.past_analyses} past analysis result(s) reference this UC.`);
    lines.push(`\nThis action is audited. Continue?`);
    if (!confirm(lines.join('\n'))) return null;
    // Sovereignty erasure choice — only when there are analyses to purge.
    let purge = false;
    if (k.past_analyses) {
      purge = confirm(
        `This use case has ${k.past_analyses} historical analysis result(s).\n\n` +
        `OK  = ALSO permanently erase them (full sovereignty erasure — audited).\n` +
        `Cancel = keep them as a historical record (the UC is still deleted).`);
    }
    return { purge };
  }
  const r = impact.removed || {}, d = impact.detached || {};
  lines.push(`Deleting scoping set "${label}":`);
  lines.push(`  • removes ${r.memberships || 0} membership(s) — the use cases themselves are kept`);
  if (d.past_runs)
    lines.push(`  • detaches ${d.past_runs} past run(s) — they keep the recorded set name, but the live link is cleared`);
  lines.push(`\nThis action is audited. Continue?`);
  return confirm(lines.join('\n')) ? { purge: false } : null;
}

async function loadReviewModels() {
  try {
    _reviewModels = await api('/api/models');
  } catch(e) {
    _reviewModels = [];
  }
}

function renderModelList() {
  const el = document.getElementById('modelList');
  if (!el) return;
  if (!_reviewModels.length) {
    el.innerHTML = '<div style="padding:14px 16px;font-size:12px;color:var(--text-faint)">No models configured. Click "+ Add model" to add one.</div>';
    return;
  }
  el.innerHTML = _reviewModels.map(m => `
    <div class="model-manager-row">
      <span class="model-pill ${m.is_local ? 'local' : 'frontier'}">${m.is_local ? 'local' : 'frontier'}</span>
      <span style="font-weight:500;color:var(--text);flex:1">${esc(m.name)}${m.from_bundle ? ' <span class="model-pill" style="background:var(--bg-raised);" title="Provided by an attached bundle — manage it in Config → Platform → Bundles">bundle</span>' : ''}</span>
      <span style="color:var(--text-faint);font-size:11px;">${esc(m.provider)} · ${esc(m.model_id)}</span>
      ${!m.enabled ? '<span class="model-pill disabled">disabled</span>' : ''}
      ${m.from_bundle
        ? '<span style="color:var(--text-faint);font-size:11px;">read-only</span>'
        : `<button class="btn ghost btn-sm" onclick="editModel(${m.id})">Edit</button>
      <button class="btn ghost btn-sm" style="color:var(--red)" onclick="deleteModel(${m.id},this)">✕</button>`}
    </div>`).join('');
}

function _populateModelSel(selId, storageKey) {
  const sel = document.getElementById(selId);
  if (!sel) return;
  const enabled = _reviewModels.filter(m => m.enabled);
  sel.innerHTML = '<option value="">— select a model —</option>' +
    enabled.map(m => {
      const suffix = m.is_local ? 'local' : 'frontier';
      const label = m.name.toLowerCase().includes(`(${suffix})`) ? m.name : `${m.name} (${suffix})`;
      return `<option value="${m.id}">${esc(label)}</option>`;
    }).join('');
  const stored = localStorage.getItem(storageKey);
  if (stored === '__custom__') {
    const ep = localStorage.getItem(storageKey + '_ep');
    const mi = localStorage.getItem(storageKey + '_mi');
    if (ep && mi) {
      // If a newly registered model now covers this custom selection, switch to it
      const match = enabled.find(m => m.endpoint_url === ep && m.model_id === mi);
      if (match) {
        sel.value = String(match.id);
        localStorage.setItem(storageKey, String(match.id));
      } else {
        const opt = document.createElement('option');
        opt.value = '__custom__';
        opt.textContent = `Custom: ${mi}`;
        sel.appendChild(opt);
        sel.value = '__custom__';
      }
    }
  } else if (stored && enabled.find(m => String(m.id) === stored)) {
    sel.value = stored;
  }
}

function _resolveEndpointModel(selId, storageKey) {
  const sel = document.getElementById(selId);
  if (!sel || !sel.value) return null;
  if (sel.value === '__custom__') {
    const ep = localStorage.getItem(storageKey + '_ep');
    const mi = localStorage.getItem(storageKey + '_mi');
    if (!ep || !mi) return null;
    return { endpoint_url: ep, model_id: mi };
  }
  const id = parseInt(sel.value, 10);
  if (!id) return null;
  return { model_config_id: id };
}

// ── Model default resolution (bypasses DOM, reads localStorage directly) ──────

function _resolveFromStorage(storageKey) {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return null;
  if (stored === '__custom__') {
    const ep = localStorage.getItem(storageKey + '_ep');
    const mi = localStorage.getItem(storageKey + '_mi');
    return (ep && mi) ? { endpoint_url: ep, model_id: mi } : null;
  }
  const id = parseInt(stored, 10);
  return id ? { model_config_id: id } : null;
}

// ── Project-scoped evaluation model default ────────────────────────────────────

let _evalDefaultModelId = null;

function _populateEvalDefaultSel(modelId) {
  const sel = document.getElementById('evalDefaultModelSel');
  if (!sel) return;
  const enabled = _reviewModels.filter(m => m.enabled);
  sel.innerHTML = '<option value="">— no default set —</option>' +
    enabled.map(m => {
      const suffix = m.is_local ? 'local' : 'frontier';
      const label = m.name.toLowerCase().includes(`(${suffix})`) ? m.name : `${m.name} (${suffix})`;
      return `<option value="${m.id}">${esc(label)}</option>`;
    }).join('');
  if (modelId) sel.value = String(modelId);
}

async function loadEvalDefault() {
  try {
    const defaults = await api('/api/model-defaults');
    _evalDefaultModelId = defaults.evaluation || null;
  } catch(e) {
    _evalDefaultModelId = null;
  }
  _populateEvalDefaultSel(_evalDefaultModelId);
}

document.getElementById('saveEvalDefaultBtn').addEventListener('click', async () => {
  const sel = document.getElementById('evalDefaultModelSel');
  const val = sel.value;
  if (val === '__custom__') {
    document.getElementById('evalDefaultMsg').textContent = 'Custom endpoints cannot be a project default — register the endpoint in Model Endpoints first.';
    return;
  }
  const modelId = val ? parseInt(val, 10) : null;
  const btn = document.getElementById('saveEvalDefaultBtn');
  const msgEl = document.getElementById('evalDefaultMsg');
  btn.disabled = true; msgEl.textContent = '';
  try {
    await api('/api/model-defaults/evaluation', {
      method: 'PUT',
      body: JSON.stringify({ model_config_id: modelId }),
    });
    _evalDefaultModelId = modelId;
    msgEl.textContent = modelId ? 'Default saved' : 'Default cleared';
    msgEl.style.color = 'var(--green)';
    setTimeout(() => { msgEl.textContent = ''; msgEl.style.color = ''; }, 2500);
  } catch(e) {
    msgEl.textContent = 'Save failed: ' + e.message;
    msgEl.style.color = 'var(--red)';
  }
  btn.disabled = false;
});

// ── Project-scoped arch-review model default ───────────────────────────────────
// Mirrors the evaluation default above. Backed by model_defaults
// (key='arch-review'); /api/arch-review reads it when the caller omits an
// explicit model_config_id / endpoint_url+model_id override.

let _archDefaultModelId = null;
let _archDefaultLoaded = false;

function _populateArchDefaultSel(modelId) {
  const sel = document.getElementById('archDefaultModelSel');
  if (!sel) return;
  // Only show models with use_arch_review=true (the per-row gate column).
  const eligible = _reviewModels.filter(m => m.enabled && m.use_arch_review);
  sel.innerHTML = '<option value="">— no default set —</option>' +
    eligible.map(m => {
      const suffix = m.is_local ? 'local' : 'frontier';
      const label = m.name.toLowerCase().includes(`(${suffix})`) ? m.name : `${m.name} (${suffix})`;
      return `<option value="${m.id}">${esc(label)}</option>`;
    }).join('');
  if (modelId) sel.value = String(modelId);
}

async function loadArchDefault() {
  // Single loader for ALL project model defaults (arch-review, enhancement,
  // uc-authoring, evaluation). Populates each Config default selector.
  try {
    _modelDefaults = (await api('/api/model-defaults')) || {};
  } catch(e) {
    _modelDefaults = {};
  }
  _archDefaultModelId = _modelDefaults['arch-review'] || null;
  _archDefaultLoaded = true;   // set before _updateArchModelInfo to break recursion
  _populateArchDefaultSel(_archDefaultModelId);
  _populateDefaultSel('enhDefaultModelSel', _modelDefaults['enhancement'] || null);
  _populateDefaultSel('ucAssistModelSel',   _modelDefaults['uc-authoring'] || null);
  _populateDefaultSel('assessIngestDefaultModelSel', _modelDefaults['assessment-ingest'] || null);
  _updateArchModelInfo();
}

// Reflect the Config "Default Arch Review model" into the Architecture view's
// read-only label, so the operator sees which model a run will use. The model
// itself is never chosen in the Architecture view — single source of truth is
// the Config default (model_defaults key='arch-review'), which the API applies.
// ── Two-tier model selection ────────────────────────────────────────────────
// Tier 1 (Config): per-use "default" selectors, server-backed via model_defaults.
// Tier 2 (views): per-use "override" selectors — first option "Use default —
//   <name>"; a blank value sends no model so the endpoint resolves the default.
//   Uses: arch-review, enhancement (chains to arch-review), uc-authoring
//   (assist panel + wizard + bulk import), and __engine__ (new-run; default
//   comes from the Config Inference source, not model_defaults).
let _modelDefaults = {};                 // {key: model_config_id}
let _engineDefaultLabel = 'project inference default';

function _modelKindLabel(m) {
  const suffix = m.is_local ? 'local' : 'frontier';
  return m.name.toLowerCase().includes(`(${suffix})`) ? m.name : `${m.name} (${suffix})`;
}

// Effective default model NAME for a use-key (enhancement chains to arch-review).
function _defaultModelName(key) {
  if (key === '__engine__') return _engineDefaultLabel;
  const m = (_reviewModels || []).find(x => String(x.id) === String(_modelDefaults[key]));
  if (m) return _modelKindLabel(m);
  if (key === 'enhancement' && _modelDefaults['arch-review']) {
    return _defaultModelName('arch-review') + ' (via Arch Review)';
  }
  return 'not set';
}

// Tier-2 override selector. Blank value ⇒ use the Config default for `key`.
function _populateOverrideSel(selId, key) {
  const sel = document.getElementById(selId);
  if (!sel) return;
  const enabled = (_reviewModels || []).filter(m => m.enabled);
  const prev = sel.value;
  sel.innerHTML = `<option value="">Use default — ${esc(_defaultModelName(key))}</option>` +
    enabled.map(m => `<option value="${m.id}">${esc(_modelKindLabel(m))}</option>`).join('');
  if (prev && Array.prototype.some.call(sel.options, o => o.value === prev)) sel.value = prev;
}

// Body fragment for a request: {} ⇒ use Config default; else explicit override.
function _overrideModelBody(selId) {
  const sel = document.getElementById(selId);
  const v = sel && sel.value;
  return v ? { model_config_id: parseInt(v, 10) } : {};
}

// Repopulate every per-view override selector (after models/defaults (re)load).
function _refreshAllOverrides() {
  _populateOverrideSel('rpRevModelSel', 'arch-review');
  _populateOverrideSel('rpEnhModelSel', 'enhancement');
  _populateOverrideSel('rdRevModelSel', 'arch-review');
  _populateOverrideSel('rdEnhModelSel', 'enhancement');
  _populateOverrideSel('biModelSel',    'uc-authoring');
  _populateOverrideSel('wzModelSel',    'uc-authoring');
  _populateOverrideSel('ucAssistPanelModelSel', 'uc-authoring');
  _populateOverrideSel('nrModelSel',    '__engine__');
}

// Back-compat name — older call sites invoke this to refresh the arch/enh
// pickers; it now refreshes every override selector.
async function _updateArchModelInfo() {
  if (!_reviewModels || !_reviewModels.length) { try { await loadReviewModels(); } catch(e){} }
  if (!_archDefaultLoaded) { try { await loadArchDefault(); } catch(e){} }
  _refreshAllOverrides();
}

// Generic Config "default model" selector populate — one consistent component
// for every model-use. gateField (optional) limits eligibility (e.g. arch
// review requires use_arch_review=true).
function _populateDefaultSel(selId, modelId, gateField) {
  const sel = document.getElementById(selId);
  if (!sel) return;
  const eligible = (_reviewModels || []).filter(m => m.enabled && (!gateField || m[gateField]));
  sel.innerHTML = '<option value="">— no default set —</option>' +
    eligible.map(m => `<option value="${m.id}">${esc(_modelKindLabel(m))}</option>`).join('');
  if (modelId) sel.value = String(modelId);
}

// Generic save for a Config default selector → model_defaults[key] (server-side).
async function _saveModelDefault(key, selId, msgId, btnId) {
  const sel = document.getElementById(selId);
  const val = sel ? sel.value : '';
  const msgEl = document.getElementById(msgId);
  const btn = document.getElementById(btnId);
  if (val === '__custom__') {
    if (msgEl) { msgEl.textContent = 'Custom endpoints cannot be a project default — register the endpoint in Model Endpoints first.'; msgEl.style.color = 'var(--red)'; }
    return;
  }
  const modelId = val ? parseInt(val, 10) : null;
  if (btn) btn.disabled = true;
  if (msgEl) msgEl.textContent = '';
  try {
    await api(`/api/model-defaults/${key}`, {
      method: 'PUT',
      body: JSON.stringify({ model_config_id: modelId }),
    });
    _modelDefaults[key] = modelId;
    if (key === 'arch-review') _archDefaultModelId = modelId;
    _refreshAllOverrides();   // every override's "Use default — <name>" reflects it
    if (msgEl) {
      msgEl.textContent = modelId ? 'Default saved' : 'Default cleared';
      msgEl.style.color = 'var(--green)';
      setTimeout(() => { if (msgEl) { msgEl.textContent = ''; msgEl.style.color = ''; } }, 2500);
    }
  } catch(e) {
    if (msgEl) { msgEl.textContent = 'Save failed: ' + e.message; msgEl.style.color = 'var(--red)'; }
  }
  if (btn) btn.disabled = false;
}

document.getElementById('saveArchDefaultBtn').addEventListener('click',
  () => _saveModelDefault('arch-review', 'archDefaultModelSel', 'archDefaultMsg', 'saveArchDefaultBtn'));
document.getElementById('saveEnhDefaultBtn')?.addEventListener('click',
  () => _saveModelDefault('enhancement', 'enhDefaultModelSel', 'enhDefaultMsg', 'saveEnhDefaultBtn'));
document.getElementById('saveAssessIngestDefaultBtn')?.addEventListener('click',
  () => _saveModelDefault('assessment-ingest', 'assessIngestDefaultModelSel', 'assessIngestDefaultMsg', 'saveAssessIngestDefaultBtn'));

// ── MCP refresh (Config panel: Pipeline Sources → MCP refresh) ────────────────

function _mcpRefreshFmtRolloutChip(rollout) {
  if (!rollout) return '—';
  const ready = rollout.ready_replicas || 0;
  const desired = rollout.replicas || 0;
  const updated = rollout.updated_replicas || 0;
  const phase = (ready === desired && updated === desired && desired > 0)
    ? `<span style="color:var(--ok)">stable</span>`
    : `<span style="color:var(--accent)">rolling</span>`;
  return `${phase} (${ready}/${desired} ready, ${updated} updated)`;
}

async function loadCorpusCacheStatus() {
  const el = document.getElementById('corpusCacheStatus');
  if (!el) return;
  try {
    const s = await api('/api/corpus/sync-status');
    const r = s.result || {};
    const age = s.age_seconds;
    const ago = (age == null) ? 'pending'
      : age < 90 ? `${age}s ago` : age < 5400 ? `${Math.round(age/60)}m ago` : `${(age/3600).toFixed(1)}h ago`;
    const repos = (r.repos || []).map(x => x.error ? `${x.namespace} ⚠` : `${x.namespace} ${x.files}`).join(' · ');
    el.textContent = `cache: ${r.files_seen != null ? r.files_seen + ' files' : '—'}${repos ? ' (' + repos + ')' : ''} · synced ${ago}`;
  } catch(e) { el.textContent = ''; }
}
async function resyncCorpusCache() {
  const btn = document.getElementById('corpusResyncBtn');
  if (btn) { btn.disabled = true; btn.textContent = '↻ Resyncing…'; }
  try {
    const r = await api('/api/corpus/resync', {method:'POST'});
    toast(`Corpus cache resynced — ${r.files_seen} files, ${r.pruned} pruned`);
  } catch(e) { toast(e.message, true); }
  finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Resync corpus cache'; }
    loadCorpusCacheStatus();
  }
}
document.getElementById('corpusResyncBtn')?.addEventListener('click', resyncCorpusCache);

async function loadMCPRefreshStatus() {
  try {
    const s = await api('/api/mcp/refresh-status');
    document.getElementById('mcpRefreshWhen').textContent =
      s.last_pod_restart_at || s.last_refreshed_at || 'never';
    document.getElementById('mcpRefreshSource').textContent =
      s.last_pod_restart_reason || s.last_refreshed_source || '—';
    document.getElementById('mcpRefreshWho').textContent =
      s.last_refreshed_by || '—';
    document.getElementById('mcpRefreshRollout').innerHTML =
      _mcpRefreshFmtRolloutChip(s.rollout);
    document.getElementById('mcpRefreshStatus').textContent =
      s.rollout && s.rollout.ready_replicas === s.rollout.replicas && s.rollout.replicas > 0
        ? 'ready' : 'rolling';
  } catch (e) {
    document.getElementById('mcpRefreshStatus').textContent = 'status read failed';
  }
}

document.getElementById('mcpRefreshNowBtn').addEventListener('click', async () => {
  const btn = document.getElementById('mcpRefreshNowBtn');
  const msgEl = document.getElementById('mcpRefreshMsg');
  if (!confirm('Refresh the MCP now? This rolls the dav-docs-mcp pod (~30-60s) and the MCP is briefly unavailable.')) return;
  btn.disabled = true; msgEl.textContent = 'triggering…';
  try {
    const r = await api('/api/mcp/refresh-now', { method: 'POST', body: '{}' });
    msgEl.textContent = `triggered at ${r.triggered_at} by ${r.triggered_by}`;
    msgEl.style.color = 'var(--green)';
    setTimeout(() => loadMCPRefreshStatus(), 1500);
    setTimeout(() => { msgEl.textContent = ''; msgEl.style.color = ''; }, 4000);
  } catch (e) {
    msgEl.textContent = 'refresh failed: ' + (e.message || e);
    msgEl.style.color = 'var(--red)';
  }
  btn.disabled = false;
});

document.getElementById('mcpRefreshReloadBtn').addEventListener('click', () => loadMCPRefreshStatus());

// ── Model Browser ─────────────────────────────────────────────────────────────

let _mbContext = null; // { selId, storageKey }

function _uniqueEndpoints() {
  const seen = new Set();
  return _reviewModels.filter(m => m.enabled && m.endpoint_url).filter(m => {
    if (seen.has(m.endpoint_url)) return false;
    seen.add(m.endpoint_url); return true;
  });
}

function _openModelBrowser(selId, storageKey) {
  _mbContext = { selId, storageKey };
  const epSel = document.getElementById('mbEndpointSel');
  const eps = _uniqueEndpoints();
  epSel.innerHTML = eps.map(m => `<option value="${esc(m.endpoint_url)}">${esc(m.endpoint_url)}</option>`).join('')
    + '<option value="__custom__">Custom…</option>';
  // Pre-select endpoint based on current selector value
  const sel = document.getElementById(selId);
  if (sel && sel.value === '__custom__') {
    const ep = localStorage.getItem(storageKey + '_ep') || '';
    const matched = eps.find(e => e.endpoint_url === ep);
    epSel.value = matched ? ep : '__custom__';
    if (!matched) document.getElementById('mbCustomEp').value = ep;
  } else if (sel && sel.value && sel.value !== '') {
    const m = _reviewModels.find(r => String(r.id) === sel.value);
    if (m && m.endpoint_url && eps.find(e => e.endpoint_url === m.endpoint_url)) epSel.value = m.endpoint_url;
  }
  _mbUpdateCustomEpRow();
  document.getElementById('mbModelSel').innerHTML = '<option value="">— probe to list models —</option>';
  document.getElementById('mbManualModel').value = localStorage.getItem(storageKey + '_mi') || '';
  document.getElementById('mbProbeStatus').textContent = '';
  const overlay = document.getElementById('modelBrowserOverlay');
  overlay.style.display = 'flex';
  _mbProbe();
}

function _mbUpdateCustomEpRow() {
  const isCustom = document.getElementById('mbEndpointSel').value === '__custom__';
  document.getElementById('mbCustomEpRow').style.display = isCustom ? '' : 'none';
}

function _mbGetEndpoint() {
  const epSel = document.getElementById('mbEndpointSel');
  return epSel.value === '__custom__'
    ? document.getElementById('mbCustomEp').value.trim()
    : epSel.value;
}

async function _mbProbe() {
  const ep = _mbGetEndpoint();
  const statusEl = document.getElementById('mbProbeStatus');
  const btn = document.getElementById('mbProbeBtn');
  const modelSel = document.getElementById('mbModelSel');
  if (!ep) {
    modelSel.innerHTML = '<option value="">— probe to list models —</option>';
    statusEl.textContent = '';
    return;
  }
  btn.disabled = true; statusEl.textContent = 'Probing…';
  try {
    const result = await api(`/api/sources/inference/models?endpoint=${encodeURIComponent(ep)}`);
    const models = result.models || [];
    if (result.error && !result.reachable) {
      modelSel.innerHTML = '<option value="">— probe failed —</option>';
      statusEl.textContent = result.error;
    } else if (!models.length) {
      modelSel.innerHTML = '<option value="">— no models returned —</option>';
      statusEl.textContent = result.error || 'No models found at this endpoint';
    } else {
      modelSel.innerHTML = '<option value="">— select —</option>' +
        models.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
      statusEl.textContent = `${models.length} model${models.length === 1 ? '' : 's'} found`;
    }
  } catch(e) {
    statusEl.textContent = 'Probe failed: ' + e.message;
  }
  btn.disabled = false;
}

document.getElementById('mbEndpointSel').addEventListener('change', () => {
  _mbUpdateCustomEpRow();
  _mbProbe();
});

document.getElementById('mbProbeBtn').addEventListener('click', _mbProbe);

document.getElementById('mbModelSel').addEventListener('change', function() {
  if (this.value) document.getElementById('mbManualModel').value = this.value;
});

document.getElementById('mbUseBtn').addEventListener('click', () => {
  const ep = _mbGetEndpoint();
  const statusEl = document.getElementById('mbProbeStatus');
  if (!ep) { statusEl.textContent = 'Enter an endpoint URL'; return; }
  const modelId = document.getElementById('mbManualModel').value.trim()
               || document.getElementById('mbModelSel').value;
  if (!modelId) { statusEl.textContent = 'Select or enter a model ID'; return; }
  const { selId, storageKey } = _mbContext;
  // Check if a registered model_config already covers this endpoint+model
  const match = _reviewModels.find(m => m.enabled && m.endpoint_url === ep && m.model_id === modelId);
  const sel = document.getElementById(selId);
  if (match) {
    sel.value = String(match.id);
    localStorage.setItem(storageKey, String(match.id));
    // Clean up custom overrides if they were set for this selector
    localStorage.removeItem(storageKey + '_ep');
    localStorage.removeItem(storageKey + '_mi');
  } else {
    localStorage.setItem(storageKey + '_ep', ep);
    localStorage.setItem(storageKey + '_mi', modelId);
    localStorage.setItem(storageKey, '__custom__');
    let opt = sel.querySelector('option[value="__custom__"]');
    if (!opt) { opt = document.createElement('option'); opt.value = '__custom__'; sel.appendChild(opt); }
    opt.textContent = `Custom: ${modelId}`;
    sel.value = '__custom__';
  }
  document.getElementById('modelBrowserOverlay').style.display = 'none';
  _mbContext = null;
});

document.getElementById('mbCancelBtn').addEventListener('click', () => {
  document.getElementById('modelBrowserOverlay').style.display = 'none';
  _mbContext = null;
});

document.getElementById('modelBrowserOverlay').addEventListener('click', function(e) {
  if (e.target === this) { this.style.display = 'none'; _mbContext = null; }
});

// Re-populate EVERY model selector after the model list changes (add / edit / delete),
// so a newly added endpoint shows up in all the default + override pickers below without
// a page refresh. Function declarations are hoisted, so the later loaders resolve fine.
async function _refreshModelSelectors() {
  await loadReviewModels();                          // refresh _reviewModels
  renderModelList();                                 // the Config model list
  _updateArchModelInfo();                            // rpRev/rpEnh/rdRev/rdEnh/bi/wz/ucAssistPanel/nr override sels
  try { await loadUCAssistConfig(); } catch (_) {}   // ucAssistModelSel
  try { await loadEvalDefault();   } catch (_) {}    // evalDefaultModelSel
  try { await loadArchDefault();   } catch (_) {}    // archDefaultModelSel + enhDefaultModelSel
  _populateUCAssistModelSel();
}

document.getElementById('addModelBtn').addEventListener('click', () => {
  _mfEditId = null;
  document.getElementById('modelFormTitle').textContent = 'Add model endpoint';
  document.getElementById('mfName').value = '';
  document.getElementById('mfProvider').value = 'openai';
  document.getElementById('mfEndpoint').value = '';
  document.getElementById('mfModelId').value = '';
  document.getElementById('mfApiKey').value = '';
  document.getElementById('mfLocal').checked = false;
  document.getElementById('mfEnabled').checked = true;
  document.getElementById('modelFormMsg').textContent = '';
  _mfResetProbe();
  document.getElementById('modelFormCard').style.display = '';
  document.getElementById('mfName').focus();
});

document.getElementById('cancelModelBtn').addEventListener('click', () => {
  document.getElementById('modelFormCard').style.display = 'none';
});

// ── Probe an endpoint for its models (connection test + model list) ───────────
// Hits POST /api/models/probe with the form's endpoint/provider/key (same URL+auth
// convention the generation code uses), then offers the discovered models for
// selection — picking one fills the Model ID field. Manual entry still works.
function _mfResetProbe() {
  const sel = document.getElementById('mfModelSel');
  const st  = document.getElementById('mfProbeStatus');
  if (sel) { sel.style.display = 'none'; sel.innerHTML = ''; }
  if (st)  { st.textContent = ''; st.style.color = 'var(--text-faint)'; }
}
async function _mfProbeModels() {
  const btn = document.getElementById('mfProbeBtn');
  const st  = document.getElementById('mfProbeStatus');
  const sel = document.getElementById('mfModelSel');
  const endpoint = document.getElementById('mfEndpoint').value.trim();
  const provider = document.getElementById('mfProvider').value;
  const apiKey   = document.getElementById('mfApiKey').value;
  if (!endpoint) { st.style.color = 'var(--red)'; st.textContent = 'Enter the endpoint URL first.'; return; }
  btn.disabled = true; sel.style.display = 'none';
  st.style.color = 'var(--text-faint)'; st.textContent = 'Probing…';
  try {
    const r = await api('/api/models/probe', { method:'POST',
      body: JSON.stringify({ provider, endpoint_url: endpoint, api_key: apiKey }) });
    const models = r.models || [];
    const ms = r.latency_ms != null ? ` (${r.latency_ms} ms)` : '';
    if (!r.reachable) {
      st.style.color = 'var(--red)';
      st.textContent = '✗ ' + (r.error || ('HTTP ' + (r.status_code || '?')));
    } else if (!models.length) {
      st.style.color = 'var(--amber,gold)';
      st.textContent = `Connected${ms} but no models listed — enter the Model ID manually.`;
    } else {
      st.style.color = 'var(--green)';
      st.textContent = `✓ Connected${ms} — ${models.length} model${models.length === 1 ? '' : 's'}`;
      const cur = document.getElementById('mfModelId').value.trim();
      sel.innerHTML = '<option value="">— select a model —</option>'
        + models.map(m => `<option value="${esc(m)}"${m === cur ? ' selected' : ''}>${esc(m)}</option>`).join('');
      sel.style.display = '';
      if (!cur && models.length === 1) { sel.value = models[0]; document.getElementById('mfModelId').value = models[0]; }
    }
  } catch (e) {
    st.style.color = 'var(--red)'; st.textContent = 'Probe failed: ' + e.message;
  } finally { btn.disabled = false; }
}
document.getElementById('mfProbeBtn')?.addEventListener('click', _mfProbeModels);
document.getElementById('mfModelSel')?.addEventListener('change', function () {
  if (this.value) document.getElementById('mfModelId').value = this.value;
});
// Re-probing is needed if the endpoint/provider changes after a probe — drop stale model list.
document.getElementById('mfEndpoint')?.addEventListener('input', _mfResetProbe);
document.getElementById('mfProvider')?.addEventListener('change', _mfResetProbe);

document.getElementById('saveModelBtn').addEventListener('click', async () => {
  const btn = document.getElementById('saveModelBtn');
  const msg = document.getElementById('modelFormMsg');
  const payload = {
    name:         document.getElementById('mfName').value.trim(),
    provider:     document.getElementById('mfProvider').value,
    endpoint_url: document.getElementById('mfEndpoint').value.trim(),
    model_id:     document.getElementById('mfModelId').value.trim(),
    api_key:      document.getElementById('mfApiKey').value,
    is_local:     document.getElementById('mfLocal').checked,
    enabled:      document.getElementById('mfEnabled').checked,
  };
  if (!payload.name || !payload.endpoint_url || !payload.model_id) {
    msg.style.color = 'var(--red)'; msg.textContent = 'Name, endpoint, and model ID are required.'; return;
  }
  btn.disabled = true; msg.style.color = 'var(--text-faint)'; msg.textContent = 'Saving…';
  try {
    if (_mfEditId) {
      await api(`/api/models/${_mfEditId}`, { method:'PUT', body: JSON.stringify(payload) });
    } else {
      await api('/api/models', { method:'POST', body: JSON.stringify(payload) });
    }
    await _refreshModelSelectors();
    document.getElementById('modelFormCard').style.display = 'none';
    toast(_mfEditId ? 'Model updated' : 'Model added');
  } catch(e) {
    msg.style.color = 'var(--red)'; msg.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

function editModel(id) {
  const m = _reviewModels.find(r => r.id === id);
  if (!m) return;
  _mfEditId = id;
  document.getElementById('modelFormTitle').textContent = 'Edit model endpoint';
  document.getElementById('mfName').value = m.name;
  document.getElementById('mfProvider').value = m.provider;
  document.getElementById('mfEndpoint').value = m.endpoint_url;
  document.getElementById('mfModelId').value = m.model_id;
  document.getElementById('mfApiKey').value = '';
  document.getElementById('mfLocal').checked = m.is_local;
  document.getElementById('mfEnabled').checked = m.enabled;
  document.getElementById('modelFormMsg').textContent = '';
  _mfResetProbe();
  document.getElementById('modelFormCard').style.display = '';
  document.getElementById('mfName').focus();
}

async function deleteModel(id, btn) {
  if (!btn) return;
  _armDeleteBtn(btn, async () => {
    try {
      await api(`/api/models/${id}`, { method:'DELETE' });
      await _refreshModelSelectors();
      toast('Model deleted');
    } catch(e) {
      toast('Delete failed: ' + e.message);
    }
  });
}

// ── Review & Plan drawer ─────────────────────────────────────────────────────

let _reviewCtx = {};          // {runId, ucUuid}
const _rdRevRaw = { text: '' };
const _rdEnhRaw = { text: '' };

function rdSwitchTab(tab) {
  const isRun = tab === 'run';
  const runBody = document.getElementById('rdRunBody');
  const revBody = document.getElementById('rdReviewBody');
  if (runBody) runBody.style.display    = isRun ? '' : 'none';
  if (revBody) revBody.style.display    = isRun ? 'none' : '';
  const tabRun = document.getElementById('rdTabRun');
  const tabRev = document.getElementById('rdTabReview');
  if (tabRun) tabRun.classList.toggle('active', isRun);
  if (tabRev) tabRev.classList.toggle('active', !isRun);
  if (!isRun) _updateArchModelInfo();   // reflect the Config arch-review default
}

function openReviewPane(scope, ucUuid, startAt = 'review') {
  // Navigate to top-level Review & Plan tab
  const runId = activeRunResultId;
  if (!runId) { toast('Select an analysis in the Results tab first'); return; }

  // Pre-populate the Review & Plan tab
  _reviewCtx = { runId, ucUuid: ucUuid || null };
  // Honor the requested scope: a per-UC action (scope='uc') must generate a UC-scoped
  // review, not silently fall back to the whole set (the retired hardcode did the latter).
  try { _rpSetScopeReq(scope, ucUuid); } catch (_) {}
  switchView('review');

  // Give the tab a moment to render, then set up
  setTimeout(() => {
    // Select run in the dropdown
    const rpRunSel = document.getElementById('rpRunSel');
    if (rpRunSel) {
      rpRunSel.value = runId;
      if (!rpRunSel.value) {
        // Option not present yet — try populating
        loadReviewTab().then(() => { rpRunSel.value = runId; _rpPopulateUCs(runId); });
      } else {
        _rpPopulateUCs(runId);
      }
    }
    // Set scope and UC
    if (ucUuid) {
      const scopeUC = document.getElementById('rpScopeUC');
      if (scopeUC) { scopeUC.checked = true; }
      const rpUCSel = document.getElementById('rpUCSel');
      if (rpUCSel) { rpUCSel.disabled = false; rpUCSel.value = ucUuid; }
      // Show a note
      const link = document.getElementById('rpUCLink');
      const ucEntry = (activeRunSummary?.ucs||[]).find(u=>u.uc_uuid===ucUuid);
      if (link) { link.textContent = `UC: ${ucEntry?.uc_handle||ucUuid}`; link.style.display = ''; }
    }
    _updateArchModelInfo();
  }, 50);
}
'use strict';

const API = '';

// ── API helpers ──────────────────────────────────────────────
let _activeProject = (() => { try { return localStorage.getItem('davActiveProject') || ''; } catch { return ''; } })();
// View-mode backstop (#136): in read-only View mode, refuse mutating requests client-side, so
// even an un-gated control can't change anything. GET/HEAD always pass; a few utility writes are
// exempt (auth, the settings toggle itself, a model connection-probe, presence heartbeat).
const _VIEWMODE_SAFE = ['/api/auth/', '/api/me/settings', '/api/models/probe', '/api/presence'];
async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  if (typeof _viewMode !== 'undefined' && _viewMode && method !== 'GET' && method !== 'HEAD'
      && !_VIEWMODE_SAFE.some(p => path.startsWith(p))) {
    try { toast('View mode is read-only — switch to Editing to make changes', true); } catch {}
    const err = new Error('View mode is read-only'); err.viewModeBlocked = true; err.status = 0;
    throw err;
  }
  const res = await fetch(API + path, {
    headers: {
      'Content-Type': 'application/json',
      ...(_activeProject ? { 'X-DAV-Project': _activeProject } : {}),
      ...(opts.headers || {}),
    },
    ...opts,
  });
  if (!res.ok) {
    const txt = await res.text();
    const err = new Error(`${res.status}: ${txt}`);
    err.status = res.status;
    // Try to parse JSON body so callers can branch on structured detail
    // (FastAPI HTTPException with a dict detail surfaces as {detail: {...}}).
    try { err.body = JSON.parse(txt); } catch { err.body = null; }
    // 401 on a non-auth call means the session is no longer valid (account
    // deleted/disabled, or never authenticated) — show the login screen so
    // access is visibly cut off immediately.
    if (res.status === 401 && !path.startsWith('/api/auth/')) {
      const ov = document.getElementById('loginOverlay'); if (ov) ov.style.display = 'flex';
    }
    throw err;
  }
  // 204 No Content (e.g. DELETE endpoints) and any empty body have no JSON to
  // parse — res.json() would throw on them, surfacing a spurious "failed" toast
  // even though the call succeeded. Treat no-body as a successful null result.
  if (res.status === 204) return null;
  const txt = await res.text();
  if (!txt) return null;
  try { return JSON.parse(txt); } catch { return txt; }
}

// ── Auto-follow helper ──────────────────────────────────────────────────────
// Standard "follow new content as it streams" behavior shared by every tail
// pane in the UI (prompts/responses today; future streams should reuse this).
// Contract:
//   - scrolling up disables auto-follow (state := false, button reflects)
//   - clicking the button re-enables auto-follow AND jumps to the bottom
//   - button is "filled" (dark accent background) when active, "outline" (grey)
//     when inactive — consistent visual language across all auto-follow tails
//   - the caller is responsible for actually scrolling to the bottom when new
//     content arrives, gated on get() returning true. This helper only owns
//     state + button + scroll-away detection.
const _AUTO_FOLLOW_BOTTOM_SLACK = 24;   // px from bottom that still counts as "at bottom"
function _renderAutoFollowBtn(btn, on) {
  btn.classList.toggle('auto-follow-active', on);
  // Inline styles (the codebase doesn't use a stylesheet entry for this btn).
  btn.style.background = on ? 'var(--accent)'      : 'transparent';
  btn.style.color      = on ? 'var(--bg-panel)'    : 'var(--text-faint)';
  btn.style.borderColor = on ? 'var(--accent)'     : 'var(--border)';
  btn.title = on ? 'Auto-follow ON — click to pause (or just scroll up)'
                 : 'Auto-follow OFF — click to resume and jump to bottom';
}
function _setupAutoFollow(scrollEl, btn, get, set) {
  if (!scrollEl || !btn) return;
  _renderAutoFollowBtn(btn, get());
  btn.addEventListener('click', e => {
    e.stopPropagation();
    const newState = !get();
    set(newState);
    _renderAutoFollowBtn(btn, newState);
    if (newState) {
      // Resuming → snap to bottom
      scrollEl.scrollTop = scrollEl.scrollHeight;
    }
  });
  scrollEl.addEventListener('scroll', () => {
    const dist = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight;
    const atBottom = dist <= _AUTO_FOLLOW_BOTTOM_SLACK;
    if (get() && !atBottom) {
      // User scrolled away from the bottom → pause
      set(false);
      _renderAutoFollowBtn(btn, false);
    } else if (!get() && atBottom) {
      // User scrolled BACK to the bottom → re-engage
      set(true);
      _renderAutoFollowBtn(btn, true);
    }
  });
}

// ── R3: PR-create wrapper that handles the approval gate ────────────────────
// Centralized so both PR-create button paths (rpPrCreateBtn / rdRevPrCreateBtn)
// share the same gate UX. POSTs the payload; on 409 with detail==approval_gate,
// opens the override modal and re-submits with override+reason on confirm.
async function createPrWithApprovalGate(payload) {
  try {
    return await api('/api/pr/create', { method: 'POST', body: JSON.stringify(payload) });
  } catch (e) {
    const gate = e.status === 409 && e.body
      && (e.body.detail?.detail === 'approval_gate' ? e.body.detail
          : (e.body.detail === 'approval_gate' ? e.body : null));
    if (!gate) throw e;
    // Show override modal; wait for user decision
    const reason = await _showApprovalGateModal(gate.non_approved || []);
    if (!reason) return null;   // user cancelled
    return await api('/api/pr/create', {
      method: 'POST',
      body: JSON.stringify({ ...payload, override: true, override_reason: reason }),
    });
  }
}
function _showApprovalGateModal(nonApproved) {
  return new Promise(resolve => {
    const modal = document.getElementById('approvalGateModal');
    const list = document.getElementById('approvalGateList');
    const reasonEl = document.getElementById('approvalGateReason');
    const status = document.getElementById('approvalGateStatus');
    reasonEl.value = ''; status.textContent = '';
    const stateColors = { draft:'var(--text-faint)', ready:'var(--blue)', in_review:'var(--accent)', approved:'var(--green)', deprecated:'var(--red)' };
    list.innerHTML = nonApproved.length
      ? nonApproved.map(n => {
          const c = stateColors[n.state || 'draft'] || 'var(--text-faint)';
          return `<div style="display:flex;align-items:center;gap:8px;padding:3px 0;">
            <span style="font-size:9px;text-transform:uppercase;color:${c};border:1px solid ${c};padding:0 4px;border-radius:2px;flex-shrink:0;">${esc(n.state || 'draft')}</span>
            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(n.uc_handle || n.uc_uuid)}</span>
          </div>`;
        }).join('')
      : '<em>(no UCs returned — server logic edge case)</em>';
    modal.classList.add('open');
    const cleanup = () => {
      modal.classList.remove('open');
      document.getElementById('approvalGateConfirm').onclick = null;
      document.getElementById('approvalGateCancel').onclick = null;
      document.getElementById('closeApprovalGateModal').onclick = null;
    };
    document.getElementById('approvalGateConfirm').onclick = () => {
      const r = (reasonEl.value || '').trim();
      if (!r) { status.textContent = 'Reason is required to override the gate.'; status.style.color = 'var(--red)'; return; }
      cleanup(); resolve(r);
    };
    document.getElementById('approvalGateCancel').onclick = () => { cleanup(); resolve(null); };
    document.getElementById('closeApprovalGateModal').onclick = () => { cleanup(); resolve(null); };
    setTimeout(() => reasonEl.focus(), 50);
  });
}

// ── Toast ────────────────────────────────────────────────────
function toast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.toggle('error', !!isError); t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2800);
}

// ── Utilities ────────────────────────────────────────────────
function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
// Canonical tab wiring (docs/ui-style-guide.md). Within `container`, clicking a
// .tab[data-tab=X] activates it + the .tabpanel[data-tabpanel=X] sibling, hiding the
// rest. Idempotent; call after rendering the tab strip. `onChange(key)` is optional.
function wireTabs(tabsEl, opts) {
  opts = opts || {};
  if (!tabsEl) return;
  // Panels may be siblings of the tab strip (e.g. Config), so search the enclosing view.
  const root = opts.panelsRoot || tabsEl.closest('.pf-view') || tabsEl.parentElement || document;
  const tabs = Array.from(tabsEl.querySelectorAll('.tab'));
  const panels = Array.from(root.querySelectorAll('.tabpanel'));
  const show = (key) => {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === key));
    panels.forEach(p => p.classList.toggle('active', p.dataset.tabpanel === key));
    if (typeof opts.onChange === 'function') { try { opts.onChange(key); } catch (e) { console.warn('tab onChange', e); } }
  };
  tabs.forEach(t => { if (!t._tabWired) { t._tabWired = true; t.addEventListener('click', () => show(t.dataset.tab)); } });
  // Keep a VISIBLE tab active (re-pick if role-gating just hid the active one).
  const visible = tabs.filter(t => t.style.display !== 'none');
  const cur = visible.find(t => t.classList.contains('active')) || visible[0] || tabs[0];
  if (cur) show(cur.dataset.tab);
}
// Show a Config section-tab only if it has ≥1 visible panel-card (respects the
// per-panel privilege gating set in _applyAccessVisibility), then (re)wire the strip.
function _syncConfigTabs() {
  const view = document.getElementById('view-config');
  const tabsEl = document.getElementById('configTabs');
  if (!view || !tabsEl) return;
  view.querySelectorAll('.tabpanel[data-tabpanel]').forEach(tp => {
    const tab = tabsEl.querySelector(`.tab[data-tab="${tp.dataset.tabpanel}"]`);
    if (!tab) return;
    const anyVisible = Array.from(tp.querySelectorAll('.panel-card')).some(p => p.style.display !== 'none');
    tab.style.display = anyVisible ? '' : 'none';
  });
  wireTabs(tabsEl);
}
// Embed JSON.stringify output safely inside a double-quoted HTML attribute
// (e.g. onclick="..."). Without &quot;-escaping, an inner `"` would terminate
// the attribute and silently kill the handler — this was a real bug (v0.9.31).
function attrJson(v) { return JSON.stringify(v).replace(/"/g, '&quot;'); }

function fmtTs(ts) {
  if (!ts) return '—';
  const d = new Date(ts), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  return d.toLocaleDateString([],{month:'short',day:'numeric'}) + ' ' +
         d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
}

function fmtDuration(startTs, endTs) {
  if (!startTs) return '—';
  const s = (endTs ? new Date(endTs) : new Date()) - new Date(startTs);
  const sec = Math.floor(s / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60), r = sec % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

// Compact duration from seconds: "45s" / "12m" / "5.3h". For estimates + timeout.
function _fmtDurShort(sec) {
  if (sec == null || !isFinite(sec)) return '—';
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 5400) return `${Math.round(sec/60)}m`;
  return `${(sec/3600).toFixed(1)}h`;
}
// Clock time from an epoch-ms (e.g. ETA): "8:35p".
function _fmtClock(ms) {
  if (!ms || !isFinite(ms)) return '—';
  const d = new Date(ms);
  let h = d.getHours(); const m = d.getMinutes();
  const ap = h >= 12 ? 'p' : 'a'; h = h % 12 || 12;
  return `${h}:${String(m).padStart(2,'0')}${ap}`;
}
// Edit a running run's "time allowed" (failsafe timeout) — extend or shorten it.
async function editRunTimeout(name, curSec) {
  const curH = curSec ? (curSec/3600).toFixed(1) : '';
  const inp = prompt(`Time allowed (hours) — the failsafe that stops this run if it runs away. Extend or shorten it; it won't cut short a run that finishes first.\n\nCurrent: ${curH}h`, curH);
  if (inp === null) return;
  const h = parseFloat(inp);
  if (!(h > 0)) { toast('Enter a positive number of hours', true); return; }
  try {
    await api(`/api/runs/${encodeURIComponent(name)}/timeout`, {method:'POST', body: JSON.stringify({seconds: Math.round(h*3600)})});
    toast(`Time allowed set to ${h}h`);
    refreshRunDrawer();
  } catch(e){ toast(e.message, true); }
}

function phaseHtml(phase) {
  const cls = 'phase-' + (phase || 'unknown').toLowerCase().replace(/\s+/g,'-');
  return `<span class="phase ${cls}"><span class="dot"></span>${esc(phase||'unknown')}</span>`;
}

function verdictClass(v) {
  if (!v) return 'verdict-error';
  if (v === 'supported') return 'verdict-supported';
  if (v.includes('partial')) return 'verdict-partial';
  if (v.includes('not_supported') || v === 'not_supported') return 'verdict-unsupported';
  return 'verdict-error';
}
function sevClass(label) { return 'sev-' + (label||'minor').toLowerCase(); }

function lcHtml(state) {
  if (!state) return '';
  const label = state.replace(/_/g,' ');
  return `<span class="lc lc-${state}">${esc(label)}</span>`;
}
// Inline lifecycle transition menu from the UC list (so changing status — incl. Reactivate
// a deprecated UC — doesn't require opening the detail pane). Reuses openLCModal + the same
// LC_TRANSITIONS map and server guard (project.usecases); blocked in View mode by api().
function _lcMenu(event, uuid, state) {
  document.querySelectorAll('.lc-menu-pop').forEach(p => p.remove());
  const trans = LC_TRANSITIONS[state] || [];
  if (!trans.length) { try { toast('No status changes available from ' + state.replace(/_/g,' ')); } catch {} return; }
  const pop = document.createElement('div');
  pop.className = 'lc-menu-pop';
  pop.style.cssText = 'position:fixed;z-index:9999;background:var(--bg-panel);border:1px solid var(--border);border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,0.35);padding:4px;min-width:160px;';
  pop.innerHTML = trans.map(t =>
    `<button class="dropdown-item" data-to="${esc(t.to)}" data-label="${esc(t.label)}" style="display:block;width:100%;text-align:left;font-size:12px;">${esc(t.label)} <span style="opacity:.5;font-size:10px;">→ ${esc(t.to.replace(/_/g,' '))}</span></button>`).join('');
  document.body.appendChild(pop);
  const r = (event.target.closest('span') || event.target).getBoundingClientRect();
  pop.style.left = Math.max(6, Math.min(r.left, window.innerWidth - 174)) + 'px';
  pop.style.top = (r.bottom + 4) + 'px';
  pop.querySelectorAll('button').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation(); pop.remove(); openLCModal(uuid, b.dataset.to, b.dataset.label);
  }));
  setTimeout(() => {
    const close = e => { if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener('click', close); } };
    document.addEventListener('click', close);
  }, 0);
}

// UC priority badge (roadmap weighting, spec 05 §6.8 / DCM feature #1).
// Color tracks urgency; the score is the roadmap weight (higher = build first).
const PRIORITY_COLORS = {
  critical: 'var(--red)', high: 'var(--accent)',
  medium:   'var(--blue)', low: 'var(--text-faint)',
};
function prioHtml(label, score) {
  if (!label) return '';
  const c = PRIORITY_COLORS[label] || 'var(--text-faint)';
  const title = `roadmap priority: ${label}` + (score != null ? ` (weight ${score})` : '');
  return `<span title="${esc(title)}" style="font-size:8px;text-transform:uppercase;letter-spacing:0.08em;`
       + `color:${c};border:1px solid ${c};padding:0 4px;border-radius:2px;flex-shrink:0;">${esc(label)}</span>`;
}

// Surfaces UC-list de-duplication: the same uuid can live in several corpus
// paths and/or also be managed. The list shows one row per uuid; this badge
// makes the collapsed copies visible. path_count = corpus paths for this uuid.
function dupHtml(u) {
  const n = u.path_count || 0;
  const ns = (u.namespaces || []).join(', ');
  if (u.source === 'managed') {
    if (n < 1) return '';
    const t = `Also present in ${n} corpus path${n===1?'':'s'}${ns ? ': ' + ns : ''}`;
    return `<span title="${esc(t)}" style="font-size:8px;color:var(--text-faint);border:1px solid var(--border);padding:0 4px;border-radius:2px;flex-shrink:0;">+${n} corpus</span>`;
  }
  if (n > 1) {
    const t = `Same UC in ${n} corpus paths${ns ? ': ' + ns : ''}`;
    return `<span title="${esc(t)}" style="font-size:8px;color:var(--text-faint);border:1px solid var(--border);padding:0 4px;border-radius:2px;flex-shrink:0;">×${n} paths</span>`;
  }
  return '';
}

// Customer-demand badge. Importance is measured by DISTINCT customers (multi-tenant
// signal), not raw requests — so one customer asking 10× doesn't inflate it. Shows the
// distinct-customer count; total requests in the tooltip; highlighted when multi-tenant.
function demandHtml(u) {
  const dc = u.distinct_customers || 0;
  const total = u.customer_requests || 0;
  if (!total && !dc) return '';
  const mt = dc > 1;
  const c = mt ? 'var(--blue)' : 'var(--text-faint)';
  const names = (u.customers || []).filter(Boolean);
  const t = `${total} request${total === 1 ? '' : 's'} from ${dc} distinct customer${dc === 1 ? '' : 's'}`
          + (mt ? ' — multi-tenant (higher importance)' : '')
          + (names.length ? `\n${names.join(', ')}` : '');
  const badge = `<span title="${esc(t)}" style="font-size:8px;color:${c};border:1px solid ${c};padding:0 4px;border-radius:2px;flex-shrink:0;">`
       + `👥 ${dc}${total > dc ? `·${total}` : ''}</span>`;
  // #130 2b-iii: customer attribution — show WHO (up to 2 name chips + overflow), not just how many.
  let chips = '';
  if (names.length) {
    chips = names.slice(0, 2).map(n =>
      `<span class="cust-chip" title="requested by ${esc(n)}" style="font-size:8px;color:var(--text-dim);background:var(--bg-raised);border:1px solid var(--border);padding:0 4px;border-radius:2px;flex-shrink:0;max-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(n)}</span>`).join('');
    if (names.length > 2) chips += `<span style="font-size:8px;color:var(--text-faint);flex-shrink:0;" title="${esc(names.join(', '))}">+${names.length - 2}</span>`;
  }
  return badge + chips;
}

// UC definition readiness badge (DCM feature #4). Band thresholds mirror the
// backend's band_for(): strong>=85, good>=70, fair>=50, else needs_work.
const READINESS_COLORS = {
  strong: 'var(--green)', good: 'var(--blue)', fair: 'var(--accent)', needs_work: 'var(--red)',
};
function readinessBand(score) {
  if (score == null) return null;
  if (score >= 85) return 'strong';
  if (score >= 70) return 'good';
  if (score >= 50) return 'fair';
  return 'needs_work';
}
function readinessHtml(score) {
  const b = readinessBand(score);
  if (!b) return '';
  const c = READINESS_COLORS[b];
  return `<span title="definition readiness: ${score}/100 (${esc(b.replace('_',' '))})" style="font-size:8px;`
       + `color:${c};border:1px solid ${c};padding:0 4px;border-radius:2px;flex-shrink:0;">rdy ${score}</span>`;
}

const LC_TRANSITIONS = {
  draft:      [{to:'ready',      label:'Submit for review', cls:'btn ghost'}],
  ready:      [{to:'in_review',  label:'Start review',      cls:'btn primary'},
               {to:'draft',      label:'Back to draft',     cls:'btn ghost'}],
  in_review:  [{to:'approved',   label:'Approve',           cls:'btn success'},
               {to:'ready',      label:'Send back',         cls:'btn ghost'}],
  approved:   [{to:'in_review',  label:'Return to review',  cls:'btn ghost'},
               {to:'deprecated', label:'Deprecate',         cls:'btn danger'}],
  deprecated: [{to:'draft',      label:'Reactivate',        cls:'btn ghost'}],
};

// ── State ────────────────────────────────────────────────────
let me = { reviewer: null, authenticated: false };
let allRuns = [];
const _selectedRuns = new Set(); // run names checked for batch archive/delete
let allResults = [];
let allUCs = [];
let allSets = [];
const ALL_SET_ID = '__all__';  // synthetic "All Use Cases" set — string sentinel, never 0 (falsy-0 kept causing silent breakage)

let activeRunResultId = null;
let activeRunSummary  = null;
let _rdShallowByUuid  = {};     // uc_uuid -> shallowness row (advisory grounding signal, #45a)
let _rdShallowSummary = null;   // run-level shallowness rollup
let activeUCResult    = null;
let _lastAnalysisData = null;
let activeUCId        = null;
let editingUCId       = null;

let activeSetId       = null;
let editingSetId      = null;

let lcPendingUCId   = null;
let lcPendingTo     = null;
let addMemberSetId  = null;
let selectedMember  = null;
let promoteSetId    = null;

let ucStateFilter = '';
let ucAssignFilter = '';        // '' | 'unassigned' | 'assigned' (unified with Scoping Sets palette)
let ucHealthFilter = '';        // '' | 'invalid' | 'valid' (#122 — flags UCs failing engine validation)
let ucSortByPriority = false;   // DCM feature #1: toggle roadmap-weight ordering
let ucPriorityFilter = '';      // '' = all; else critical/high/medium/low
let ucTagFilter = '';           // #244 tag facet: '' = any; else EXACT tag match
let _ucPoolMode = false;        // #43: list is showing the "available to apply" pool (managed UCs from other projects)

// ── Init ─────────────────────────────────────────────────────
// If the URL carries ?invite=<token>, show the accept overlay and short-circuit
// the normal app boot (the invitee isn't logged in yet).
async function _maybeHandleInvite() {
  const token = new URLSearchParams(location.search).get('invite');
  if (!token) return false;
  const ov = document.getElementById('inviteAcceptOverlay');
  if (ov) ov.style.display = 'flex';
  const info = document.getElementById('inviteAcceptInfo');
  try {
    const inv = await api(`/api/invites/${encodeURIComponent(token)}`);
    info.innerHTML = `You've been invited${inv.project_name ? ` to <strong>${esc(inv.project_name)}</strong>` : ''} as <code>${esc(inv.email)}</code>.`;
    if (!inv.sessions_enabled) {
      info.innerHTML += '<br><span style="color:var(--red)">App sessions aren\'t configured yet — contact your admin.</span>';
      return true;
    }
    document.getElementById('inviteAcceptForm').style.display = '';
    document.getElementById('inviteName').value = inv.display_name || '';
    document.getElementById('inviteAcceptBtn').addEventListener('click', async () => {
      const pw = document.getElementById('invitePw').value;
      const name = document.getElementById('inviteName').value;
      const msg = document.getElementById('inviteAcceptMsg');
      if ((pw || '').length < 8) { msg.textContent = 'Password must be at least 8 characters'; msg.style.color = 'var(--red)'; return; }
      msg.textContent = 'Joining…'; msg.style.color = 'var(--text-faint)';
      try {
        await api(`/api/invites/${encodeURIComponent(token)}/accept`, { method:'POST', body: JSON.stringify({ password: pw, display_name: name }) });
        location.href = location.pathname;   // reload into the logged-in app
      } catch(e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
    });
  } catch(e) {
    info.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  }
  return true;
}

async function _submitLogin() {
  const email = document.getElementById('loginEmail').value;
  const pw = document.getElementById('loginPw').value;
  const msg = document.getElementById('loginMsg');
  msg.textContent = 'signing in…'; msg.style.color = 'var(--text-faint)';
  try { await api('/api/auth/login', { method:'POST', body: JSON.stringify({ email, password: pw }) }); location.reload(); }
  catch(e){ msg.textContent = e.message; msg.style.color = 'var(--red)'; }
}
document.getElementById('loginBtn')?.addEventListener('click', _submitLogin);
['loginEmail','loginPw'].forEach(id => document.getElementById(id)?.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); _submitLogin(); }
}));

async function init() {
  if (await _maybeHandleInvite()) return;
  initTheme();
  initNavCollapse();
  renderDomainRail();   // build the domain rail from DOMAINS before focus/RBAC/switchView run
  try { me = await api('/api/me'); } catch { me = { authenticated:false }; }
  // Not signed in (relaxed-proxy mode) → show the sign-in screen and stop.
  if (me && me.authenticated === false) {
    document.getElementById('loginOverlay').style.display = 'flex';
    return;
  }
  // Signed in but not yet approved (e.g. an OCP user an admin hasn't enabled).
  if (me && me.authenticated && me.approved === false) {
    const ov = document.getElementById('loginOverlay');
    document.getElementById('loginTitle').textContent = 'Account pending approval';
    document.getElementById('loginBody').innerHTML =
      `<div style="font-size:12px;color:var(--text-dim);">Signed in as <code>${esc(me.reviewer||'')}</code>, but your account isn't enabled yet. Ask a platform admin to approve you.</div>
       <button class="btn ghost" style="width:100%;margin-top:14px;" onclick="api('/api/auth/logout',{method:'POST'}).then(()=>location.reload())">Sign out</button>`;
    ov.style.display = 'flex';
    return;
  }
  try { await Promise.all([loadMe(), loadRunStatus()]); setApiStatus(true); }
  catch (e) { console.error(e); setApiStatus(false, e.message); }
  _loadUserSettings();   // #129: pull server-side prefs (theme/persona/view-mode/nav) + re-apply
  // Pre-load models + project model defaults so every default-aware override
  // selector ("Use default — <name>") is ready on any tab.
  loadReviewModels().then(async () => {
    await loadArchDefault();      // _modelDefaults + Config default selectors + _refreshAllOverrides
    _populateUCAssistModelSel();  // UC-authoring status chip + eval default
  }).catch(() => {});
  loadProjectSwitcher();   // masthead active-project selector
  try { populateCustomerSel(); } catch (_) {}   // matrix #130: masthead customer axis
  _setupRbacViews();       // relocate Users/Projects panels into their own views
  // Land on the active persona's first domain. _persona is resolved by now (loadMe →
  // _applyPersona already homed the rail); re-assert it here to be safe.
  { const _h = _personaDomains()[0]; if (_h) switchDomain(_h.key); }
  // Load the run list on boot so the masthead run selector is ready on every tab
  // (same archived-aware source as the Runs tab).
  loadRuns();
  loadFreshness();   // masthead analysis-freshness chip (#112)
}

let _defaultProject = '';
async function loadProjectSwitcher() {
  const sel = document.getElementById('globalProjectSel');
  if (!sel) return;
  try {
    const r = await api('/api/projects/mine');
    const projects = r.projects || [];
    _defaultProject = r.default_project_id ? String(r.default_project_id) : '';
    // On login (no client-side selection), land on the user's default project.
    if (!_activeProject && _defaultProject && projects.some(p => String(p.id) === _defaultProject)) {
      _activeProject = _defaultProject;
      try { localStorage.setItem('davActiveProject', _activeProject); } catch {}
    }
    if (_activeProject && !projects.some(p => String(p.id) === String(_activeProject))) {
      _activeProject = ''; try { localStorage.removeItem('davActiveProject'); } catch {}
    }
    sel.innerHTML = projects.length
      ? projects.map(p => `<option value="${p.id}"${String(p.id)===String(_activeProject)?' selected':''}>${esc(p.name)}${String(p.id)===_defaultProject?' ★':''}</option>`).join('')
      : '<option value="">No projects — ask an admin to add you</option>';
    if (_activeProject) sel.value = _activeProject;
    const chip = document.getElementById('projectChip');
    if (chip) chip.style.display = '';
    const star = document.getElementById('projSetDefaultBtn');
    if (star) {
      star.style.display = projects.length ? '' : 'none';
      star.textContent = (_activeProject && _activeProject === _defaultProject) ? '★' : '☆';
      star.title = (_activeProject && _activeProject === _defaultProject) ? 'This is your default project' : 'Make this my default project';
    }
  } catch(e) {}
}
document.getElementById('globalProjectSel')?.addEventListener('change', function() {
  _activeProject = this.value || '';
  try { _activeProject ? localStorage.setItem('davActiveProject', _activeProject) : localStorage.removeItem('davActiveProject'); } catch {}
  location.reload();   // re-fetch everything scoped to the newly selected project
});
document.getElementById('projSetDefaultBtn')?.addEventListener('click', async () => {
  if (!_activeProject) return;
  try { await api('/api/me/default-project', { method:'PUT', body: JSON.stringify({ project_id: parseInt(_activeProject,10) }) });
    _defaultProject = _activeProject; const s = document.getElementById('projSetDefaultBtn');
    if (s) { s.textContent = '★'; s.title = 'This is your default project'; } toast('Default project set'); }
  catch(e){ toast(e.message, true); }
});

// Re-fetch identity + re-apply access visibility when the tab regains focus, throttled.
// Self-heals the case where a transient /api/me during a deploy rollover briefly reported
// is_platform_admin=false and the admin UI (presence chip, Users/Audit nav, etc.) vanished
// until a manual refresh.
let _lastMeRefresh = 0;
window.addEventListener('focus', () => {
  if (Date.now() - _lastMeRefresh > 30000) { _lastMeRefresh = Date.now(); loadMe(); }
});

async function loadMe() {
  _lastMeRefresh = Date.now();
  try { me = await api('/api/me'); } catch { me = {reviewer:null,authenticated:false}; }
  const chip = document.getElementById('acctChipLabel');
  const who = document.getElementById('acctMenuWho');
  if (me.authenticated && me.reviewer) {
    chip.textContent = me.reviewer;
    if (who) who.textContent = me.role ? `${me.reviewer} · ${me.role}` : me.reviewer;
  } else {
    chip.textContent = 'unauthenticated';
    if (who) who.textContent = 'Not signed in';
  }
  _applyAccessVisibility();
  if (me && me.must_change_password) {
    // Forced first-login change — no cancel; messaging reflects that.
    const sub = document.getElementById('pwChangeSub'); if (sub) sub.textContent = "You're using a default password — set your own to continue.";
    const c = document.getElementById('pwChangeCancel'); if (c) c.style.display = 'none';
    const ov = document.getElementById('pwChangeOverlay'); if (ov) ov.style.display = 'flex';
  }
}
document.getElementById('pwChangeBtn')?.addEventListener('click', async () => {
  const cur = document.getElementById('pwChangeCur').value;
  const nw = document.getElementById('pwChangeNew').value;
  const msg = document.getElementById('pwChangeMsg');
  if ((nw||'').length < 8) { msg.textContent = 'New password must be at least 8 characters'; msg.style.color='var(--red)'; return; }
  try {
    await api('/api/auth/change-password', { method:'POST', body: JSON.stringify({ current_password: cur, new_password: nw }) });
    document.getElementById('pwChangeOverlay').style.display = 'none';
    toast('Password updated');
  } catch(e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
});

// Show the admin-only Users & Access UI when the caller is an admin.
// Does the caller hold a project-scoped privilege in the active project?
// platform.admin is a superuser. me.privileges is resolved for the active project
// by /api/me, so this re-evaluates correctly after a project switch.
function can(priv) {
  return !!(me && (me.is_platform_admin || (me.privileges || []).includes(priv)));
}

// ── Workspace focus (Architecture ⇄ Assessment) + read-only View mode ──────────
// Focus filters the left nav to an intent's views (+ 'both' shared ones); View mode
// hides edit affordances even for users who can edit. Both persist per-user.
let _persona = (() => { try { return localStorage.getItem('davPersona') || ''; } catch { return ''; } })();
let _viewMode = (() => { try { return localStorage.getItem('davViewMode') === '1'; } catch { return false; } })();
let _navRbac = {};  // {navId: allowed} — RBAC baseline computed by _applyAccessVisibility

// ── Persona (the UX paradigm, docs/ux-paradigm-design.md) ───────────────────
// Objectives are constant; the PERSONA is the lens. Each persona foregrounds an
// ordered subset of domains (+ later, the projections it reads). Switchable, with the
// default derived from the RBAC role; orthogonal to view-mode (persona = which
// projection you consume; posture = edit/view).
const PERSONAS = [
  { key:'architect',   label:'Architect',   domains:['author','execute','roadmap','catalog','improve','org'] },
  { key:'engineer',    label:'Engineer',    domains:['roadmap','execute','catalog'] },
  { key:'customer',    label:'Customer',    domains:['roadmap','execute'] },         // + Outcomes when built
  { key:'stakeholder', label:'Stakeholder', domains:['roadmap'] },                   // + Value/Outcomes when built
  { key:'assessor',    label:'Assessor',    domains:['assess','catalog','roadmap','org'] },
  { key:'operator',    label:'Operator',    domains:['org','config','catalog','improve','audit'] },
];
function _activePersona() { return PERSONAS.find(a => a.key === _persona) || PERSONAS[0]; }
// Default persona from privileges: assessment-oriented users (assessment access but not
// the UC/arch pipeline, not a platform admin) → Assessor; everyone else → Architect.
function _defaultPersona() {
  if (can('assessment.view') && !can('project.usecases') && !can('project.runs.execute')
      && !(me && me.is_platform_admin)) return 'assessor';
  return 'architect';
}
// UI lean slice 1: personas removed as a navigation mechanism. The nav is one
// stable rail for everyone — every domain the user is PERMITTED to see (RBAC),
// in canonical DOMAINS order. No per-role reshuffling (that breaks spatial
// memory / recognition). Permission gates visibility, not persona.
function _personaDomains() {
  return DOMAINS.filter(d => _domainPermitted(d));
}
// canEdit = can(priv) AND not browsing in read-only View mode.
function canEdit(priv) { return can(priv) && !_viewMode; }

// Re-render the rail for the active persona (subset + order), reflect the switcher, and
// land on the persona's first domain if the active one isn't in this persona.
function _applyPersona() {
  if (!_persona || !PERSONAS.some(a => a.key === _persona)) _persona = _defaultPersona();
  renderDomainRail();
  const sw = document.getElementById('personaSel');
  if (sw) {
    if (sw.options.length !== PERSONAS.length)
      sw.innerHTML = PERSONAS.map(a => `<option value="${a.key}">${esc(a.label)}</option>`).join('');
    sw.value = _persona;
  }
  const doms = _personaDomains();
  // renderDomainRail() just cleared the rail's .active marker, so derive the current
  // domain from the active VIEW (not the DOM) — otherwise a bare re-render (e.g. window
  // refocus → loadMe → here) would always look "unset" and home us back to the default
  // view (Authoring → Use Cases). Only auto-home when there's genuinely no current view.
  const curDom = _curView ? _viewToDomain[_curView] : null;
  if (curDom && doms.some(d => d.key === curDom.key)) {
    document.querySelectorAll('.pf-nav-item[data-domain]').forEach(a =>
      a.classList.toggle('active', a.dataset.domain === curDom.key));
    try { renderDomainTabs(curDom, _curView); } catch (e) {}
  } else if (doms.length) {
    try { switchDomain(doms[0].key); } catch (e) { console.warn('persona home switch failed', e); }
  }
}
// #129: per-user settings sync. The UI chrome prefs live in localStorage (fast, local) and
// are mirrored to the DB so they follow the user across devices. localStorage is the cache;
// the server is the source of truth, pulled once at boot and pushed (debounced) on change.
// Chrome prefs (always lightweight). Session/working-context keys (where you left off) sync
// only when "Continue session across devices" is on.
const _USER_SETTING_KEYS = ['davTheme', 'davMode', 'davPersona', 'davViewMode', 'davNavCollapsed'];
const _SESSION_KEYS = ['davActiveProject', 'davScope', 'davCustomer'];
let _userSettingsLoaded = false;
let _persistTimer = null;
// Master switch (default ON). When OFF, nothing but the flag itself syncs — pure local/per-browser.
function _syncEnabled() { try { return localStorage.getItem('davSyncSession') !== '0'; } catch { return true; } }
function setSyncSession(on) {
  try { localStorage.setItem('davSyncSession', on ? '1' : '0'); } catch {}
  const cb = document.getElementById('syncSessionToggle'); if (cb) cb.checked = on;
  _persistUserSettings();   // persists the flag (and, when on, your current settings + context)
  try { toast(on ? 'Session sync on — you’ll continue where you left off on any device' : 'Session sync off — this browser only'); } catch {}
}
function _persistUserSettings() {
  if (!_userSettingsLoaded) return;   // don't clobber the server with defaults before the pull lands
  const on = _syncEnabled();
  const settings = { davSyncSession: on ? '1' : '0' };   // the choice itself always persists
  if (on) {
    for (const k of [..._USER_SETTING_KEYS, ..._SESSION_KEYS]) {
      try { const v = localStorage.getItem(k); if (v !== null && v !== '') settings[k] = v; } catch {}
    }
  }
  clearTimeout(_persistTimer);
  _persistTimer = setTimeout(() => {
    api('/api/me/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings }) }).catch(() => {});
  }, 600);
}
async function _loadUserSettings() {
  try {
    const r = await api('/api/me/settings');
    const s = (r && r.settings) || {};
    if (s.davSyncSession !== undefined) { try { localStorage.setItem('davSyncSession', String(s.davSyncSession)); } catch {} }
    const on = _syncEnabled();
    const cb = document.getElementById('syncSessionToggle'); if (cb) cb.checked = on;
    _userSettingsLoaded = true;
    if (!on) return;   // local-only — don't restore
    // Seed keys this browser is MISSING (a fresh device) from the server; an active device's
    // local choices win (no surprise overrides). Chrome re-applies live; a restored working
    // context (project/scope/customer) needs a single clean re-boot.
    let chromeSeeded = false, sessionSeeded = false;
    for (const k of [..._USER_SETTING_KEYS, ..._SESSION_KEYS]) {
      let local = null; try { local = localStorage.getItem(k); } catch {}
      if ((local === null || local === '') && s[k] !== undefined && s[k] !== null && String(s[k]) !== '') {
        try { localStorage.setItem(k, String(s[k])); } catch {}
        if (_SESSION_KEYS.includes(k)) sessionSeeded = true; else chromeSeeded = true;
      }
    }
    if (sessionSeeded && !sessionStorage.getItem('davSessionRestored')) {
      try { sessionStorage.setItem('davSessionRestored', '1'); } catch {}
      location.reload();   // re-boot with the restored working context (existing init reads localStorage)
      return;
    }
    if (chromeSeeded) {   // re-apply chrome from the seeded prefs (no reload needed)
      try { currentTheme = localStorage.getItem('davTheme') || currentTheme; currentMode = localStorage.getItem('davMode') || currentMode; applyTheme(); } catch {}
      try { _persona = localStorage.getItem('davPersona') || _persona; _applyPersona(); const ps = document.getElementById('personaSel'); if (ps) ps.value = _persona; } catch {}
      try { _viewMode = localStorage.getItem('davViewMode') === '1'; _applyViewMode(); _applyAccessVisibility(); } catch {}
      try { const nav = document.getElementById('pfNav'); if (nav) { _navCollapsed = localStorage.getItem('davNavCollapsed') === '1'; nav.classList.toggle('collapsed', _navCollapsed); } } catch {}
    }
    // Snapshot this (active) device's current context to the server so the latest project/
    // scope/customer is available to the next fresh device (the project switch reloads, so
    // its push lands here on the post-reload boot).
    try { _persistUserSettings(); } catch {}
  } catch { _userSettingsLoaded = true; }   // never block boot on a settings fetch
}
function setPersona(a) {
  if (!PERSONAS.some(x => x.key === a) || a === _persona) return;
  _persona = a;
  try { localStorage.setItem('davPersona', a); } catch {}
  _applyPersona();
  _persistUserSettings();
}
function _applyViewMode() {
  document.body.dataset.viewMode = _viewMode ? '1' : '';
  const chip = document.getElementById('viewModeToggle');
  const lbl = document.getElementById('viewModeLabel');
  if (chip) chip.classList.toggle('on', _viewMode);
  if (lbl) lbl.textContent = _viewMode ? 'View only' : 'Editing';
}
function toggleViewMode() {
  _viewMode = !_viewMode;
  try { localStorage.setItem('davViewMode', _viewMode ? '1' : '0'); } catch {}
  _applyViewMode();
  try { _applyAccessVisibility(); } catch (e) { console.warn('view-mode reapply failed', e); }
  _persistUserSettings();
}

function _applyAccessVisibility() {
  const projAdmin = !!(me && me.is_admin);            // project admin or above
  const platAdmin = !!(me && me.is_platform_admin);   // platform admin only
  // Projects management (now in view-projects): project admins.
  const cp = document.getElementById('configProjectsPanel'); if (cp) cp.style.display = projAdmin ? '' : 'none';
  // Accounts/roles + LDAP + SMTP + Agents (Config → Platform): platform admins only.
  ['configUsersPanel','configLdapPanel','configSmtpPanel','configAgentsPanel','configTenantsPanel','configGroupsPanel'].forEach(id => {
    const el = document.getElementById(id); if (el) el.style.display = platAdmin ? '' : 'none';
  });
  // Bundles (#107) — manage shared/platform/use-category config: usecat.manage holders.
  const bp = document.getElementById('configBundlesPanel'); if (bp) bp.style.display = can('usecat.manage') ? '' : 'none';
  // The Config "Platform" section-tab shows iff one of its panels is visible (projAdmin
  // → Projects; platAdmin → Email/LDAP/Users) — derived by _syncConfigTabs() below from
  // the per-panel gating already applied above. No separate nav-link gating needed.
  // Nav RBAC baseline (which items the user may see at all). The persona switcher then
  // selects which domains foreground — see _applyPersona(). Projects/Users left-nav
  // views are retired (consolidated into Config → Platform).
  _navRbac = {
    navAudit: platAdmin,
    navAssess: can('assessment.view'),
  };
  try { _applyPersona(); } catch (e) { console.warn('persona apply failed', e); }
  // Presence gauge — platform admins only. Run it HERE (early) and guarded, so a failure
  // in any later block can never strip the admin status bar (it used to be the last line).
  try { _startPresence(platAdmin); } catch (e) { console.warn('presence init failed', e); }
  try { loadAssessmentSelector(); loadBlueprintSelector(); } catch (e) { console.warn('masthead selector init failed', e); }
  // Config panels are project-scoped: gate each on its management privilege.
  const cfgGate = [
    ['configReposPanel',  'project.repos'],
    ['configModelsPanel', 'project.models'],
    ['configMCPPanel',    'project.integrations'],
  ];
  for (const [panelId, priv] of cfgGate) {
    const panel = document.getElementById(panelId); if (panel) panel.style.display = can(priv) ? '' : 'none';
  }
  // Section-tabs follow the now-resolved per-panel visibility (hides empty sections).
  try { _syncConfigTabs(); } catch (e) { console.warn('config tab sync failed', e); }
  // Workflow action affordances (server enforces regardless; this is UX). Toggle
  // body data-* flags that CSS can hang off, and hide tagged controls directly.
  // canEdit() = the privilege AND not browsing in read-only View mode, so toggling
  // View mode hides every edit/execute affordance hung off these data-can-* flags.
  const flags = {
    'data-can-usecases':    canEdit('project.usecases'),
    'data-can-runs-exec':   canEdit('project.runs.execute'),
    'data-can-runs-manage': canEdit('project.runs.manage'),
    'data-can-archreview':  canEdit('project.archreview.execute'),
    'data-can-archctx':     canEdit('project.archreview.context'),
    'data-can-enh':         canEdit('project.enhancement.execute'),
    'data-can-enh-pr':      canEdit('project.enhancement.pr'),
    'data-can-catalog':     canEdit('project.catalog'),
  };
  for (const [attr, ok] of Object.entries(flags)) {
    if (ok) document.body.setAttribute(attr, '1'); else document.body.removeAttribute(attr);
  }
  // Any element tagged data-needs-priv="<key>" is hidden unless the caller holds it.
  document.querySelectorAll('[data-needs-priv]').forEach(el => {
    el.style.display = can(el.getAttribute('data-needs-priv')) ? '' : 'none';
  });
  // Edit affordances tagged data-edit-gate="<priv>" hide unless the caller can EDIT — i.e. holds
  // the privilege AND isn't in read-only View mode. One marker covers both #136 (view mode) and
  // #137 (view-only role). The api() backstop blocks any leaked mutation regardless.
  document.querySelectorAll('[data-edit-gate]').forEach(el => {
    el.style.display = canEdit(el.getAttribute('data-edit-gate')) ? '' : 'none';
  });
  // A persistent, unmistakable read-only banner while in View mode.
  document.body.dataset.viewMode = _viewMode ? '1' : '';
  // Presence gauge — platform admins only.
  _startPresence(platAdmin);
}

let _presenceTimer = null;
async function loadPresence() {
  const wrap = document.getElementById('presenceWrap');
  if (!wrap || wrap.style.display === 'none') return;
  try {
    const p = await api('/api/presence');
    const o = document.getElementById('presenceOnline'); if (o) o.textContent = p.online ?? '–';
    const a = document.getElementById('presenceActive'); if (a) a.textContent = p.active ?? '–';
  } catch(e) { /* keep last values on a transient failure */ }
}
function _startPresence(on) {
  const wrap = document.getElementById('presenceWrap');
  if (on) {
    if (wrap) wrap.style.display = 'inline-block';
    loadPresence();
    if (!_presenceTimer) _presenceTimer = setInterval(loadPresence, 45000);
  } else {
    if (wrap) wrap.style.display = 'none';
    _closePresencePopover();
    if (_presenceTimer) { clearInterval(_presenceTimer); _presenceTimer = null; }
  }
}
let _presencePopTimer = null;
function _closePresencePopover() {
  const pop = document.getElementById('presencePopover'); if (pop) pop.style.display = 'none';
  if (_presencePopTimer) { clearInterval(_presencePopTimer); _presencePopTimer = null; }
}
async function _renderPresenceDetail(first) {
  const pop = document.getElementById('presencePopover');
  if (!pop || pop.style.display === 'none') return;
  if (first) pop.innerHTML = '<div style="padding:8px 10px;color:var(--text-faint);font-size:11px;">loading…</div>';
  try {
    const p = await api('/api/presence?detail=1');
    // Keep the chip headline counts in sync with the live detail.
    const co = document.getElementById('presenceOnline'); if (co) co.textContent = p.online ?? '–';
    const ca = document.getElementById('presenceActive'); if (ca) ca.textContent = p.active ?? '–';
    const me_id = (me && me.reviewer) ? me.reviewer.toLowerCase() : '';
    const head = `<div style="display:flex;align-items:center;gap:6px;padding:4px 8px 6px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint);border-bottom:1px solid var(--border);">
        <span class="presence-live-dot" style="width:6px;height:6px;border-radius:50%;background:var(--green);"></span>
        ${p.online||0} online · ${p.active||0} active</div>`;
    const rows = (p.users && p.users.length) ? p.users.map(u => {
      // online+active = working now; online+idle = polling but quiet; active+!online = acted
      // recently but the tab stopped polling (e.g. closed/backgrounded — counts as active, not online).
      const idleTxt = (u.idle_secs == null) ? '' : (u.idle_secs < 60 ? `${u.idle_secs}s idle` : `${Math.floor(u.idle_secs/60)}m idle`);
      const idle = (u.online && u.active) ? 'active now'
        : (u.online ? idleTxt
        : (u.active ? 'active · not polling' : (idleTxt || 'offline')));
      const dot = (u.online && u.active) ? 'var(--green)' : (u.active ? 'var(--amber,#d79a2b)' : 'var(--text-faint)');
      const meTag = (u.id === me_id) ? ' <span style="color:var(--text-faint);">(you)</span>' : '';
      return `<div style="display:flex;align-items:center;gap:8px;padding:5px 8px;font-size:12px;">
        <span style="color:${dot};font-size:9px;">●</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(u.id)}${meTag}</span>
        <span style="color:var(--text-faint);font-size:10px;white-space:nowrap;">${idle}</span>
      </div>`;
    }).join('') : '<div style="padding:8px;color:var(--text-faint);font-size:11px;">No one online.</div>';
    pop.innerHTML = head + rows;
  } catch(e) {
    if (first) pop.innerHTML = `<div style="padding:8px;color:var(--red);font-size:11px;">${esc(e.message||'failed')}</div>`;
  }
}
function _togglePresencePopover() {
  const pop = document.getElementById('presencePopover');
  if (!pop) return;
  if (pop.style.display !== 'none') { _closePresencePopover(); return; }
  pop.style.display = 'block';
  _renderPresenceDetail(true);
  // Live refresh while open — fast cadence for a real-time feel; stops on close.
  if (!_presencePopTimer) _presencePopTimer = setInterval(() => _renderPresenceDetail(false), 4000);
}
document.getElementById('presenceChip')?.addEventListener('click', (e) => { e.stopPropagation(); _togglePresencePopover(); });
document.addEventListener('click', (e) => {
  const wrap = document.getElementById('presenceWrap');
  if (wrap && !wrap.contains(e.target)) _closePresencePopover();
});
// Build stamp — confirm which UI build this browser is actually running.
(function(){
  const b = document.querySelector('meta[name="dav-build"]')?.content || 'dev';
  const el = document.getElementById('buildStamp'); if (el) el.textContent = b;
  try { console.info('%cDAV UI build: ' + b, 'color:#c8964a'); } catch(_) {}
})();

function loadAccessPanels() {
  // Projects admin moved to the Customers & Projects → Projects tab (loadProjectsTab).
  if (me && me.is_platform_admin) { loadLdapStatus(); loadUsers(); loadLdapSettings(); loadSmtpSettings(); }
}

// ── Agents & access tokens (#168) — Config → Platform → Agents panel ─────────
function _agentFmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso); if (isNaN(d.getTime())) return esc(String(iso));
  return d.toLocaleDateString(undefined, { year:'numeric', month:'short', day:'numeric' });
}

async function loadAgentTokens() {
  const box = document.getElementById('agentTokenList');
  if (!box) return;
  box.innerHTML = '<div class="empty" style="padding:14px;">Loading…</div>';
  try {
    const r = await api('/api/tokens');
    const toks = (r && r.tokens) || [];
    if (!toks.length) { box.innerHTML = '<div class="empty" style="padding:14px;">No tokens yet — generate one above.</div>'; return; }
    const now = Date.now();
    const rows = toks.map(t => {
      const revoked = !!t.revoked_at;
      const exp = t.expires_at ? new Date(t.expires_at).getTime() : 0;
      const expired = exp && exp < now;
      const status = revoked ? '<span style="color:var(--red);">revoked</span>'
        : expired ? '<span style="color:var(--orange,#c8861a);">expired</span>'
        : '<span style="color:var(--green);">active</span>';
      const lastUsed = t.last_used_at ? _agentFmtDate(t.last_used_at) : '<span style="color:var(--text-faint);">never</span>';
      const expTxt = t.expires_at ? _agentFmtDate(t.expires_at) : '<span style="color:var(--text-faint);">no expiry</span>';
      return `<tr style="border-top:1px solid var(--border);">
        <td style="padding:8px 10px;">${esc(t.label || '—')}</td>
        <td style="padding:8px 10px;font-family:monospace;font-size:11px;">${esc(t.email)}</td>
        <td style="padding:8px 10px;font-size:11px;">${status}</td>
        <td style="padding:8px 10px;font-size:11px;">${_agentFmtDate(t.created_at)}<div style="color:var(--text-faint);">${esc(t.created_by || '')}</div></td>
        <td style="padding:8px 10px;font-size:11px;">${lastUsed}</td>
        <td style="padding:8px 10px;font-size:11px;">${expTxt}</td>
        <td style="padding:8px 10px;text-align:right;">${revoked ? '' : `<button class="btn ghost btn-sm" type="button" onclick="_revokeAgentToken(${t.id}, this)">Revoke</button>`}</td>
      </tr>`;
    }).join('');
    box.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.04em;">
        <th style="padding:6px 10px;">Label</th><th style="padding:6px 10px;">Acts as</th><th style="padding:6px 10px;">Status</th>
        <th style="padding:6px 10px;">Created</th><th style="padding:6px 10px;">Last used</th><th style="padding:6px 10px;">Expires</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  } catch (e) {
    box.innerHTML = `<div class="empty" style="padding:14px;color:var(--red);">${esc(e.message || 'failed to load tokens')}</div>`;
  }
}

async function _revokeAgentToken(id, btn) {
  if (!confirm('Revoke this token? Any agent using it loses access immediately.')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'revoking…'; }
  try { await api('/api/tokens/' + id, { method: 'DELETE' }); toast('Token revoked'); loadAgentTokens(); }
  catch (e) { toast(e.message, true); if (btn) { btn.disabled = false; btn.textContent = 'Revoke'; } }
}

document.getElementById('agentMintBtn')?.addEventListener('click', async () => {
  const email = (document.getElementById('agentEmail').value || '').trim().toLowerCase();
  const label = (document.getElementById('agentLabel').value || '').trim();
  const days = parseInt(document.getElementById('agentExpiry').value, 10);
  const msg = document.getElementById('agentMintMsg');
  if (email.length < 2) { msg.textContent = 'Enter the account email the token acts as.'; msg.style.color = 'var(--red)'; return; }
  let expires_at = null;
  if (days > 0) expires_at = new Date(Date.now() + days * 864e5).toISOString();
  msg.textContent = 'generating…'; msg.style.color = 'var(--text-faint)';
  try {
    const r = await api('/api/tokens', { method: 'POST', body: JSON.stringify({ email, label, expires_at }) });
    msg.textContent = '';
    const card = document.getElementById('agentRevealCard');
    document.getElementById('agentRevealToken').textContent = r.token;
    document.getElementById('agentRevealUsage').innerHTML =
      `Send it as a bearer header on every request:<br>` +
      `<code>Authorization: Bearer ${esc(r.token)}</code><br>` +
      `Example: <code>curl -s ${esc(location.origin)}/api/me -H "Authorization: Bearer $DAV_TOKEN"</code><br>` +
      `Acts as <strong>${esc(r.email)}</strong> — grant it roles in Users &amp; roles if it has none.`;
    card.style.display = '';
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    document.getElementById('agentLabel').value = '';
    loadAgentTokens();
  } catch (e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
});

document.getElementById('agentCopyBtn')?.addEventListener('click', () => {
  const t = document.getElementById('agentRevealToken').textContent || '';
  if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => toast('Copied')).catch(() => toast('Copy failed', true));
  else toast('Copy not available', true);
});
document.getElementById('agentRevealDismiss')?.addEventListener('click', () => {
  const c = document.getElementById('agentRevealCard'); if (c) c.style.display = 'none';
});
document.getElementById('agentRefreshBtn')?.addEventListener('click', loadAgentTokens);

// ── Tenants + Groups management (tenancy Phase 1c) — platform-admin ───────────
let _tenantsCache = [];
async function loadTenants() {
  const box = document.getElementById('tenantList');
  if (!box) return;
  try {
    const r = await api('/api/tenants');
    _tenantsCache = (r && r.tenants) || [];
    if (!_tenantsCache.length) { box.innerHTML = '<div class="empty" style="padding:10px;">No tenants.</div>'; }
    else box.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr style="text-align:left;color:var(--text-dim);font-size:10px;text-transform:uppercase;letter-spacing:.04em;">
        <th style="padding:6px 8px;">Slug</th><th style="padding:6px 8px;">Name</th><th style="padding:6px 8px;">Isolation</th>
        <th style="padding:6px 8px;">Regime</th><th style="padding:6px 8px;">Projects</th></tr></thead><tbody>${
      _tenantsCache.map(t => `<tr style="border-top:1px solid var(--border);">
        <td style="padding:6px 8px;font-family:var(--mono,monospace);">${esc(t.slug)}</td>
        <td style="padding:6px 8px;">${esc(t.name)}</td>
        <td style="padding:6px 8px;">${esc(t.isolation_level)}</td>
        <td style="padding:6px 8px;">${esc(t.declared_regime)}</td>
        <td style="padding:6px 8px;">${t.project_count}</td></tr>`).join('')}</tbody></table>`;
    _populateGroupTenantPicker();
  } catch (e) { box.innerHTML = `<div class="empty" style="padding:10px;color:var(--red);">${esc(e.message)}</div>`; }
}
document.getElementById('tenantCreateBtn')?.addEventListener('click', async () => {
  const slug = (document.getElementById('tenantNewSlug').value || '').trim().toLowerCase();
  const name = (document.getElementById('tenantNewName').value || '').trim();
  const regime = document.getElementById('tenantNewRegime').value;
  const msg = document.getElementById('tenantCreateMsg');
  if (slug.length < 2) { msg.textContent = 'slug required'; msg.style.color = 'var(--red)'; return; }
  msg.textContent = 'creating…'; msg.style.color = 'var(--text-faint)';
  try {
    await api('/api/tenants', { method: 'POST', body: JSON.stringify({ slug, name, declared_regime: regime }) });
    msg.textContent = ''; document.getElementById('tenantNewSlug').value = ''; document.getElementById('tenantNewName').value = '';
    toast('Tenant created'); loadTenants();
  } catch (e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
});

function _onGroupScopeChange() {
  const scope = document.getElementById('groupNewScope').value;
  const tp = document.getElementById('groupNewTenant');
  if (tp) tp.style.display = scope === 'tenant' ? '' : 'none';
}
function _populateGroupTenantPicker() {
  const tp = document.getElementById('groupNewTenant');
  if (tp) tp.innerHTML = _tenantsCache.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
}
async function loadGroups() {
  const box = document.getElementById('groupList');
  if (!box) return;
  try {
    const r = await api('/api/groups');
    const groups = (r && r.groups) || [];
    if (!groups.length) { box.innerHTML = '<div class="empty" style="padding:10px;">No groups.</div>'; return; }
    box.innerHTML = groups.map(g => {
      const scopeLabel = g.scope === 'tenant' ? `tenant:${(_tenantsCache.find(t => t.id === g.tenant_id) || {}).slug || g.tenant_id}` : g.scope;
      return `<div class="cfg-card" style="margin:6px 0;padding:8px 10px;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <div><b>${esc(g.name)}</b> <span style="font-size:10px;color:var(--text-dim);border:1px solid var(--border);padding:0 4px;border-radius:3px;">${esc(scopeLabel)}</span>
            <span style="font-size:11px;color:var(--text-faint);"> · ${g.member_count} member${g.member_count===1?'':'s'} · ${g.role_count} role${g.role_count===1?'':'s'}</span></div>
          <div style="display:flex;gap:4px;">
            <button class="btn ghost btn-sm" type="button" onclick="_toggleGroupDetail(${g.id})">Manage</button>
            <button class="btn danger btn-sm" type="button" onclick="_deleteGroup(${g.id}, '${esc(g.name)}')">Delete</button></div>
        </div>
        <div id="groupDetail-${g.id}" style="display:none;margin-top:8px;border-top:1px solid var(--border);padding-top:8px;"></div>
      </div>`;
    }).join('');
  } catch (e) { box.innerHTML = `<div class="empty" style="padding:10px;color:var(--red);">${esc(e.message)}</div>`; }
}
document.getElementById('groupCreateBtn')?.addEventListener('click', async () => {
  const name = (document.getElementById('groupNewName').value || '').trim();
  const scope = document.getElementById('groupNewScope').value;
  const msg = document.getElementById('groupCreateMsg');
  if (name.length < 1) { msg.textContent = 'name required'; msg.style.color = 'var(--red)'; return; }
  const body = { name, scope };
  if (scope === 'tenant') body.tenant_id = parseInt(document.getElementById('groupNewTenant').value, 10);
  msg.textContent = 'creating…'; msg.style.color = 'var(--text-faint)';
  try {
    await api('/api/groups', { method: 'POST', body: JSON.stringify(body) });
    msg.textContent = ''; document.getElementById('groupNewName').value = ''; toast('Group created'); loadGroups();
  } catch (e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
});
async function _deleteGroup(id, name) {
  if (!confirm(`Delete group "${name}"? Members lose its roles.`)) return;
  try { await api('/api/groups/' + id, { method: 'DELETE' }); toast('Group deleted'); loadGroups(); }
  catch (e) { toast(e.message, true); }
}
async function _toggleGroupDetail(id) {
  const d = document.getElementById('groupDetail-' + id);
  if (!d) return;
  if (d.style.display !== 'none') { d.style.display = 'none'; return; }
  d.style.display = ''; d.innerHTML = 'Loading…';
  try {
    const [mres, rres, roles] = await Promise.all([
      api('/api/groups/' + id + '/members'), api('/api/groups/' + id + '/roles'), api('/api/rbac/roles')]);
    const members = (mres && mres.members) || [];
    const bound = (rres && rres.roles) || [];
    const allRoles = ((roles && roles.roles) || roles || []);
    d.innerHTML = `
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
        <input id="gm-${id}" placeholder="user email" style="font-size:12px;width:180px;">
        <button class="btn ghost btn-sm" type="button" onclick="_addGroupMember(${id})">+ Member</button></div>
      <div style="font-size:11px;margin-bottom:8px;">${members.length ? members.map(m =>
        `<span style="display:inline-flex;align-items:center;gap:3px;border:1px solid var(--border);border-radius:10px;padding:1px 7px;margin:2px;">${esc(m.reviewer)}<span style="cursor:pointer;color:var(--red);" onclick="_removeGroupMember(${id},'${esc(m.reviewer)}')">✕</span></span>`).join('') : '<span style="color:var(--text-faint);">no members</span>'}</div>
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
        <select id="gr-${id}" style="font-size:12px;">${allRoles.map(r => `<option value="${r.id}">${esc(r.name)} (${esc(r.scope)})</option>`).join('')}</select>
        <button class="btn ghost btn-sm" type="button" onclick="_bindGroupRole(${id})">+ Role</button></div>
      <div style="font-size:11px;">${bound.length ? bound.map(r =>
        `<span style="display:inline-flex;align-items:center;gap:3px;border:1px solid var(--border);border-radius:10px;padding:1px 7px;margin:2px;">${esc(r.name)}<span style="cursor:pointer;color:var(--red);" onclick="_unbindGroupRole(${id},${r.role_id})">✕</span></span>`).join('') : '<span style="color:var(--text-faint);">no roles bound</span>'}</div>`;
  } catch (e) { d.innerHTML = `<span style="color:var(--red);">${esc(e.message)}</span>`; }
}
async function _addGroupMember(id) {
  const v = (document.getElementById('gm-' + id).value || '').trim();
  if (v.length < 2) return;
  try { await api('/api/groups/' + id + '/members', { method: 'POST', body: JSON.stringify({ reviewer: v }) });
    d_reload(id); } catch (e) { toast(e.message, true); }
}
async function _removeGroupMember(id, reviewer) {
  try { await api('/api/groups/' + id + '/members/' + encodeURIComponent(reviewer), { method: 'DELETE' }); d_reload(id); }
  catch (e) { toast(e.message, true); }
}
async function _bindGroupRole(id) {
  const rid = parseInt(document.getElementById('gr-' + id).value, 10);
  try { await api('/api/groups/' + id + '/roles', { method: 'POST', body: JSON.stringify({ role_id: rid }) }); d_reload(id); }
  catch (e) { toast(e.message, true); }
}
async function _unbindGroupRole(id, roleId) {
  try { await api('/api/groups/' + id + '/roles/' + roleId, { method: 'DELETE' }); d_reload(id); }
  catch (e) { toast(e.message, true); }
}
// Re-open a group's detail (refresh counts + the expanded panel).
async function d_reload(id) { await loadGroups(); await _toggleGroupDetail(id); }

// Create a login-less AGENT identity (kind='agent') — a real account, separate from
// people, that carries its own roles (granted in Users & roles) and authenticates only
// via a PAT. After creation we pre-fill the mint form's "Acts as" with it.
document.getElementById('agentCreateBtn')?.addEventListener('click', async () => {
  const email = (document.getElementById('newAgentEmail').value || '').trim().toLowerCase();
  const name  = (document.getElementById('newAgentName').value || '').trim();
  const msg = document.getElementById('agentCreateMsg');
  if (email.length < 2) { msg.textContent = 'Enter an identifier for the agent.'; msg.style.color = 'var(--red)'; return; }
  msg.textContent = 'creating…'; msg.style.color = 'var(--text-faint)';
  try {
    await api('/api/accounts', { method: 'POST', body: JSON.stringify({ email, display_name: name, kind: 'agent' }) });
    msg.textContent = ''; toast('Agent identity created — grant it roles in Users & roles, then mint a token');
    document.getElementById('newAgentEmail').value = '';
    document.getElementById('newAgentName').value = '';
    const acts = document.getElementById('agentEmail'); if (acts) acts.value = email;   // prefill the mint form
    try { loadAccounts(); loadRolesMatrix(); } catch {}   // refresh the people/roles panel so the agent shows
  } catch (e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
});

async function loadLdapSettings() {
  try {
    const s = await api('/api/settings/ldap');
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
    set('ls-url', s.url); set('ls-bind_dn', s.bind_dn); set('ls-user_base', s.user_base);
    set('ls-group_dn', s.group_dn); set('ls-user_attr', s.user_attr); set('ls-mail_attr', s.mail_attr);
    set('ls-name_attr', s.name_attr); set('ls-member_attr', s.member_attr);
    document.getElementById('ls-start_tls').checked = !!s.start_tls;
    document.getElementById('ls-enforce').checked = !!s.enforce;
    document.getElementById('ls-bind_password').placeholder = s.bind_password_set ? '•••• (set — leave blank to keep)' : '';
  } catch(e) {}
}
document.getElementById('ls-save')?.addEventListener('click', async () => {
  const v = id => (document.getElementById(id)?.value || '').trim();
  const body = { url:v('ls-url'), bind_dn:v('ls-bind_dn'), user_base:v('ls-user_base'), group_dn:v('ls-group_dn'),
    user_attr:v('ls-user_attr'), mail_attr:v('ls-mail_attr'), name_attr:v('ls-name_attr'), member_attr:v('ls-member_attr'),
    start_tls:document.getElementById('ls-start_tls').checked, enforce:document.getElementById('ls-enforce').checked };
  const pw = v('ls-bind_password'); if (pw) body.bind_password = pw;
  const msg = document.getElementById('ls-msg'); msg.textContent = 'saving…'; msg.style.color='var(--text-faint)';
  try { const r = await api('/api/settings/ldap', { method:'PUT', body: JSON.stringify(body) });
    msg.textContent = r.configured ? (r.synced_ok ? `saved — ${r.approved_identity_count} approved` : ('saved, sync failed: '+(r.last_error||''))) : 'saved (not configured)';
    msg.style.color = r.synced_ok || !r.configured ? 'var(--green)' : 'var(--red)';
    document.getElementById('ls-bind_password').value=''; loadLdapStatus();
  } catch(e){ msg.textContent = e.message; msg.style.color='var(--red)'; }
});
function _ldapFormBody() {
  const v = id => (document.getElementById(id)?.value || '').trim();
  const body = { url:v('ls-url'), bind_dn:v('ls-bind_dn'), user_base:v('ls-user_base'), group_dn:v('ls-group_dn'),
    user_attr:v('ls-user_attr'), mail_attr:v('ls-mail_attr'), name_attr:v('ls-name_attr'), member_attr:v('ls-member_attr'),
    start_tls:document.getElementById('ls-start_tls').checked, enforce:document.getElementById('ls-enforce').checked };
  const pw = v('ls-bind_password'); if (pw) body.bind_password = pw;
  return body;
}
document.getElementById('ls-test')?.addEventListener('click', async () => {
  const msg = document.getElementById('ls-msg'); const btn = document.getElementById('ls-test');
  btn.disabled = true; msg.textContent = 'testing…'; msg.style.color = 'var(--text-faint)';
  try {
    const r = await api('/api/settings/ldap/test', { method:'POST', body: JSON.stringify(_ldapFormBody()) });
    if (r.ok) { msg.innerHTML = `✓ bound — ${r.count} group member${r.count===1?'':'s'}${r.sample&&r.sample.length?` (${r.sample.map(esc).join(', ')}${r.count>r.sample.length?'…':''})`:''}`; msg.style.color = 'var(--green)'; }
    else { msg.textContent = '✕ ' + (r.error||'failed'); msg.style.color = 'var(--red)'; }
  } catch(e){ msg.textContent = e.message; msg.style.color = 'var(--red)'; }
  btn.disabled = false;
});
async function loadSmtpSettings() {
  try {
    const s = await api('/api/settings/smtp');
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
    set('sm-host', s.host); set('sm-port', s.port); set('sm-user', s.user); set('sm-from_addr', s.from_addr); set('sm-base_url', s.base_url);
    document.getElementById('sm-tls').checked = s.tls !== false;
    document.getElementById('sm-verify_cert').checked = s.verify_cert !== false;
    document.getElementById('sm-password').placeholder = s.password_set ? '•••• (set — leave blank to keep)' : '(optional)';
  } catch(e) {}
}
document.getElementById('sm-save')?.addEventListener('click', async () => {
  const v = id => (document.getElementById(id)?.value || '').trim();
  const body = { host:v('sm-host'), port:parseInt(v('sm-port')||'587',10), user:v('sm-user'),
    from_addr:v('sm-from_addr'), base_url:v('sm-base_url'), tls:document.getElementById('sm-tls').checked,
    verify_cert:document.getElementById('sm-verify_cert').checked };
  const pw = v('sm-password'); if (pw) body.password = pw;
  const msg = document.getElementById('sm-msg'); msg.textContent='saving…'; msg.style.color='var(--text-faint)';
  try { await api('/api/settings/smtp', { method:'PUT', body: JSON.stringify(body) }); msg.textContent='saved'; msg.style.color='var(--green)'; document.getElementById('sm-password').value=''; }
  catch(e){ msg.textContent=e.message; msg.style.color='var(--red)'; }
});
document.getElementById('sm-test')?.addEventListener('click', async () => {
  const v = id => (document.getElementById(id)?.value || '').trim();
  const to = prompt('Send a test email to:', v('sm-from_addr') || (me && me.reviewer) || '');
  if (to === null) return;
  const msg = document.getElementById('sm-msg'); const btn = document.getElementById('sm-test');
  btn.disabled = true; msg.textContent = 'sending…'; msg.style.color = 'var(--text-faint)';
  const body = { host:v('sm-host'), port:parseInt(v('sm-port')||'587',10), user:v('sm-user'),
    from_addr:v('sm-from_addr'), base_url:v('sm-base_url'), tls:document.getElementById('sm-tls').checked,
    verify_cert:document.getElementById('sm-verify_cert').checked, test_to: to };
  const pw = v('sm-password'); if (pw) body.password = pw;
  try {
    const r = await api('/api/settings/smtp/test', { method:'POST', body: JSON.stringify(body) });
    if (r.ok) { msg.textContent = `✓ sent to ${r.sent_to}`; msg.style.color = 'var(--green)'; }
    else { msg.textContent = '✕ ' + (r.error||'failed'); msg.style.color = 'var(--red)'; }
  } catch(e){ msg.textContent = e.message; msg.style.color = 'var(--red)'; }
  btn.disabled = false;
});

// ── Projects admin (create / archive / membership) ───────────────────────────
let _projApproved = null;   // cached approved-user list for member pickers
async function _projApprovedUsers() {
  if (_projApproved) return _projApproved;
  try { _projApproved = (await api('/api/ldap/approved')).users || []; } catch { _projApproved = []; }
  return _projApproved;
}
document.getElementById('newProjBtn')?.addEventListener('click', async () => {
  const slug = document.getElementById('newProjSlug').value.trim();
  const name = document.getElementById('newProjName').value.trim();
  const msg = document.getElementById('newProjMsg');
  if (!slug) { msg.textContent = 'slug required'; msg.style.color = 'var(--red)'; return; }
  try {
    await api('/api/projects', { method:'POST', body: JSON.stringify({ slug, name: name || slug }) });
    document.getElementById('newProjSlug').value = ''; document.getElementById('newProjName').value = '';
    msg.textContent = 'created'; msg.style.color = 'var(--green)';
    loadProjectsAdmin(); loadProjectSwitcher();
  } catch(e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
});
// Projects management — two-pane master/detail (#168 follow-up; mirrors the Customers tab).
let _selectedProjId = null;
let _projectsCache = [];
document.getElementById('newProjToggle')?.addEventListener('click', () => {
  const f = document.getElementById('newProjForm'); if (f) f.style.display = f.style.display === 'none' ? '' : 'none';
});
async function loadProjectsAdmin() {
  const el = document.getElementById('projectsList');
  if (!el) return;
  // Project creation is gated on the project.create privilege (platform admins by default).
  const canCreate = !!(me && (me.is_platform_admin || (me.privileges||[]).includes('project.create')));
  const tgl = document.getElementById('newProjToggle'); if (tgl) tgl.style.display = canCreate ? '' : 'none';
  try {
    const r = await api('/api/projects?show_archived=true');
    let projects = r.projects || [];
    // Project admins see only the projects they administer.
    if (me && !me.is_platform_admin && me.role !== 'admin') {
      projects = projects.filter(p => p.my_role === 'admin');
    }
    _projectsCache = projects;
    el.innerHTML = projects.map(p => `
      <div class="proj-item" data-pid="${p.id}" style="padding:8px 10px;border-radius:6px;cursor:pointer;margin-bottom:2px;${p.archived?'opacity:.55;':''}${p.id===_selectedProjId?'background:var(--bg-raised);':''}">
        <div style="display:flex;align-items:center;gap:6px;">
          <strong style="font-size:12px;flex:1;">${esc(p.name)}</strong>
          ${p.is_exclusive?'<span title="exclusive — sealed">🔒</span>':''}
          ${p.archived?'<span style="font-size:9px;color:var(--red);">archived</span>':''}
        </div>
        <div style="font-size:10px;color:var(--text-faint);"><code>${esc(p.slug)}</code> · ${p.member_count} member${p.member_count===1?'':'s'}</div>
      </div>`).join('') || '<div class="empty" style="padding:14px;color:var(--text-faint);">No projects.</div>';
    el.querySelectorAll('.proj-item').forEach(it => it.addEventListener('click', () => _selectProject(parseInt(it.dataset.pid, 10))));
    if (_selectedProjId && projects.some(p => p.id === _selectedProjId)) _selectProject(_selectedProjId);
    else if (!_selectedProjId && projects.length) _selectProject(projects[0].id);
    else if (_selectedProjId && !projects.some(p => p.id === _selectedProjId)) {
      _selectedProjId = null;
      const det = document.getElementById('projDetail'); if (det) det.innerHTML = '<div class="empty" style="padding:20px;color:var(--text-faint);">Select a project…</div>';
    }
  } catch(e) { el.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}

function _selectProject(pid) {
  _selectedProjId = pid;
  document.querySelectorAll('.proj-item').forEach(it => {
    it.style.background = (parseInt(it.dataset.pid, 10) === pid) ? 'var(--bg-raised)' : '';
  });
  const p = _projectsCache.find(x => x.id === pid);
  const det = document.getElementById('projDetail');
  if (!p || !det) return;
  const projects = _projectsCache;
  const canAdmin = me && (me.is_platform_admin || p.my_role === 'admin');
  const moveOpts = projects.filter(x => x.id !== p.id && !x.archived);
  det.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:2px;flex-wrap:wrap;">
      <div class="view-title" style="margin:0;">${esc(p.name)}</div>
      <code style="font-size:12px;color:var(--text-faint);">${esc(p.slug)}</code>
      ${p.archived?'<span style="font-size:10px;color:var(--red);border:1px solid var(--red);border-radius:10px;padding:0 8px;">archived</span>':''}
    </div>
    <div class="view-subtitle">${p.member_count} member${p.member_count===1?'':'s'} · the masthead switcher sets your active project; data is scoped to it.</div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0;padding:10px 12px;background:var(--bg-input,rgba(255,255,255,.03));border:1px solid var(--border);border-radius:8px;">
      <label style="display:inline-flex;align-items:center;gap:5px;font-size:11px;cursor:pointer;" title="Sealed — explicit grant required for everyone, incl. platform-admin"><input type="checkbox" id="pd-excl" ${p.is_exclusive?'checked':''} style="width:auto;height:auto;" /> 🔒 Exclusive</label>
      ${(me&&me.can_manage_uc_sources)?`<button class="btn ghost btn-sm" id="pd-ucstore" type="button">UC store</button>`:''}
      ${p.slug!=='default'?`<button class="btn ghost btn-sm" id="pd-archive" type="button">${p.archived?'Unarchive':'Archive'}</button>`:''}
      ${(me&&me.is_platform_admin&&moveOpts.length)?`<select id="pd-move" style="font-size:11px;"><option value="">move data →</option>${moveOpts.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select>`:''}
      <span style="flex:1;"></span>
      ${(p.slug!=='default'&&canAdmin)?`<a href="javascript:void(0)" id="pd-del" style="color:var(--red);font-size:11px;">delete project</a>`:''}
    </div>
    <div id="pd-ucstore-box" style="display:none;margin-bottom:14px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;"></div>
    <div class="cfg-card panel-card">
      <div class="panel-card-header"><div><div class="pc-title">Members</div><div class="pc-sub">Who can access this project, and with what role.</div></div></div>
      <div id="pd-members" style="padding:12px 14px;">Loading…</div>
    </div>`;
  det.querySelector('#pd-excl')?.addEventListener('change', async function() {
    try { await api(`/api/projects/${pid}`, { method:'PATCH', body: JSON.stringify({ is_exclusive: this.checked }) }); p.is_exclusive = this.checked; }
    catch(e){ toast(e.message, true); this.checked = !this.checked; }
  });
  det.querySelector('#pd-archive')?.addEventListener('click', async function() {
    try { await api(`/api/projects/${pid}`, { method:'PATCH', body: JSON.stringify({ archived: !p.archived }) }); loadProjectsAdmin(); loadProjectSwitcher(); }
    catch(e){ toast('Failed: ' + e.message, true); }
  });
  det.querySelector('#pd-move')?.addEventListener('change', async function() {
    const tgt = this.value; if (!tgt) return;
    const tgtName = this.options[this.selectedIndex].text;
    if (!confirm(`Move ALL data from "${p.name}" into "${tgtName}"? This reassigns use cases, analyses, sessions, Scoping Sets and cached outputs.`)) { this.value=''; return; }
    try { const rr = await api(`/api/projects/${pid}/move-data`, { method:'POST', body: JSON.stringify({ target_project_id: parseInt(tgt,10) }) });
      toast(`Moved ${rr.total} item(s): ${rr.source} → ${rr.target}`); loadProjectsAdmin(); loadProjectSwitcher(); }
    catch(e){ toast(e.message, true); this.value=''; }
  });
  det.querySelector('#pd-del')?.addEventListener('click', async function() {
    if (!confirm(`Delete project "${p.name}"? Its data must already be moved or removed.`)) return;
    try { await api(`/api/projects/${pid}`, { method:'DELETE' }); toast('Project deleted'); _selectedProjId = null; loadProjectsAdmin(); loadProjectSwitcher(); }
    catch(e){ toast(e.message, true); }
  });
  const ucBox = det.querySelector('#pd-ucstore-box');
  det.querySelector('#pd-ucstore')?.addEventListener('click', () => {
    if (ucBox.style.display === 'none') { ucBox.style.display = ''; _renderUcDestination(pid, ucBox); } else ucBox.style.display = 'none';
  });
  _renderProjectMembers(pid, det.querySelector('#pd-members'));
}
// UC git destination for a project (Phase 2 — where its use cases live).
async function _renderUcDestination(pid, box) {
  box.innerHTML = 'Loading…';
  try {
    const dest = await api(`/api/projects/${pid}/uc-destination`);
    const repoResp = await api('/api/repos?role=uc-store');
    const repos = repoResp.repos || [];
    const opts = ['<option value="">— global default —</option>'].concat(repos.map(r => {
      const pvc = (r.metadata && r.metadata.provider === 'pvc-local') || r.provider === 'pvc-local';
      return `<option value="${esc(r.uuid)}"${dest.repo_uuid===r.uuid?' selected':''}>${esc(r.display_name||r.namespace)}${pvc?' (DAV-hosted)':''}</option>`;
    })).join('');
    box.innerHTML = `
      <div style="font-size:11px;color:var(--text-faint);margin-bottom:4px;">Git home for this project's use cases (a uc-store repo). Per-UC overrides win.</div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <select id="ucd-repo-${pid}" style="font-size:11px;">${opts}</select>
        <input id="ucd-path-${pid}" placeholder="path (optional)" value="${esc(dest.path||'')}" style="font-size:11px;width:120px;">
        <input id="ucd-branch-${pid}" placeholder="branch" value="${esc(dest.branch||'')}" style="font-size:11px;width:90px;">
        <button class="btn primary btn-sm ucd-save" data-pid="${pid}">Save</button>
        <span id="ucd-msg-${pid}" style="font-size:11px;color:var(--text-faint);"></span>
      </div>
      <div style="margin-top:6px;font-size:11px;">
        <a href="javascript:void(0)" class="ucd-new-toggle" data-pid="${pid}" style="color:var(--accent)">+ Let DAV host a new store</a>
        <span id="ucd-new-form-${pid}" style="display:none;margin-left:6px;">
          <input id="ucd-new-ns-${pid}" placeholder="namespace (a-z0-9-)" style="font-size:11px;width:150px;">
          <button class="btn ghost btn-sm ucd-new-go" data-pid="${pid}">Create</button>
        </span>
      </div>`;
    box.querySelector('.ucd-save').addEventListener('click', async function() {
      const repo_uuid = document.getElementById(`ucd-repo-${pid}`).value || null;
      const path = document.getElementById(`ucd-path-${pid}`).value;
      const branch = document.getElementById(`ucd-branch-${pid}`).value;
      const msg = document.getElementById(`ucd-msg-${pid}`);
      try { await api(`/api/projects/${pid}/uc-destination`, { method:'PUT', body: JSON.stringify({ repo_uuid, path, branch }) }); msg.textContent = 'saved'; msg.style.color = 'var(--green)'; }
      catch(e){ msg.textContent = e.message; msg.style.color = 'var(--red)'; }
    });
    box.querySelector('.ucd-new-toggle').addEventListener('click', function() {
      const f = document.getElementById(`ucd-new-form-${pid}`); f.style.display = f.style.display === 'none' ? '' : 'none';
    });
    box.querySelector('.ucd-new-go').addEventListener('click', async function() {
      const ns = (document.getElementById(`ucd-new-ns-${pid}`).value || '').trim();
      if (!ns) return;
      try { await api('/api/repos/pvc-local', { method:'POST', body: JSON.stringify({ namespace: ns }) }); toast('DAV-hosted store created'); _renderUcDestination(pid, box); }
      catch(e){ toast(e.message, true); }
    });
  } catch(e) { box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}
// Project membership = per-project RBAC role assignments (same model as the
// Accounts panel). A user may hold multiple project roles.
const _LEGACY_PROJ_ROLE = { 'project-admin':'admin', 'project-edit':'editor', 'project-viewer':'viewer' };
// Reusable type-ahead user picker (#133/#134) — unifies "add member" across project + customer
// surfaces. `accounts` = [{reviewer,email,display_name}]; `excludeSet` = lowercased reviewers to omit
// (current members, so you only pick NON-members). On pick, sets input.value (display) +
// input.dataset.reviewer (the id) and calls onPick(reviewer). Typing clears the pick — you must select.
function userPickerHtml(inputId, ddId, placeholder) {
  return `<div style="position:relative;flex:1;min-width:140px;">
    <input id="${inputId}" placeholder="${esc(placeholder || '+ add user…')}" autocomplete="off" style="font-size:11px;width:100%;" />
    <div id="${ddId}" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:200;background:var(--bg-panel);border:1px solid var(--border-bright);border-radius:6px;min-width:340px;max-height:340px;overflow:auto;box-shadow:0 8px 24px rgba(0,0,0,0.45);margin-top:3px;"></div>
  </div>`;
}
function wireUserPicker(inputId, ddId, accounts, excludeSet, onPick) {
  const inp = document.getElementById(inputId), dd = document.getElementById(ddId);
  if (!inp || !dd) return;
  const pool = (accounts || []).filter(a => !excludeSet.has((a.reviewer || '').toLowerCase()));
  const close = () => { dd.style.display = 'none'; };
  const render = (q) => {
    q = (q || '').toLowerCase().trim();
    const matches = pool.filter(a =>
      (a.reviewer || '').toLowerCase().includes(q) || (a.email || '').toLowerCase().includes(q) || (a.display_name || '').toLowerCase().includes(q)
    ).slice(0, 20);
    dd.innerHTML = matches.length
      ? matches.map(a => `<div class="up-item" data-rev="${esc(a.reviewer)}" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);line-height:1.35;" onmouseover="this.style.background='var(--bg-raised)'" onmouseout="this.style.background=''"><div style="font-size:13px;">${esc(a.display_name || a.reviewer)}</div><div style="color:var(--text-faint);font-size:11px;">${esc(a.email || a.reviewer)}</div></div>`).join('')
      : `<div style="padding:8px 12px;font-size:12px;color:var(--text-faint);">${q ? 'no matching non-members' : (pool.length ? 'type to search…' : 'everyone is already a member')}</div>`;
    dd.style.display = '';
    dd.querySelectorAll('.up-item').forEach(it => it.addEventListener('mousedown', (e) => {
      e.preventDefault();   // beat the input's blur so the pick registers
      const a = pool.find(x => x.reviewer === it.dataset.rev);
      inp.value = (a.display_name || a.reviewer); inp.dataset.reviewer = a.reviewer; close();
      if (onPick) onPick(a.reviewer);
    }));
  };
  inp.addEventListener('input', () => { inp.dataset.reviewer = ''; render(inp.value); });
  inp.addEventListener('focus', () => render(inp.value));
  inp.addEventListener('blur', () => setTimeout(close, 150));
}

async function _renderProjectMembers(pid, box) {
  box.innerHTML = 'Loading…';
  try {
    const mResp = await api(`/api/projects/${pid}/members`);
    const approved = await _projApprovedUsers();
    const rolesResp = await api('/api/rbac/roles');
    const projRoles = (rolesResp.roles||[]).filter(r => r.scope === 'project');
    const roleOpts = projRoles.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
    const members = mResp.members || [];
    box.innerHTML = members.map(m => `
      <div style="display:flex;gap:8px;align-items:center;padding:3px 0;">
        <span style="flex:1;">${esc(m.display_name||m.reviewer)} <span style="color:var(--text-faint);font-size:11px;">${esc(m.email||m.reviewer)}</span></span>
        <span style="font-size:10px;background:var(--bg-input);border:1px solid var(--border);border-radius:10px;padding:1px 8px;">${esc(m.role_name||m.role_key)}</span>
        <button class="btn ghost btn-sm pm-remove" data-pid="${pid}" data-rev="${esc(m.reviewer)}" data-role="${m.role_id}" style="color:var(--red);" title="Revoke this role">✕</button>
      </div>`).join('') +
      `<div style="display:flex;gap:6px;align-items:center;margin-top:6px;">
        ${userPickerHtml(`pm-add-${pid}`, `pm-add-dd-${pid}`, '+ add user…')}
        <select id="pm-add-role-${pid}" style="font-size:11px;">${roleOpts}</select>
        <button class="btn ghost btn-sm pm-add-btn" data-pid="${pid}">Add</button>
      </div>` +
      `<div style="margin-top:8px;border-top:1px dashed var(--border);padding-top:8px;">
        <div style="font-size:10px;color:var(--text-faint);margin-bottom:4px;">Invite a new user by email (they set a password &amp; join with this role):</div>
        <div style="display:flex;gap:6px;">
          <input id="inv-email-${pid}" placeholder="name@org" style="font-size:11px;flex:1;" />
          <select id="inv-role-${pid}" style="font-size:11px;">${roleOpts}</select>
          <button class="btn ghost btn-sm inv-send-btn" data-pid="${pid}">Invite</button>
        </div>
        <div id="inv-result-${pid}" style="font-size:10px;margin-top:5px;word-break:break-all;"></div>
      </div>`;
    box.querySelectorAll('.pm-remove').forEach(b => b.addEventListener('click', async function() {
      try { await api(`/api/projects/${this.dataset.pid}/members/${encodeURIComponent(this.dataset.rev)}?role_id=${this.dataset.role}`, { method:'DELETE' }); _renderProjectMembers(pid, box); loadProjectsAdmin(); loadProjectSwitcher(); } catch(e){ toast(e.message, true); }
    }));
    const _pmExclude = new Set(members.map(m => (m.reviewer || '').toLowerCase()));
    wireUserPicker(`pm-add-${pid}`, `pm-add-dd-${pid}`, approved, _pmExclude, null);
    box.querySelector('.pm-add-btn')?.addEventListener('click', async function() {
      const rev = document.getElementById(`pm-add-${pid}`).dataset.reviewer || '';
      const role_id = parseInt(document.getElementById(`pm-add-role-${pid}`).value, 10);
      if (!rev) { toast('Pick a user from the list', true); return; }
      try { await api(`/api/projects/${pid}/members`, { method:'POST', body: JSON.stringify({ reviewer:rev, role_id }) }); _renderProjectMembers(pid, box); loadProjectsAdmin(); loadProjectSwitcher(); } catch(e){ toast(e.message, true); }
    });
    box.querySelector('.inv-send-btn')?.addEventListener('click', async function() {
      const email = (document.getElementById(`inv-email-${pid}`).value || '').trim();
      const role_id = parseInt(document.getElementById(`inv-role-${pid}`).value, 10);
      const out = document.getElementById(`inv-result-${pid}`);
      if (!email) { out.textContent = 'email required'; out.style.color = 'var(--red)'; return; }
      out.textContent = 'creating invite…'; out.style.color = 'var(--text-faint)';
      try {
        const rk = (projRoles.find(r=>r.id===role_id)||{}).key;
        const legacy = _LEGACY_PROJ_ROLE[rk] || 'viewer';
        const r = await api('/api/invites', { method:'POST', body: JSON.stringify({ email, project_id: parseInt(pid,10), project_role: legacy }) });
        const link = (r.link && r.link.startsWith('http')) ? r.link : (location.origin + (r.link||''));
        out.innerHTML = (r.emailed ? '✓ emailed · ' : ('⚠ not emailed' + (r.email_error ? ' (' + esc(r.email_error) + ')' : ' (no SMTP)') + ' · '))
          + `<a href="javascript:void(0)" id="inv-copy-${pid}" style="color:var(--accent)">copy link</a>`;
        out.style.color = 'var(--text-dim)';
        document.getElementById(`inv-copy-${pid}`)?.addEventListener('click', () => { navigator.clipboard.writeText(link).then(()=>toast('Invite link copied')); });
        document.getElementById(`inv-email-${pid}`).value = '';
      } catch(e) { out.textContent = e.message; out.style.color = 'var(--red)'; }
    });
  } catch(e) { box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}

async function loadLdapStatus() {
  const body = document.getElementById('ldapStatusBody');
  const chip = document.getElementById('ldapStatusChip');
  if (!body) return;
  try {
    const s = await api('/api/ldap/status');
    if (chip) {
      chip.textContent = s.configured ? (s.enforcing ? 'enforcing' : 'configured · not enforcing') : 'not configured';
      chip.style.color = (s.configured && s.synced_ok) ? 'var(--ok)' : 'var(--text-faint)';
    }
    body.innerHTML = s.configured
      ? `<div>Server: <code>${esc(s.url||'')}</code></div>
         <div>Approval group: <code>${esc(s.group_dn||'')}</code></div>
         <div>Last sync: ${s.synced_ok ? '<span style="color:var(--ok)">ok</span>' : '<span style="color:var(--red)">not yet</span>'} — ${s.group_member_count} group member(s), ${s.approved_identity_count} approved identit${s.approved_identity_count===1?'y':'ies'}</div>
         ${s.last_error ? `<div style="color:var(--red)">Last error: ${esc(s.last_error)}</div>` : ''}
         <div>Enforcement: <strong>${s.enforcing ? 'ON' : 'OFF'}</strong>${s.enforcing ? '' : ' — set <code>DAV_LDAP_ENFORCE=true</code> once the user list looks right'}</div>
         <div>Bootstrap admins: ${(s.bootstrap_admins||[]).map(esc).join(', ') || '<span style="color:var(--text-faint)">none</span>'}</div>`
      : `<div style="color:var(--text-faint)">LDAP is not configured. Set the <code>DAV_LDAP_*</code> env vars (from a Secret) on the API to enable approved-user access. The gate stays a no-op until configured <em>and</em> <code>DAV_LDAP_ENFORCE=true</code>.</div>`;
  } catch(e) { body.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}

document.getElementById('ldapSyncBtn')?.addEventListener('click', async () => {
  const msg = document.getElementById('ldapSyncMsg'); const btn = document.getElementById('ldapSyncBtn');
  btn.disabled = true; msg.textContent = 'syncing…'; msg.style.color = 'var(--text-faint)';
  try {
    const r = await api('/api/ldap/sync', { method:'POST', body:'{}' });
    msg.textContent = r.synced_ok ? `synced — ${r.approved_identity_count} approved` : ('failed: ' + (r.last_error||''));
    msg.style.color = r.synced_ok ? 'var(--green)' : 'var(--red)';
    loadLdapStatus(); loadUsers();
  } catch(e) { msg.textContent = 'error: ' + e.message; msg.style.color = 'var(--red)'; }
  btn.disabled = false;
});

let _rbacRoles = [];
let _rbacProjects = [];

// Back-compat alias — callers refresh the whole Users & roles panel.
async function loadUsers() { loadAccounts(); loadRolesMatrix(); }

// ── Bundles (#107) — Config → Platform → Bundles ─────────────────────────────
// Reusable, versioned packages of config/capability items. Assemble → publish
// (snapshots non-secret defs) → attach to a project/use-category (scope-resolved).
let _bundleOpenId = null;
async function loadBundles() {
  const el = document.getElementById('bundlesList');
  if (!el) return;
  try {
    const bundles = await api('/api/bundles');
    if (!bundles.length) {
      el.innerHTML = '<div style="color:var(--text-faint);padding:8px 0;">No bundles yet. Create one, add items, publish a version, then attach it to a project or use-category.</div>';
      const d = document.getElementById('bundleDetail'); if (d) d.style.display = 'none';
      return;
    }
    el.innerHTML = bundles.map(b => `
      <div style="display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--border);">
        <span style="flex:1;cursor:pointer;" onclick="openBundle(${b.id})"><strong>${esc(b.name)}</strong>
          <span style="font-size:10px;background:var(--bg-raised);border:1px solid var(--border);padding:0 5px;border-radius:2px;">${esc(b.kind)}</span>
          <span style="color:var(--text-faint);font-size:11px;"> · ${b.item_count} item${b.item_count===1?'':'s'} · ${b.versions} ver · ${b.attachments} attach${b.current_version_id?'':' · <span style="color:var(--accent);">unpublished</span>'}</span></span>
        <button class="btn ghost btn-sm" onclick="openBundle(${b.id})">Open</button>
        <a href="javascript:void(0)" onclick="_armDeleteBtn(this, () => deleteBundle(${b.id}))" style="color:var(--red);font-size:11px;">delete</a>
      </div>`).join('');
    if (_bundleOpenId && bundles.some(b => b.id === _bundleOpenId)) openBundle(_bundleOpenId);
  } catch (e) { el.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}
function _bundleNew() {
  const f = document.getElementById('bundleNewForm');
  if (f) f.style.display = f.style.display === 'none' ? 'flex' : 'none';
}
async function createBundle() {
  const name = (document.getElementById('bundleNewName').value || '').trim();
  if (!name) { toast('name required', true); return; }
  const kind = document.getElementById('bundleNewKind').value;
  const description = (document.getElementById('bundleNewDesc').value || '').trim();
  try {
    const b = await api('/api/bundles', { method: 'POST', body: JSON.stringify({ name, kind, description }) });
    toast('✓ bundle created');
    document.getElementById('bundleNewName').value = '';
    document.getElementById('bundleNewDesc').value = '';
    _bundleNew(); _bundleOpenId = b.id; await loadBundles();
  } catch (e) { toast(e.message, true); }
}
async function deleteBundle(id) {
  try {
    await api(`/api/bundles/${id}`, { method: 'DELETE' });
    toast('bundle deleted');
    if (_bundleOpenId === id) { _bundleOpenId = null; const d = document.getElementById('bundleDetail'); if (d) d.style.display = 'none'; }
    await loadBundles();
  } catch (e) { toast(e.message, true); }
}
async function openBundle(id) {
  _bundleOpenId = id;
  const box = document.getElementById('bundleDetail');
  if (!box) return;
  box.style.display = '';
  box.innerHTML = '<div style="color:var(--text-faint);">loading…</div>';
  try {
    const b = await api(`/api/bundles/${id}`);
    const latest = (b.version_list || [])[0];
    const isDraft = latest && latest.status === 'draft';
    const items = (b.items || []).map(i => `
      <div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11px;">
        <span style="background:var(--bg-raised);border:1px solid var(--border);padding:0 4px;border-radius:2px;font-size:9px;">${esc(i.item_type)}</span>
        <span style="flex:1;">${esc((i.item_data && (i.item_data.name || i.item_data.model_id)) || (i.source_id ? ('#' + i.source_id) : '(item)'))}</span>
        ${isDraft ? `<a href="javascript:void(0)" onclick="_delBundleItem(${i.id})" style="color:var(--red);font-size:10px;">remove</a>` : ''}
      </div>`).join('') || '<div style="color:var(--text-faint);font-size:11px;">no items yet</div>';
    const attachments = (b.attachment_list || []).map(a => `
      <div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11px;">
        <span style="flex:1;">${a.project_id ? 'project ' + a.project_id : 'platform'}${a.use_category ? (' · ' + esc(a.use_category)) : ' · any use-category'}</span>
        <a href="javascript:void(0)" onclick="_detachBundle(${a.id})" style="color:var(--red);font-size:10px;">detach</a>
      </div>`).join('') || '<div style="color:var(--text-faint);font-size:11px;">not attached anywhere</div>';
    box.innerHTML = `
      <div style="display:flex;align-items:baseline;gap:10px;">
        <div style="font-size:13px;font-weight:600;flex:1;">${esc(b.name)} <span style="font-size:10px;color:var(--text-faint);">${esc(b.slug)} · ${isDraft ? '<span style="color:var(--accent)">editing draft v' + (latest ? latest.version_no : '?') + '</span>' : 'published v' + (latest ? latest.version_no : '?')}</span></div>
        ${isDraft ? `<button class="btn primary btn-sm" onclick="publishBundle(${id})">Publish version</button>` : ''}
      </div>
      <div style="margin-top:8px;"><div style="font-size:10px;text-transform:uppercase;color:var(--text-faint);letter-spacing:.05em;margin-bottom:3px;">Items</div>${items}</div>
      <div style="display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap;">
        <select id="biType-${id}" style="font-size:11px;" onchange="_biLoadSources(${id})"><option value="mcp_server">MCP server</option><option value="model_config">Model</option></select>
        <select id="biSource-${id}" style="font-size:11px;min-width:200px;"><option value="">— pick an item —</option></select>
        <button class="btn ghost btn-sm" onclick="addBundleItem(${id})">Add to draft</button>
      </div>
      <div style="margin-top:10px;"><div style="font-size:10px;text-transform:uppercase;color:var(--text-faint);letter-spacing:.05em;margin-bottom:3px;">Attached</div>${attachments}</div>
      ${b.current_version_id ? `<div style="display:flex;gap:6px;align-items:center;margin-top:6px;flex-wrap:wrap;">
        <span style="font-size:11px;color:var(--text-dim);">Attach published version to:</span>
        <button class="btn ghost btn-sm" onclick="attachBundle(${id}, 'project')">this project</button>
        <select id="baCat-${id}" style="font-size:11px;"><option value="">— use-category —</option><option value="assessment">assessment</option><option value="arch-review">arch-review</option><option value="uc-gap-analysis">uc-gap-analysis</option><option value="enhancement">enhancement</option><option value="evaluation">evaluation</option></select>
        <button class="btn ghost btn-sm" onclick="attachBundle(${id}, 'usecat')">use-category (platform-wide)</button>
      </div>` : '<div style="font-size:10px;color:var(--text-faint);margin-top:6px;">Publish a version to enable attaching.</div>'}
    `;
    _biLoadSources(id);
  } catch (e) { box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}
async function _biLoadSources(id) {
  const type = document.getElementById(`biType-${id}`) ? document.getElementById(`biType-${id}`).value : 'mcp_server';
  const sel = document.getElementById(`biSource-${id}`);
  if (!sel) return;
  try {
    const data = await api(type === 'model_config' ? '/api/models' : '/api/mcp-servers');
    // Exclude rows that are themselves materialized from a bundle (no re-bundling).
    sel.innerHTML = '<option value="">— pick an item —</option>' + (data || []).filter(x => x.id && !x.from_bundle).map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
  } catch (e) { /* leave the picker as-is on error */ }
}
async function addBundleItem(id) {
  const item_type = document.getElementById(`biType-${id}`).value;
  const source_id = parseInt(document.getElementById(`biSource-${id}`).value || '', 10);
  if (!source_id) { toast('pick an item', true); return; }
  try { await api(`/api/bundles/${id}/items`, { method: 'POST', body: JSON.stringify({ item_type, source_id }) }); toast('item added to draft'); await openBundle(id); loadBundles(); }
  catch (e) { toast(e.message, true); }
}
async function _delBundleItem(iid) {
  try { await api(`/api/bundle-items/${iid}`, { method: 'DELETE' }); if (_bundleOpenId) openBundle(_bundleOpenId); loadBundles(); }
  catch (e) { toast(e.message, true); }
}
async function publishBundle(id) {
  try { await api(`/api/bundles/${id}/publish`, { method: 'POST' }); toast('✓ version published'); await openBundle(id); loadBundles(); }
  catch (e) { toast(e.message, true); }
}
async function attachBundle(id, scope) {
  const body = {};
  if (scope === 'project') {
    body.project_id = parseInt(_activeProject || '0', 10) || null;
    if (!body.project_id) { toast('no active project selected', true); return; }
  } else if (scope === 'usecat') {
    const c = document.getElementById(`baCat-${id}`) ? document.getElementById(`baCat-${id}`).value : '';
    if (!c) { toast('pick a use-category', true); return; }
    body.use_category = c;
  }
  try { await api(`/api/bundles/${id}/attach`, { method: 'POST', body: JSON.stringify(body) }); toast('✓ attached'); await openBundle(id); loadBundles(); }
  catch (e) { toast(e.message, true); }
}
async function _detachBundle(aid) {
  try { await api(`/api/bundle-attachments/${aid}`, { method: 'DELETE' }); toast('detached'); if (_bundleOpenId) openBundle(_bundleOpenId); loadBundles(); }
  catch (e) { toast(e.message, true); }
}

// #39 identity unification: link an alias identity (uid / old key / 2nd email) into a canonical
// account, optionally migrating its roles + settings and removing the duplicate account.
async function _linkIdentity(reviewer, email) {
  const alias = prompt(`Unify another identity into ${email}.\n\nEnter the OTHER identity (a uid, an old login, or a second email) that should resolve to this account:`);
  if (!alias || !alias.trim()) return;
  const migrate = confirm(`Migrate ${alias.trim()}'s existing roles + settings onto ${email}, and remove its duplicate account?\n\nOK = unify (recommended)   ·   Cancel = alias only (no migration)`);
  try {
    await api(`/api/accounts/${encodeURIComponent(reviewer)}/identities`, { method: 'POST', body: JSON.stringify({ alias: alias.trim(), migrate }) });
    toast(`Linked ${alias.trim()} → ${email}${migrate ? ' (roles migrated)' : ''}`);
    loadAccounts();
  } catch (e) { toast(e.message, true); }
}
async function loadAccounts() {
  const el = document.getElementById('accountsList');
  const chip = document.getElementById('usersCountChip');
  if (!el) return;
  try {
    const aResp = await api('/api/accounts');
    const rResp = await api('/api/rbac/roles');
    const pResp = await api('/api/projects');
    const accounts = aResp.accounts || [];
    _rbacRoles = rResp.roles || [];
    _rbacProjects = (pResp.projects || []).filter(p => !p.archived);
    if (chip) chip.textContent = `${accounts.length} account${accounts.length===1?'':'s'}`;
    // Platform + Cross-project roles bind globally (no project); Project roles bind per-project.
    const platRoles = _rbacRoles.filter(r => r.scope === 'platform' || r.scope === 'cross-project');
    const projRoles = _rbacRoles.filter(r => r.scope === 'project');
    el.innerHTML = accounts.map(a => {
      const roleChips = (a.roles||[]).map(r => {
        const lbl = r.scope==='project' ? `${esc(r.name)} · ${esc(r.project_name||('#'+r.project_id))}` : esc(r.name);
        return `<span style="display:inline-flex;align-items:center;gap:4px;background:var(--bg-input);border:1px solid var(--border);border-radius:10px;padding:1px 7px;font-size:10px;">${lbl}<a href="javascript:void(0)" class="role-rm" data-rev="${esc(a.reviewer)}" data-role="${r.role_id}" data-proj="${r.project_id==null?'':r.project_id}" style="color:var(--text-faint);">✕</a></span>`;
      }).join(' ') || '<span style="color:var(--text-faint);font-size:10px;">no roles</span>';
      const platOpts = platRoles.map(r => `<option value="p:${r.id}">+ ${esc(r.name)}</option>`).join('');
      const projOpts = projRoles.map(r => _rbacProjects.map(p => `<option value="r:${r.id}:${p.id}">+ ${esc(r.name)} · ${esc(p.name)}</option>`).join('')).join('');
      return `<div style="display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);flex-wrap:wrap;">
        <span style="flex:1;min-width:140px;"><strong>${esc(a.display_name||a.reviewer)}</strong> <span style="color:var(--text-faint);font-size:11px;">${esc(a.email||a.reviewer)}</span>${a.is_default_admin?' <span style="font-size:9px;color:var(--accent);">default</span>':''}${(a.aliases||[]).map(al=>`<span title="alias identity → resolves to this account" style="display:inline-flex;align-items:center;gap:2px;font-size:9px;background:var(--bg-input);border:1px solid var(--border);border-radius:8px;padding:0 5px;margin-left:4px;">🔗 ${esc(al)} <a href="javascript:void(0)" class="acct-unalias" data-rev="${esc(a.reviewer)}" data-alias="${esc(al)}" style="color:var(--text-faint);">✕</a></span>`).join('')}</span>
        <span style="display:flex;gap:4px;flex-wrap:wrap;flex:2;min-width:160px;">${roleChips}</span>
        <select class="acct-assign" data-rev="${esc(a.reviewer)}" style="font-size:11px;max-width:170px;"><option value="">assign role…</option>${platOpts}${projOpts}</select>
        <label style="font-size:10px;display:flex;align-items:center;gap:3px;"><input type="checkbox" class="acct-enabled" data-rev="${esc(a.reviewer)}" ${a.enabled?'checked':''}> enabled</label>
        ${(!a.has_password && (a.email||'').indexOf('@')>=0)?`<a href="javascript:void(0)" class="acct-invite" data-rev="${esc(a.reviewer)}" style="color:var(--accent);font-size:11px;">send invite</a>`:''}
        <a href="javascript:void(0)" class="acct-alias" data-rev="${esc(a.reviewer)}" data-email="${esc(a.email||a.reviewer)}" style="color:var(--accent);font-size:11px;" title="Unify another identity (a uid, an old login, or a 2nd email) into this account">🔗 link</a>
        ${(me && a.reviewer===me.reviewer)?'<span style="font-size:10px;color:var(--text-faint);">(you)</span>':`<a href="javascript:void(0)" class="acct-del" data-rev="${esc(a.reviewer)}" data-default="${a.is_default_admin?1:0}" style="color:var(--red);font-size:11px;">${a.is_default_admin?'deactivate':'delete'}</a>`}
      </div>`;
    }).join('') || '<div style="color:var(--text-faint)">No accounts yet.</div>';
    el.querySelectorAll('.acct-assign').forEach(s => s.addEventListener('change', async function() {
      const v = this.value; if (!v) return; const rev = this.dataset.rev;
      let body;
      if (v.startsWith('p:')) body = { role_id: parseInt(v.slice(2),10) };
      else { const parts = v.split(':'); body = { role_id: parseInt(parts[1],10), project_id: parseInt(parts[2],10) }; }
      try { await api(`/api/accounts/${encodeURIComponent(rev)}/roles`, {method:'POST', body: JSON.stringify(body)}); toast('role assigned'); loadAccounts(); }
      catch(e){ toast(e.message, true); this.value=''; }
    }));
    el.querySelectorAll('.role-rm').forEach(a => a.addEventListener('click', async function() {
      const rev=this.dataset.rev, rid=this.dataset.role, proj=this.dataset.proj;
      const q = proj!=='' ? `?role_id=${rid}&project_id=${proj}` : `?role_id=${rid}`;
      try { await api(`/api/accounts/${encodeURIComponent(rev)}/roles${q}`, {method:'DELETE'}); loadAccounts(); }
      catch(e){ toast(e.message, true); }
    }));
    el.querySelectorAll('.acct-enabled').forEach(c => c.addEventListener('change', async function() {
      try { const r = await api(`/api/accounts/${encodeURIComponent(this.dataset.rev)}`, {method:'PATCH', body: JSON.stringify({enabled: this.checked})}); if (r && r.warning) toast(r.warning); else toast(this.checked?'enabled':'disabled'); loadAccounts(); }
      catch(e){ toast(e.message, true); loadAccounts(); }
    }));
    el.querySelectorAll('.acct-del').forEach(a => a.addEventListener('click', async function() {
      const isDefault = this.dataset.default==='1';
      if (!confirm(`${isDefault?'Deactivate the break-glass default account':'Delete account'} ${this.dataset.rev}?`)) return;
      try { const r = await api(`/api/accounts/${encodeURIComponent(this.dataset.rev)}`, {method:'DELETE'}); toast(r && r.warning ? r.warning : (r && r.deactivated ? 'Account deactivated' : 'Account deleted')); loadAccounts(); }
      catch(e){ toast(e.message, true); }
    }));
    el.querySelectorAll('.acct-alias').forEach(a => a.addEventListener('click', function() { _linkIdentity(this.dataset.rev, this.dataset.email); }));
    el.querySelectorAll('.acct-unalias').forEach(a => a.addEventListener('click', async function() {
      if (!confirm(`Unlink alias ${this.dataset.alias}? (does not restore the old account or un-migrate roles)`)) return;
      try { await api(`/api/accounts/${encodeURIComponent(this.dataset.rev)}/identities/${encodeURIComponent(this.dataset.alias)}`, { method: 'DELETE' }); toast('alias unlinked'); loadAccounts(); }
      catch (e) { toast(e.message, true); }
    }));
    el.querySelectorAll('.acct-invite').forEach(a => a.addEventListener('click', async function() {
      try {
        const r = await api(`/api/accounts/${encodeURIComponent(this.dataset.rev)}/invite`, {method:'POST'});
        if (r.emailed) toast('Invite emailed');
        else {
          const abs=(r.link&&r.link.startsWith('http'))?r.link:(location.origin+(r.link||''));
          if (r.email_error) toast('⚠ not emailed: ' + r.email_error + ' — link copied (see Audit)', true);
          navigator.clipboard.writeText(abs).then(()=>{ if(!r.email_error) toast('SMTP off — invite link copied'); });
        }
      } catch(e){ toast(e.message, true); }
    }));
  } catch(e) { el.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}

document.getElementById('acctAddBtn')?.addEventListener('click', async () => {
  const email = (document.getElementById('acctNewEmail').value||'').trim();
  const display_name = (document.getElementById('acctNewName').value||'').trim();
  const password = document.getElementById('acctNewPw').value;
  const msg = document.getElementById('acctAddMsg');
  if (!email) { msg.textContent='email required'; msg.style.color='var(--red)'; return; }
  try {
    const r = await api('/api/accounts', {method:'POST', body: JSON.stringify({email, display_name, password: password||null})});
    document.getElementById('acctNewEmail').value=''; document.getElementById('acctNewName').value=''; document.getElementById('acctNewPw').value='';
    if (r.invited && r.emailed) { msg.textContent='account added · invite emailed'; msg.style.color='var(--green)'; }
    else if (r.invited) { _showInviteLink(msg, r.link); if (r.email_error) toast('⚠ invite not emailed: ' + r.email_error + ' (see Audit)', true); }
    else { msg.textContent='account added (password set)'; msg.style.color='var(--green)'; }
    loadAccounts();
  } catch(e){ msg.textContent=e.message; msg.style.color='var(--red)'; }
});
// Render a "copy invite link" affordance (SMTP not configured / not emailed).
function _showInviteLink(el, link) {
  const abs = (link && link.startsWith('http')) ? link : (location.origin + (link||''));
  el.innerHTML = 'account added · <a href="javascript:void(0)" class="inv-copy-link" style="color:var(--accent)">copy invite link</a>';
  el.style.color = 'var(--text-dim)';
  el.querySelector('.inv-copy-link').addEventListener('click', () => navigator.clipboard.writeText(abs).then(()=>toast('Invite link copied')));
}

async function loadRolesMatrix() {
  const el = document.getElementById('rolesMatrix');
  if (!el) return;
  try {
    const rResp = await api('/api/rbac/roles');
    const pResp = await api('/api/rbac/privileges');
    const roles = rResp.roles || [];
    const privs = pResp.privileges || [];
    const RANK = { 'platform':3, 'cross-project':2, 'project':1 };
    const SCOPE_LABEL = { 'platform':'Platform', 'cross-project':'Cross-project', 'project':'Project' };
    const SCOPE_DESC = {
      'platform':'The platform itself — settings, accounts, roles, repos. Bound globally.',
      'cross-project':'Project-related but not tied to one project (e.g. create projects). Bound globally.',
      'project':'A single project — its data, settings, members, deletion. Bound per-project.' };
    const roleCard = (r) => {
      // A role may hold privileges of its own scope or narrower (Platform ⊇ Cross-project ⊇ Project).
      const chips = privs.filter(p => (RANK[p.scope]||1) <= (RANK[r.scope]||1)).map(p => {
        const on = (r.privileges||[]).includes(p.key);
        return `<label title="${esc(p.key)} — ${esc(p.description)}" style="display:inline-flex;align-items:center;gap:5px;font-size:12px;background:var(--bg-input);border:1px solid var(--border);border-radius:5px;padding:3px 9px;margin:3px;cursor:pointer;"><input type="checkbox" class="rp-cell" data-role="${r.id}" data-priv="${esc(p.key)}" ${on?'checked':''}> ${esc(p.name)} <span style="font-size:9px;color:var(--text-faint);">${esc(p.scope)}</span></label>`;
      }).join('');
      return `<div style="padding:9px 0;border-bottom:1px solid var(--border);">
        <div style="margin-bottom:3px;"><strong style="font-size:13px;">${esc(r.name)}</strong>
          <span style="font-size:11px;color:var(--text-faint);"> · ${r.is_system?'built-in':'custom'} · ${r.assignment_count||0} binding${(r.assignment_count||0)===1?'':'s'}</span>
          ${r.is_system?'':`<a href="javascript:void(0)" class="role-del" data-role="${r.id}" style="color:var(--red);font-size:11px;margin-left:8px;">delete</a>`}</div>
        <div style="display:flex;flex-wrap:wrap;">${chips || '<span style="font-size:11px;color:var(--text-faint);">no privileges</span>'}</div>
      </div>`;
    };
    const groups = ['platform','cross-project','project'].map(sc => {
      const rs = roles.filter(r => r.scope === sc);
      if (!rs.length) return '';
      return `<div style="margin-top:12px;"><div style="font-size:12px;font-weight:600;color:var(--accent);">${SCOPE_LABEL[sc]} roles</div>
        <div style="font-size:10px;color:var(--text-faint);margin-bottom:2px;">${SCOPE_DESC[sc]}</div>
        ${rs.map(roleCard).join('')}</div>`;
    }).join('');
    el.innerHTML = `${groups}
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:14px;border-top:1px dashed var(--border);padding-top:10px;">
        <span style="font-size:11px;color:var(--text-faint);">New role:</span>
        <input id="roleNewKey" placeholder="key (a-z-)" style="font-size:12px;width:120px;">
        <input id="roleNewName" placeholder="name" style="font-size:12px;width:150px;">
        <select id="roleNewScope" style="font-size:12px;"><option value="project">Project</option><option value="cross-project">Cross-project</option><option value="platform">Platform</option></select>
        <button class="btn ghost btn-sm" id="roleAddBtn">Add role</button>
        <span id="roleAddMsg" style="font-size:11px;color:var(--text-faint);"></span>
      </div>`;
    el.querySelectorAll('.rp-cell').forEach(c => c.addEventListener('change', async function() {
      const roleId = this.dataset.role;
      const checked = Array.from(el.querySelectorAll(`.rp-cell[data-role="${roleId}"]`)).filter(x=>x.checked).map(x=>x.dataset.priv);
      try { await api(`/api/rbac/roles/${roleId}`, {method:'PUT', body: JSON.stringify({privileges: checked})}); toast('role updated'); }
      catch(e){ toast(e.message, true); loadRolesMatrix(); }
    }));
    el.querySelector('#roleAddBtn')?.addEventListener('click', async () => {
      const key=(document.getElementById('roleNewKey').value||'').trim();
      const name=(document.getElementById('roleNewName').value||'').trim();
      const scope=document.getElementById('roleNewScope').value;
      const msg=document.getElementById('roleAddMsg');
      if(!key||!name){ msg.textContent='key + name required'; msg.style.color='var(--red)'; return; }
      try { await api('/api/rbac/roles', {method:'POST', body: JSON.stringify({key,name,scope,privileges:[]})}); loadRolesMatrix(); loadAccounts(); }
      catch(e){ msg.textContent=e.message; msg.style.color='var(--red)'; }
    });
    el.querySelectorAll('.role-del').forEach(a => a.addEventListener('click', async function() {
      if(!confirm('Delete this custom role?')) return;
      try { await api(`/api/rbac/roles/${this.dataset.role}`, {method:'DELETE'}); loadRolesMatrix(); loadAccounts(); loadRoleBindings(); }
      catch(e){ toast(e.message, true); }
    }));
  } catch(e) { el.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}

// Role bindings: who is bound to what, where — account bindings + LDAP/OCP group→role mappings.
// Human label for a binding's scope target — project, customer, a spanning grant, or platform.
function _bindScopeLabel(b) {
  if (b.spans_all) return `<span style="color:var(--accent);">${b.scope === 'customer' ? 'All customers' : (b.scope === 'project' ? 'All projects' : 'All')}</span>`;
  if (b.customer_id) return '👥 ' + esc(b.customer_name || ('customer #' + b.customer_id));
  if (b.project_id) return esc(b.project_name || ('project #' + b.project_id));
  if (b.scope === 'platform' || b.scope === 'cross-project') return 'Platform';
  return '<span style="color:var(--text-faint);">—</span>';
}
// Revoke a binding via the right axis endpoint (customer member vs account-role).
async function _revokeBinding(reviewer, roleId, projId, custId) {
  if (custId) {
    return api(`/api/customers/${custId}/members/${encodeURIComponent(reviewer)}?role_id=${roleId}`, { method: 'DELETE' });
  }
  const q = (projId !== '' && projId != null) ? `?role_id=${roleId}&project_id=${projId}` : `?role_id=${roleId}`;
  return api(`/api/accounts/${encodeURIComponent(reviewer)}/roles${q}`, { method: 'DELETE' });
}

// ── RBAC grant matrix (#130 2b-iv) — subject × scoped-resource → role, parameterized by scope type.
let _bindView = 'list';
let _bindMatrixScope = 'project';   // 'project' | 'customer'
function _setBindView(mode) {
  _bindView = mode;
  document.getElementById('bindViewListBtn')?.classList.toggle('active', mode === 'list');
  document.getElementById('bindViewMatrixBtn')?.classList.toggle('active', mode === 'matrix');
  const lv = document.getElementById('roleBindings'), mv = document.getElementById('roleBindingsMatrix');
  if (lv) lv.style.display = mode === 'list' ? '' : 'none';
  if (mv) mv.style.display = mode === 'matrix' ? '' : 'none';
  if (mode === 'matrix') _renderBindingsMatrix();
}
let _bindMatrixBox = 'roleBindingsMatrix';   // the container the grant matrix renders into (reused in Customers & Projects → Access)
async function _renderBindingsMatrix(boxId = 'roleBindingsMatrix') {
  _bindMatrixBox = boxId;
  const box = document.getElementById(boxId);
  if (!box) return;
  box.innerHTML = '<div class="empty">loading…</div>';
  let accounts = [], roles = [], projects = [], customers = [], bindings = [];
  try {
    accounts = (await api('/api/accounts')).accounts || [];
    roles = (await api('/api/rbac/roles')).roles || [];
    projects = ((await api('/api/projects?show_archived=false')).projects || []).filter(p => !p.archived);
    customers = (await api('/api/customers')).customers || [];
    bindings = (await api('/api/rbac/bindings')).account_bindings || [];
  } catch (e) { box.innerHTML = `<div class="empty" style="color:var(--red)">${esc(e.message)}</div>`; return; }
  const scope = _bindMatrixScope;
  const resources = (scope === 'project' ? projects : customers).map(r => ({ id: r.id, name: r.name }));
  const scopeRoles = roles.filter(r => r.scope === scope);
  // Index bindings of this scope: subject -> (resourceId | 'all') -> [{role_name, role_id, ...}]
  const idx = {};                        // `${subject}|${resId}` -> array of bindings
  const platformSubjects = new Set();    // subjects holding a platform/cross-project role (span everything)
  for (const b of bindings) {
    if (b.scope === 'platform' || b.scope === 'cross-project') { platformSubjects.add(b.subject); continue; }
    if (b.scope !== scope) continue;
    const resId = scope === 'project' ? b.project_id : b.customer_id;
    if (resId == null) continue;
    (idx[`${b.subject}|${resId}`] ||= []).push(b);
  }
  // Subjects = all accounts (so you can grant to anyone), ordered: platform admins first.
  const subjects = accounts.map(a => ({ id: (a.reviewer || '').toLowerCase(), name: a.display_name || a.reviewer }))
    .sort((x, y) => (platformSubjects.has(y.id) - platformSubjects.has(x.id)) || x.name.localeCompare(y.name));
  const seal = scope === 'customer' ? 'customer_exclusive' : 'project_exclusive';
  const cell = (subj, resId) => {
    const list = idx[`${subj.id}|${resId}`] || [];
    if (list.length) {
      return list.map(b => `<span class="bm-role" data-rev="${esc(b.subject)}" data-role="${b.role_id}" data-proj="${b.project_id == null ? '' : b.project_id}" data-cust="${b.customer_id == null ? '' : b.customer_id}" title="${esc(b.role_name)}${b.spans_all ? ' · spans all (cell-model, not yet enforced)' : ''} — click ✕ to revoke" style="display:inline-flex;align-items:center;gap:3px;font-size:9px;background:var(--accent-bg);border:1px solid var(--accent-soft);border-radius:8px;padding:0 5px;white-space:nowrap;">${esc(b.role_name)}${b.spans_all ? ' ⊞' : ''} <a href="javascript:void(0)" class="bm-revoke" style="color:var(--red);">✕</a></span>`).join(' ');
    }
    return `<a href="javascript:void(0)" class="bm-grant" data-rev="${esc(subj.id)}" data-res="${resId}" title="Grant a ${esc(scope)} role here" style="color:var(--text-faint);font-size:11px;">＋</a>`;
  };
  let h = `<div style="display:flex;gap:6px;align-items:center;margin:4px 0 8px;">
      <span style="font-size:10px;color:var(--text-faint);">Axis</span>
      <button class="tab${scope === 'project' ? ' active' : ''}" type="button" onclick="_bindMatrixScope='project';_renderBindingsMatrix(_bindMatrixBox)">Projects</button>
      <button class="tab${scope === 'customer' ? ' active' : ''}" type="button" onclick="_bindMatrixScope='customer';_renderBindingsMatrix(_bindMatrixBox)">Customers</button>
      <span style="font-size:10px;color:var(--text-faint);margin-left:8px;">rows = accounts · cols = ${scope}s · cell = role (click ＋ to grant, ✕ to revoke). A 🔒 sealed ${scope} requires an explicit grant for everyone — platform admins included.</span>
    </div>`;
  if (!resources.length) { box.innerHTML = h + `<div class="empty">No ${scope}s yet.</div>`; return; }
  const sealedSet = new Set((scope === 'project' ? projects : customers).filter(r => r.is_exclusive).map(r => r.id));
  h += `<div style="overflow:auto;max-height:60vh;"><table class="capmap" style="border-collapse:collapse;font-size:11px;">
    <thead><tr><th class="cm-corner" style="position:sticky;left:0;top:0;z-index:4;background:var(--bg-panel);padding:3px 8px;text-align:left;">Account ＼ ${scope}</th>
      ${resources.map(r => `<th class="cm-caphead" style="position:sticky;top:0;background:var(--bg-panel);padding:3px 6px;white-space:nowrap;" title="${sealedSet.has(r.id) ? 'sealed — explicit grants only' : ''}">${sealedSet.has(r.id) ? '🔒 ' : ''}${esc(r.name)}</th>`).join('')}
    </tr></thead><tbody>`;
  h += subjects.map(s => `<tr>
      <td class="cm-uc" style="position:sticky;left:0;background:var(--bg-panel);padding:3px 8px;white-space:nowrap;">${esc(s.name)}${platformSubjects.has(s.id) ? ' <span title="platform admin — superuser, except on sealed scopes" style="font-size:8px;color:var(--accent);">★ platform</span>' : ''}</td>
      ${resources.map(r => `<td style="border:1px solid var(--border);padding:3px 6px;text-align:center;">${cell(s, r.id)}</td>`).join('')}
    </tr>`).join('');
  h += '</tbody></table></div>';
  box.innerHTML = h;
  // Revoke from a cell badge.
  box.querySelectorAll('.bm-revoke').forEach(a => a.addEventListener('click', async (e) => {
    e.stopPropagation();
    const sp = a.closest('.bm-role');
    try { await _revokeBinding(sp.dataset.rev, sp.dataset.role, sp.dataset.proj, sp.dataset.cust); _renderBindingsMatrix(_bindMatrixBox); loadAccounts(); }
    catch (err) { toast(err.message, true); }
  }));
  // Grant into an empty resource cell via a role picker popover.
  box.querySelectorAll('.bm-grant').forEach(a => a.addEventListener('click', (e) => _bmGrantPopover(e, a.dataset.rev, a.dataset.res, scope, scopeRoles)));
}
function _bmGrantPopover(event, reviewer, resId, scope, scopeRoles) {
  document.querySelectorAll('.bm-pop').forEach(p => p.remove());
  if (!scopeRoles.length) { toast(`No ${scope} roles defined`); return; }
  const pop = document.createElement('div');
  pop.className = 'bm-pop';
  pop.style.cssText = 'position:fixed;z-index:9999;background:var(--bg-panel);border:1px solid var(--border);border-radius:4px;box-shadow:0 4px 16px rgba(0,0,0,0.35);padding:4px;min-width:160px;';
  pop.innerHTML = `<div style="font-size:9px;color:var(--text-faint);padding:2px 6px;">Grant role to ${esc(reviewer)}</div>` +
    scopeRoles.map(r => `<button class="dropdown-item" data-role="${r.id}" style="display:block;width:100%;text-align:left;font-size:12px;">${esc(r.name)}</button>`).join('');
  document.body.appendChild(pop);
  const rb = event.target.getBoundingClientRect();
  pop.style.left = Math.min(rb.left, window.innerWidth - 180) + 'px';
  pop.style.top = (rb.bottom + 4) + 'px';
  pop.querySelectorAll('button').forEach(b => b.addEventListener('click', async () => {
    pop.remove();
    try {
      if (scope === 'customer') {
        await api(`/api/customers/${resId}/members`, { method: 'POST', body: JSON.stringify({ reviewer, role_id: parseInt(b.dataset.role, 10) }) });
      } else {
        await api(`/api/accounts/${encodeURIComponent(reviewer)}/roles`, { method: 'POST', body: JSON.stringify({ role_id: parseInt(b.dataset.role, 10), project_id: parseInt(resId, 10) }) });
      }
      _renderBindingsMatrix(_bindMatrixBox); loadAccounts();
    } catch (e) { toast(e.message, true); }
  }));
  setTimeout(() => { const close = ev => { if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('click', close); } }; document.addEventListener('click', close); }, 0);
}

async function loadRoleBindings() {
  const el = document.getElementById('roleBindings');
  if (!el) return;
  try {
    const bResp = await api('/api/rbac/bindings');
    const rResp = await api('/api/rbac/roles');
    const pResp = await api('/api/projects?show_archived=false');
    const roles = rResp.roles || [];
    const projects = (pResp.projects || []).filter(p => !p.archived);
    const accts = bResp.account_bindings || [];
    const groups = bResp.group_mappings || [];
    const SL = { 'platform':'Platform', 'cross-project':'Cross-project', 'project':'Project', 'customer':'Customer' };
    const acctRows = accts.length ? accts.map(b => `
      <tr style="border-bottom:1px solid var(--border);">
        <td style="padding:3px 8px;">${esc(b.display_name||b.subject)} <span style="color:var(--text-faint);font-size:10px;">${esc(b.subject)}</span></td>
        <td style="padding:3px 8px;">${esc(b.role_name)} <span style="font-size:9px;color:var(--text-faint);">${esc(b.scope)}</span></td>
        <td style="padding:3px 8px;">${_bindScopeLabel(b)}</td>
        <td style="padding:3px 8px;"><a href="javascript:void(0)" class="rb-acct-rm" data-rev="${esc(b.subject)}" data-role="${b.role_id}" data-proj="${b.project_id==null?'':b.project_id}" data-cust="${b.customer_id==null?'':b.customer_id}" style="color:var(--red);">revoke</a></td>
      </tr>`).join('') : `<tr><td colspan="4" style="padding:6px 8px;color:var(--text-faint);">No account bindings — assign roles from the account list above.</td></tr>`;
    const groupRows = groups.length ? groups.map(g => `
      <tr style="border-bottom:1px solid var(--border);">
        <td style="padding:3px 8px;"><span style="font-size:9px;color:var(--text-faint);">${esc(g.source)}</span> ${esc(g.subject)}</td>
        <td style="padding:3px 8px;">${esc(g.role_name)} <span style="font-size:9px;color:var(--text-faint);">${esc(g.scope)}</span></td>
        <td style="padding:3px 8px;">${_bindScopeLabel(g)}</td>
        <td style="padding:3px 8px;"><a href="javascript:void(0)" class="rb-grp-rm" data-id="${g.mapping_id}" style="color:var(--red);">remove</a></td>
      </tr>`).join('') : `<tr><td colspan="4" style="padding:6px 8px;color:var(--text-faint);">No group mappings yet.</td></tr>`;
    const roleOpts = roles.map(r => `<option value="${r.id}" data-scope="${esc(r.scope)}">${esc(r.name)} (${SL[r.scope]||r.scope})</option>`).join('');
    const projOpts = projects.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
    const th = '<tr style="text-align:left;color:var(--text-faint);font-size:10px;"><th style="padding:2px 8px;">Subject</th><th style="padding:2px 8px;">Role</th><th style="padding:2px 8px;">Scope</th><th></th></tr>';
    el.innerHTML = `
      <div style="font-size:11px;font-weight:600;margin:6px 0 2px;">Account bindings</div>
      <table style="border-collapse:collapse;width:100%;font-size:12px;"><thead>${th}</thead><tbody>${acctRows}</tbody></table>
      <div style="font-size:11px;font-weight:600;margin:14px 0 2px;">LDAP / OCP group → role mappings</div>
      <table style="border-collapse:collapse;width:100%;font-size:12px;"><thead>${th}</thead><tbody>${groupRows}</tbody></table>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px;">
        <select id="rbGrpSource" style="font-size:11px;"><option value="ldap">ldap</option><option value="ocp">ocp</option></select>
        <input id="rbGrpKey" placeholder="group DN / name" style="font-size:11px;width:220px;">
        <select id="rbGrpRole" style="font-size:11px;">${roleOpts}</select>
        <select id="rbGrpProj" style="font-size:11px;display:none;"><option value="">— project —</option>${projOpts}</select>
        <button class="btn ghost btn-sm" id="rbGrpAdd">Add mapping</button>
        <span id="rbGrpMsg" style="font-size:11px;color:var(--text-faint);"></span>
      </div>`;
    const roleSel = document.getElementById('rbGrpRole');
    const projSel = document.getElementById('rbGrpProj');
    const syncProjVis = () => { const sc = roleSel.options[roleSel.selectedIndex]?.dataset.scope; projSel.style.display = sc==='project' ? '' : 'none'; };
    roleSel.addEventListener('change', syncProjVis); syncProjVis();
    document.getElementById('rbGrpAdd').addEventListener('click', async () => {
      const source = document.getElementById('rbGrpSource').value;
      const group_key = (document.getElementById('rbGrpKey').value||'').trim();
      const role_id = parseInt(roleSel.value,10);
      const sc = roleSel.options[roleSel.selectedIndex]?.dataset.scope;
      const project_id = sc==='project' ? (parseInt(projSel.value,10)||null) : null;
      const msg = document.getElementById('rbGrpMsg');
      if (!group_key) { msg.textContent='group required'; msg.style.color='var(--red)'; return; }
      if (sc==='project' && !project_id) { msg.textContent='pick a project'; msg.style.color='var(--red)'; return; }
      try { await api('/api/rbac/group-mappings', {method:'POST', body: JSON.stringify({source, group_key, role_id, project_id})}); document.getElementById('rbGrpKey').value=''; loadRoleBindings(); }
      catch(e){ msg.textContent=e.message; msg.style.color='var(--red)'; }
    });
    el.querySelectorAll('.rb-acct-rm').forEach(a => a.addEventListener('click', async function() {
      try { await _revokeBinding(this.dataset.rev, this.dataset.role, this.dataset.proj, this.dataset.cust); loadRoleBindings(); loadAccounts(); }
      catch(e){ toast(e.message, true); }
    }));
    el.querySelectorAll('.rb-grp-rm').forEach(a => a.addEventListener('click', async function() {
      try { await api(`/api/rbac/group-mappings/${this.dataset.id}`, {method:'DELETE'}); loadRoleBindings(); }
      catch(e){ toast(e.message, true); }
    }));
  } catch(e) { el.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
}

async function loadRunStatus() {
  try {
    const s = await api('/api/runs/status');
    document.getElementById('triggerStatus').textContent =
      s.enabled && s.available ? `pipeline: ${s.pipeline_name}`
      : s.enabled ? 'pipeline: unavailable' : 'trigger disabled';
  } catch { /* non-fatal */ }
}

function setApiStatus(ok, msg) {
  const el = document.getElementById('apiStatusEl'), txt = document.getElementById('apiStatusText');
  el.classList.toggle('api-ok', !!ok); el.classList.toggle('api-err', !ok);
  txt.textContent = ok ? 'api ok' : ('error: ' + (msg || 'offline'));
}

// ── Sidebar nav state ────────────────────────────────────────
let _navCollapsed = false;

function initNavCollapse() {
  try { _navCollapsed = localStorage.getItem('davNavCollapsed') === '1'; } catch(e) {}
  const nav = document.getElementById('pfNav');
  if (_navCollapsed) nav.classList.add('collapsed');
}

function toggleNav() {
  _navCollapsed = !_navCollapsed;
  document.getElementById('pfNav').classList.toggle('collapsed', _navCollapsed);
  try { localStorage.setItem('davNavCollapsed', _navCollapsed ? '1' : '0'); } catch(e) {}
  try { _persistUserSettings(); } catch {}
}

// ── Tab routing ──────────────────────────────────────────────
// Background poll that keeps the runs list live while the user is on the
// Runs tab. Triggers from elsewhere (API direct, CLI) show up within
// _RUNS_LIST_POLL_MS; in-flight phase transitions repaint without refresh.
let _runsListPollTimer = null;
const _RUNS_LIST_POLL_MS = 5000;
function _startRunsListPoll() {
  _stopRunsListPoll();
  _runsListPollTimer = setInterval(() => {
    // Only poll while the Runs view is the active one — protects against
    // background tabs / hidden pages spinning needless requests.
    if (document.visibilityState === 'visible'
        && document.getElementById('view-runs')?.classList.contains('active')) {
      // silent: skip the "loading…" placeholder + don't blank the list
      // on a transient API blip — diff-render handles the update in place.
      loadRuns({silent: true}).catch(() => {});
    }
  }, _RUNS_LIST_POLL_MS);
}
function _stopRunsListPoll() {
  if (_runsListPollTimer) { clearInterval(_runsListPollTimer); _runsListPollTimer = null; }
}

// ───────────────────────────────────────────────────────────────────────────
// Logical-domain IA (app-wide). The left rail lists DOMAINS; the active domain's
// sub-views render as the #domainTabs top strip; the selected sub-view's #view-X
// section fills the bulk. switchView() stays the single funnel (its tail keeps the
// rail + strip in sync) so every existing switchView('x') caller works unchanged.
// Sub-tabs are dual-classed `.tab.pf-nav-item[data-view]` (+ legacy ids) so the e2e
// selectors and the active-toggle in switchView keep resolving them.
const DOMAINS = [
  { key:'author',  label:'Use Cases',  icon:'✎', focus:'architecture', subviews:[
      { view:'usecases',    label:'Use Cases',     badge:'badgeUC' },
      { view:'scopingsets', label:'Scoping Sets' },
      { view:'inbox',       label:'Discussion',    badge:'badgeInbox' },
  ]},
  { key:'execute', label:'Analyze',    icon:'▶', focus:'architecture', subviews:[
      { view:'runs',    label:'Analyses', badge:'badgeRuns' },
      { view:'results', label:'Results',    badge:'badgeResults' },
  ]},
  { key:'roadmap', label:'Roadmaps',   icon:'✦', focus:'architecture', subviews:[
      { view:'review',      label:'Arch Review' },
      { view:'enhancement', label:'Enhancement / PR' },
      { view:'engineering', label:'Engineering Roadmap' },
  ]},
  { key:'assess',  label:'Assessments', icon:'📊', focus:'assessment', navId:'navAssess', subviews:[
      { view:'assess',   label:'Assessments', priv:'assessment.view' },
      { view:'maturity', label:'Maturity Wall', priv:'assessment.view' },
  ]},
  // IA slice 2: capability spine = one domain. The Catalog (registry, List/Board) and the
  // Cap Map (UC↔capability matrix) are both capability surfaces, so they live together under
  // "Capabilities" rather than Cap Map sitting in Roadmaps. (The Engineering Roadmap view's own
  // inline cap-map render is the remaining duplicate — removed in the generator-collapse slice.)
  { key:'catalog', label:'Capabilities', icon:'📒', focus:'both', subviews:[
      { view:'catalog', label:'Catalog' },
      { view:'capmap',  label:'Cap Map' },
  ]},
  // UI lean slice 3: setup/admin surfaces folded into one Settings group (9 → 6 top-level
  // domains; Miller's 7±2). Config, Prompts & Improvement, Customers & Projects, and Audit
  // are not daily-driver views — they live behind one gear, surfaced as its sub-tabs.
  // Each sub-view keeps its existing privilege, so access is unchanged (the domain shows iff
  // at least one sub-tab is permitted; the strip renders only permitted tabs).
  { key:'settings', label:'Settings', icon:'⚙', focus:'both', subviews:[
      { view:'config',    label:'Config' },
      { view:'improve',   label:'Prompts & Improvement', badge:'badgeImprove' },
      { view:'customers', label:'Customers' },
      { view:'projects',  label:'Projects' },
      { view:'audit',     label:'Audit', priv:'__platAdmin' },
  ]},
];
const _viewToDomain = {};
DOMAINS.forEach(d => d.subviews.forEach(s => { _viewToDomain[s.view] = d; }));
const _lastSubview = {};       // domainKey -> last view shown in it
let _inSwitchView = false;     // re-entrancy guard (focus auto-home re-enters switchView)

// A sub-view is permitted if it has no priv, or the caller holds it ('__platAdmin' = platform admin).
function _subviewPermitted(sub) {
  if (!sub.priv) return true;
  if (sub.priv === '__platAdmin') return !!(me && me.is_platform_admin);
  return can(sub.priv);
}
function _domainPermitted(d) { return d.subviews.some(_subviewPermitted); }

// Render the left rail from DOMAINS (one anchor per domain). data-focus preserved so
// _applyFocus()'s selector keeps filtering; navId carried for RBAC + e2e parity.
function renderDomainRail() {
  const host = document.querySelector('.pf-nav-items');
  if (!host) return;
  host.innerHTML = _personaDomains().map(d => `
    <a class="pf-nav-item" data-domain="${d.key}" data-focus="${d.focus}"${d.navId ? ` id="${d.navId}"` : ''} href="javascript:void(0)">
      <span class="nav-icon">${d.icon}</span><span class="nav-label">${esc(d.label)} <span class="badge dom-dot" id="domDot-${d.key}" style="font-size:9px;"></span></span>
    </a>`).join('');
  // Clicks are handled by event delegation on .pf-nav-items (bound once at boot).
}

// Populate #domainTabs with the active domain's permitted sub-views (the top strip).
// Dual-classed `.tab.pf-nav-item` + data-view (+ legacy id) so e2e selectors + the
// switchView active-toggle keep resolving them. Strip hidden when ≤1 permitted sub-view.
function renderDomainTabs(domain, activeView) {
  const strip = document.getElementById('domainTabs');
  if (!strip) return;
  const subs = domain.subviews.filter(_subviewPermitted);
  if (subs.length <= 1) { strip.style.display = 'none'; strip.innerHTML = ''; return; }
  strip.style.display = '';
  strip.innerHTML = subs.map(s => `
    <button class="tab pf-nav-item${s.view === activeView ? ' active' : ''}" data-tab="${s.view}" data-view="${s.view}"${s.navId ? ` id="${s.navId}"` : ''} onclick="switchView('${s.view}')">
      ${esc(s.label)}${s.badge ? ` <span class="badge" id="${s.badge}" style="font-size:9px;"></span>` : ''}
    </button>`).join('');
}

// Select a domain → show its remembered (or first permitted) sub-view via the funnel.
function switchDomain(key) {
  const d = DOMAINS.find(x => x.key === key);
  if (!d) return;
  const permitted = d.subviews.filter(_subviewPermitted);
  if (!permitted.length) return;
  const target = (_lastSubview[key] && permitted.some(s => s.view === _lastSubview[key]))
    ? _lastSubview[key] : permitted[0].view;
  switchView(target);
}

function switchView(name) {
  _curView = name;
  _updateContextChrome(name);
  document.querySelectorAll('.pf-nav-item').forEach(b =>
    b.classList.toggle('active', b.dataset.view === name));
  document.querySelectorAll('.pf-view').forEach(v =>
    v.classList.toggle('active', v.id === 'view-' + name));
  // Start/stop the runs-list poll based on whether we're on that tab
  if (name === 'runs') _startRunsListPoll();
  else                 _stopRunsListPoll();
  if (name === 'runs')     { loadRuns(); setTimeout(_renderAnalysisAudit, 40); }
  if (name === 'results')  { loadResults(); _showCurrentRunResults(); }
  if (name === 'usecases') { loadUCs(); loadSets(); }
  if (name === 'scopingsets') loadScopingSets();
  if (name === 'review')   loadReviewTab();
  if (name === 'enhancement') loadEnhancementWorkbench();
  if (name === 'engineering') loadEngineeringTab();
  if (name === 'catalog')  loadCatalogTab();
  if (name === 'customers') loadCustomers();
  if (name === 'projects')  loadProjectsTab();
  if (name === 'capmap')   loadCapMap();
  if (name === 'audit')    loadAudit();
  if (name === 'assess')   loadAssessments();
  if (name === 'maturity') loadMaturityWall();
  if (name === 'improve')  { pmInit(); loadImproveQueue(); }
  if (name === 'config') {
    loadConfig();
    // Platform settings (accounts/roles/bindings/LDAP/SMTP/agents/tenants/groups) live in
    // Config → Platform. Project management is its own Settings → Projects view now, so the
    // projects loaders are NOT fired here (IA slice 4: drop the redundant double-load — the
    // 'projects' branch below is the single owner).
    if (me && me.is_platform_admin) { loadAccounts(); loadRolesMatrix(); loadRoleBindings(); loadLdapStatus(); loadLdapSettings(); loadSmtpSettings(); loadAgentTokens(); loadTenants(); loadGroups(); }
    if (can('usecat.manage')) loadBundles();
  }
  if (name === 'inbox')    loadInbox();
  if (name === 'projects') { loadProjectsAdmin(); loadProjectSwitcher(); }
  // Keep the domain rail + top sub-tab strip in sync with the active view (the IA
  // funnel). Guarded against the _applyFocus() auto-home re-entry.
  const _dom = _viewToDomain[name];
  if (_dom && !_inSwitchView) {
    _inSwitchView = true;
    try {
      _lastSubview[_dom.key] = name;
      document.querySelectorAll('.pf-nav-item[data-domain]').forEach(a =>
        a.classList.toggle('active', a.dataset.domain === _dom.key));
      renderDomainTabs(_dom, name);
    } finally { _inSwitchView = false; }
  }
}

// Relocate the Users/Projects management panels out of Config into their own
// full-page views (built once at boot). Far safer than duplicating the markup;
// the panels keep their ids so all the existing loaders keep working.
function _setupRbacViews() {
  // Platform settings (Projects, Email/SMTP, LDAP, Users & roles) now live in the Config
  // view's "Platform" section — no longer relocated into separate Users/Projects views
  // (whose nav items are retired). Kept as a no-op stub so existing callers don't break.
}

// ── Active run chip (masthead) ───────────────────────────────
function updateRunChip(name, phase) {
  // The pill text is owned solely by _renderRunChipLive (aggregate stats, never a run name);
  // this only manages the chip's active/done state + refreshes the stats.
  const chip = document.getElementById('runContextChip');
  const dot  = document.getElementById('rccDot');
  if (!name) { if (chip) chip.classList.remove('active'); _renderRunChipLive(); return; }
  const terminal = ['Succeeded','Failed','Cancelled','TimedOut'].includes(phase);
  if (dot) dot.classList.toggle('done', terminal);
  if (chip) chip.classList.add('active');
  _renderRunChipLive();
}

function clearRunChip() {
  document.getElementById('runContextChip').classList.remove('active');
  _rdName = null;
  stopRunPolling();
}

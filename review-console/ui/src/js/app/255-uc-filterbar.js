// ── Use Cases filter bar (#244) ──────────────────────────────────────────────
// A GitHub/GitLab-style qualifier grammar (`state:draft tag:billing free text`) rendered as
// PatternFly-style removable chips, plus a "+ Filter" attribute menu for discoverability.
// See docs/uc-filtering-design.md.
//
// This is an ADAPTER, not a rewrite: the legacy <select>/<input> controls in #ucFilterBacking are
// the canonical state store. loadUCs()/renderUCList() read them exactly as before. This bar only
// writes into them and dispatches their native events, so all existing filter logic is untouched.
//
// Facet registry — the single source of truth for the parser, the chips, and the menu. Each facet
// maps a qualifier key to a hidden backing control. `scope:true` facets change the fetched
// population (the control's listener calls loadUCs); the rest narrow the loaded set (renderUCList).
const UC_FACETS = [
  { key:'state',    label:'State',    el:'ucStateFilter',   ev:'change', scope:false,
    values:[['draft','draft'],['ready','ready'],['in_review','in review'],['approved','approved'],
            ['deprecated','deprecated'],['__all__','all (incl. deprecated)']] },
  { key:'priority', label:'Priority', el:'ucPriorityFilter', ev:'change', scope:false,
    values:[['critical','critical'],['high','high'],['medium','medium'],['low','low']] },
  { key:'assigned', label:'Assigned', el:'ucAssignFilter',  ev:'change', scope:false,
    values:[['assigned','yes (in a set)'],['unassigned','no (in no set)']],
    aliases:{ yes:'assigned', no:'unassigned' } },
  { key:'health',   label:'Health',   el:'ucHealthFilter',  ev:'change', scope:false,
    values:[['valid','valid'],['invalid','invalid']] },
  { key:'tag',      label:'Tag',      el:'ucTagFilter',     ev:'input',  scope:false, dynamic:'tags' },
  { key:'source',   label:'Source',   el:'ucSourceFilter',  ev:'change', scope:true,
    values:[['managed','managed'],['corpus','corpus']] },
  { key:'repo',     label:'Repo',     el:'ucRepoFilter',    ev:'change', scope:true, dynamic:'options' },
  { key:'scope',    label:'Project scope', el:'ucScopeFilter', ev:'change', scope:true,
    values:[['pool','available to apply']] },   // '' = "in this project" (the default — no chip)
];
const _ucFacetByKey = k => UC_FACETS.find(f => f.key === k);

// Dynamic value lists for facets whose options come from the loaded data.
function _ucFacetValues(facet) {
  if (facet.dynamic === 'tags') {
    const s = new Set();
    (typeof allUCs !== 'undefined' ? allUCs : []).forEach(u => (u.tags||[]).forEach(t => s.add(t)));
    return [...s].sort().map(t => [t, t]);
  }
  if (facet.dynamic === 'options') {
    const sel = document.getElementById(facet.el);
    return sel ? [...sel.options].filter(o => o.value).map(o => [o.value, o.textContent]) : [];
  }
  return facet.values || [];
}
function _ucValueLabel(facet, val) {
  const pair = _ucFacetValues(facet).find(([v]) => v === val);
  return pair ? pair[1] : val;
}

// Set a facet's backing control + fire its native event so the existing listener (loadUCs /
// renderUCList) runs. Empty string clears the facet.
function _ucSetFacet(key, val) {
  const facet = _ucFacetByKey(key); if (!facet) return;
  const el = document.getElementById(facet.el); if (!el) return;
  el.value = val;
  el.dispatchEvent(new Event(facet.ev, { bubbles: true }));
  _ucRenderChips();
}

// Render one chip per active (non-empty) facet from the backing controls.
function _ucRenderChips() {
  const box = document.getElementById('ucFilterChips'); if (!box) return;
  box.innerHTML = '';
  for (const facet of UC_FACETS) {
    const el = document.getElementById(facet.el); if (!el || !el.value) continue;
    const chip = document.createElement('span');
    chip.className = 'uc-fb-chip' + (facet.scope ? ' scope' : '');
    const label = document.createElement('span');
    label.textContent = `${facet.label}: ${_ucValueLabel(facet, el.value)}`;
    const x = document.createElement('button');
    x.type = 'button'; x.textContent = '×'; x.title = 'Remove filter';
    x.addEventListener('click', () => _ucSetFacet(facet.key, ''));
    chip.appendChild(label); chip.appendChild(x);
    box.appendChild(chip);
  }
}

// Lift `key:value` qualifiers typed in the search box into facet chips; the residual free text is
// mirrored to the hidden #ucFilter (whose existing 'input' listener re-renders the list).
function _ucOnFilterTyped(liftTrailing) {
  const inp = document.getElementById('ucFilterInput'); if (!inp) return;
  let v = inp.value;
  const re = liftTrailing ? /([A-Za-z_]+):("[^"]*"|\S+)\s*/g : /([A-Za-z_]+):("[^"]*"|\S+)\s+/g;
  v = v.replace(re, (m, k, raw) => {
    const facet = _ucFacetByKey(k.toLowerCase());
    if (!facet) return m;   // unknown qualifier → leave it as plain text
    let val = raw.replace(/^"|"$/g, '');
    if (facet.aliases && facet.aliases[val.toLowerCase()]) val = facet.aliases[val.toLowerCase()];
    _ucSetFacet(facet.key, val);
    return '';
  });
  inp.value = v.replace(/^\s+/, '');
  const hidden = document.getElementById('ucFilter');
  if (hidden) { hidden.value = inp.value.trim(); hidden.dispatchEvent(new Event('input', { bubbles: true })); }
}

// Build the "+ Filter" attribute→value menu (grouped value pills; click sets the facet).
function _ucBuildAddFilterMenu() {
  const menu = document.getElementById('ucAddFilterMenu'); if (!menu) return;
  menu.innerHTML = '';
  for (const facet of UC_FACETS) {
    const vals = _ucFacetValues(facet);
    if (!vals.length) continue;
    const grp = document.createElement('div'); grp.className = 'uc-fb-menu-grp';
    const h = document.createElement('div'); h.className = 'uc-fb-menu-lbl'; h.textContent = facet.label;
    grp.appendChild(h);
    const wrap = document.createElement('div'); wrap.className = 'uc-fb-menu-vals';
    vals.forEach(([val, lbl]) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'uc-fb-menu-val'; b.textContent = lbl;
      b.addEventListener('click', () => { _ucSetFacet(facet.key, val); _ucCloseFilterMenu(); });
      wrap.appendChild(b);
    });
    grp.appendChild(wrap); menu.appendChild(grp);
  }
}
function _ucToggleFilterMenu() {
  const menu = document.getElementById('ucAddFilterMenu'); if (!menu) return;
  if (menu.style.display === 'none' || !menu.style.display) { _ucBuildAddFilterMenu(); menu.style.display = 'block'; }
  else menu.style.display = 'none';
}
function _ucCloseFilterMenu() {
  const menu = document.getElementById('ucAddFilterMenu'); if (menu) menu.style.display = 'none';
}

// ── Live autocomplete on the search box ──────────────────────────────────────
// As you type, suggest facet KEYS for a bare word and facet VALUES once past the `:`
// (tag values match live against the loaded set). Keyboard: ↑/↓ move, Enter/Tab accept,
// Esc dismiss. Accepting a key inserts `key:`; accepting a value lifts it to a chip.
let _ucSuggestItems = [];    // [{kind:'key'|'value', facet, val, label, hint}]
let _ucSuggestActive = -1;   // highlighted index

// The trailing whitespace-delimited chunk currently being typed, and its start offset.
function _ucTrailingToken() {
  const inp = document.getElementById('ucFilterInput'); if (!inp) return { tok:'', start:0 };
  const m = inp.value.match(/(\S*)$/); const tok = m ? m[1] : '';
  return { tok, start: inp.value.length - tok.length };
}

function _ucComputeSuggest() {
  const { tok } = _ucTrailingToken();
  if (!tok) return [];
  const ci = tok.indexOf(':');
  if (ci === -1) {
    const q = tok.toLowerCase();
    return UC_FACETS.filter(f => f.key.startsWith(q) || f.label.toLowerCase().startsWith(q))
      .map(f => ({ kind:'key', facet:f, label:`${f.key}:`, hint:f.label }));
  }
  const facet = _ucFacetByKey(tok.slice(0, ci).toLowerCase()); if (!facet) return [];
  const partial = tok.slice(ci + 1).replace(/^"|"$/g, '').toLowerCase();
  return _ucFacetValues(facet)
    .filter(([val, lbl]) => val.toLowerCase().includes(partial) || String(lbl).toLowerCase().includes(partial))
    .slice(0, 12)
    .map(([val, lbl]) => ({ kind:'value', facet, val, label:lbl, hint:facet.label }));
}

function _ucRenderSuggest() {
  const box = document.getElementById('ucFilterSuggest'); if (!box) return;
  _ucSuggestItems = _ucComputeSuggest();
  if (!_ucSuggestItems.length) { _ucHideSuggest(); return; }
  if (_ucSuggestActive >= _ucSuggestItems.length) _ucSuggestActive = -1;
  box.innerHTML = '';
  _ucSuggestItems.forEach((it, i) => {
    const row = document.createElement('div');
    row.className = 'uc-fb-sugg-item' + (i === _ucSuggestActive ? ' active' : '');
    row.innerHTML = `<span class="uc-fb-sugg-lbl">${esc(it.label)}</span><span class="uc-fb-sugg-hint">${esc(it.hint)}</span>`;
    row.addEventListener('mousedown', e => { e.preventDefault(); _ucAcceptSuggest(i); });
    box.appendChild(row);
  });
  box.style.display = 'block';
}

function _ucAcceptSuggest(i) {
  const it = _ucSuggestItems[i]; if (!it) return;
  const inp = document.getElementById('ucFilterInput'); if (!inp) return;
  const { start } = _ucTrailingToken();
  if (it.kind === 'key') {
    // Replace the partial word with `key:`; keep focus and immediately offer its values.
    inp.value = inp.value.slice(0, start) + it.label;
    inp.focus(); _ucRenderSuggest();
  } else {
    _ucSetFacet(it.facet.key, it.val);                       // lift to a chip
    inp.value = inp.value.slice(0, start).replace(/\s+$/, '');
    if (inp.value) inp.value += ' ';
    const hidden = document.getElementById('ucFilter');      // sync residual free text
    if (hidden) { hidden.value = inp.value.trim(); hidden.dispatchEvent(new Event('input', { bubbles: true })); }
    inp.focus(); _ucHideSuggest();
  }
}

function _ucHideSuggest() {
  const box = document.getElementById('ucFilterSuggest');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  _ucSuggestItems = []; _ucSuggestActive = -1;
}

// Wiring (runs once at load; the UC view markup is always present in the DOM).
(function _ucFilterBarInit() {
  const inp = document.getElementById('ucFilterInput');
  if (inp) {
    inp.addEventListener('input', () => { _ucOnFilterTyped(false); _ucRenderSuggest(); });
    inp.addEventListener('focus', () => _ucRenderSuggest());
    inp.addEventListener('blur', () => setTimeout(_ucHideSuggest, 120));   // let a click land first
    inp.addEventListener('keydown', e => {
      const n = _ucSuggestItems.length;
      if (n && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        _ucSuggestActive = e.key === 'ArrowDown'
          ? (_ucSuggestActive + 1) % n
          : (_ucSuggestActive - 1 + n) % n;
        _ucRenderSuggest();
      } else if (e.key === 'Tab' && n) {
        e.preventDefault(); _ucAcceptSuggest(_ucSuggestActive >= 0 ? _ucSuggestActive : 0);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const { tok } = _ucTrailingToken();
        if (_ucSuggestActive >= 0) _ucAcceptSuggest(_ucSuggestActive);
        else if (n && tok.includes(':')) _ucAcceptSuggest(0);   // finishing a value → take best match
        else _ucOnFilterTyped(true);                            // bare text → just commit/lift
      } else if (e.key === 'Escape') {
        _ucHideSuggest();
      }
    });
  }
  const addBtn = document.getElementById('ucAddFilterBtn');
  if (addBtn) addBtn.addEventListener('click', e => { e.stopPropagation(); _ucToggleFilterMenu(); });
  document.addEventListener('click', e => {
    const menu = document.getElementById('ucAddFilterMenu');
    if (menu && menu.style.display === 'block' && !menu.contains(e.target) && e.target.id !== 'ucAddFilterBtn')
      _ucCloseFilterMenu();
  });
  // The tag facet's backing input (#244): exact-tag narrowing handled in renderUCList.
  const tag = document.getElementById('ucTagFilter');
  if (tag) tag.addEventListener('input', e => { ucTagFilter = e.target.value; renderUCList(); });
  _ucRenderChips();
})();

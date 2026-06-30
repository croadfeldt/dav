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

// Wiring (runs once at load; the UC view markup is always present in the DOM).
(function _ucFilterBarInit() {
  const inp = document.getElementById('ucFilterInput');
  if (inp) {
    inp.addEventListener('input', () => _ucOnFilterTyped(false));
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); _ucOnFilterTyped(true); } });
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

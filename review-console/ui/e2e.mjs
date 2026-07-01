// UI boot-smoke e2e (QA pipeline — UI/UX layer). Loads the REAL index.html in jsdom,
// stubs the API per role, runs boot, and asserts: (a) no uncaught errors at boot, and
// (b) role-gated elements render correctly. Catches the class of bug where a boot path
// throws (e.g. the `hasPriv` ReferenceError that stripped the admin status bar) or an
// element is mis-gated — which `node --check` and even eslint can't see.
//
//   cd review-console/ui && npm install && node e2e.mjs   (or: npm test)
//
import { JSDOM, VirtualConsole } from 'jsdom';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dir = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dir, 'index.html'), 'utf8');

// Privilege sets per role under test (mirrors what /api/me would return).
const ROLES = {
  'platform-admin': {
    is_platform_admin: true, is_admin: true, is_project_admin: true,
    privileges: ['platform.admin', 'project.data.read', 'assessment.view', 'assessment.edit',
                 'prompt.manage', 'project.models', 'project.integrations', 'project.repos',
                 'project.catalog', 'blueprint.view', 'blueprint.edit', 'usecat.manage'],
  },
  'project-admin': {
    is_platform_admin: false, is_admin: true, is_project_admin: true,
    privileges: ['project.data.read', 'project.create', 'assessment.view', 'assessment.edit',
                 'prompt.manage', 'project.models', 'project.integrations', 'blueprint.view'],
  },
  'project-viewer': {
    is_platform_admin: false, is_admin: false, is_project_admin: false,
    privileges: ['project.data.read', 'assessment.view', 'blueprint.view'],
  },
};

// data-target → display for a Config Platform-section nav link.
function linkDisp(document, target) {
  const el = document.querySelector(`.config-nav-link[data-target="${target}"]`);
  return el ? el.style.display : '(missing)';
}

function meFor(role) {
  return {
    authenticated: true, reviewer: 'qa@dav.local', role, roles: [],
    can_manage_uc_sources: false, approved: true, ldap_enabled: false,
    must_change_password: false, sessions_enabled: true,
    default_project_id: null, active_project_id: null,
    ...ROLES[role],
  };
}

// Permissive default body so the many boot loaders don't false-fail on shape.
function bodyFor(path, me) {
  if (path === '/api/me') return me;
  return {
    ok: true, count: 0,
    items: [], projects: [], runs: [], results: [], sets: [], use_cases: [],
    managed_use_cases: [], assessments: [], stages: [], experiments: [], events: [],
    invites: [], accounts: [], roles: [], privileges: [], capabilities: [], terms: [],
    members: [], proposals: [], comments: [], gaps: [],
  };
}

async function runRole(role) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => errors.push('jsdomError: ' + (e && (e.message || e.detail || e))));
  const me = meFor(role);

  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
    url: 'http://localhost/',
    beforeParse(window) {
      window.fetch = async (url) => {
        const path = new URL(url, 'http://localhost').pathname;
        const body = bodyFor(path, me);
        return { ok: true, status: 200, headers: { get: () => null },
                 json: async () => body, text: async () => JSON.stringify(body) };
      };
      window.addEventListener('error', (e) => errors.push('error: ' + (e.error?.message || e.message)));
      window.addEventListener('unhandledrejection', (e) => errors.push('unhandledrejection: ' + (e.reason?.message || e.reason)));
      // Things jsdom doesn't implement that boot may touch.
      window.scrollTo = () => {};
      window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
      const noop = () => {}; window.HTMLCanvasElement && (window.HTMLCanvasElement.prototype.getContext = () => null);
      window.scrollIntoView = noop;
    },
  });

  await new Promise((r) => setTimeout(r, 700)); // let loadMe()/boot settle
  const { document } = dom.window;
  const disp = (id) => { const el = document.getElementById(id); return el ? (el.style.display) : '(missing)'; };
  const navView = (v) => { const el = document.querySelector(`.pf-nav-item[data-view="${v}"]`); return el ? el.style.display : '(missing)'; };
  // Domain IA: the left rail lists DOMAINS (data-domain); a view's sub-tab only exists in
  // #domainTabs when its domain is active. So area-reachability is checked on the domain anchor.
  const domDisp = (key) => { const el = document.querySelector(`.pf-nav-item[data-domain="${key}"]`); return el ? el.style.display : '(missing)'; };
  const shown = (d) => d !== 'none' && d !== '(missing)';

  const checks = [];
  const ck = (name, cond, detail = '') => checks.push([cond ? 'PASS' : 'FAIL', `[${role}] ${name}`, detail]);

  ck('no uncaught errors at boot', errors.length === 0, errors.slice(0, 4).join('  |  '));
  // UI lean slice 1: personas removed as a navigation mechanism. There is ONE stable rail
  // for everyone — every domain the user is PERMITTED to see (RBAC), in canonical order, no
  // per-role reshuffling. The persona switcher is gone; visibility is gated by privilege.
  ck('persona switcher removed', !document.getElementById('personaSel'));
  ck('view-mode toggle present (in account menu)', !!document.getElementById('viewModeToggle'));
  // Dead masthead chips removed (selection lives in-view, Blueprint unbuilt).
  ck('assessment masthead chip removed', !document.getElementById('globalAssessmentSel'));
  ck('blueprint masthead chip removed', !document.getElementById('globalBlueprintSel'));
  // UI lean slice 2: contextual masthead chrome. Scope + Customer are filters shown only on the
  // views that consume them. Boot lands on Use Cases (author domain) → Customer shown, Scope hidden.
  ck('contextual chrome: Customer shown on Use Cases', disp('customerChip') !== 'none', 'display=' + disp('customerChip'));
  ck('persistent chrome: Scope shown on Use Cases (#259)', disp('scopeChip') !== 'none', 'display=' + disp('scopeChip'));
  // Masthead run selector retired → read-only run-status label (run is working context, not chrome).
  ck('run selector retired (read-only status)',
     !document.getElementById('globalRunSel') && !!document.getElementById('rccName'),
     'globalRunSel=' + !!document.getElementById('globalRunSel') + ' rccName=' + !!document.getElementById('rccName'));
  ck('domain top-tab strip present', !!document.getElementById('domainTabs'));
  ck('domain rail rendered', document.querySelectorAll('.pf-nav-item[data-domain]').length >= 2,
     'count=' + document.querySelectorAll('.pf-nav-item[data-domain]').length);
  // #140/#138: Roadmaps gained the Enhancement / PR workbench tab + view.
  ck('Enhancement/PR workbench view present',
     !!document.getElementById('view-enhancement') && !!document.getElementById('ewRouteBtn') && !!document.getElementById('ewSubmitBtn'));
  // #147: Assessments gained the Maturity Wall sub-view (FlightPath-style).
  ck('Maturity Wall view present',
     !!document.getElementById('view-maturity') && !!document.getElementById('mwWall') && !!document.getElementById('mwStates'));
  // Stable rail (no personas): Authoring + Catalog have no privilege gate, so they show for
  // every role. Assessments shows where assessment.view is held (all three fixtures have it /
  // are admin). The rail is identical in shape per RBAC — it does not reshuffle by role.
  ck('stable rail: Use Cases domain shown', shown(domDisp('author')), 'display=' + domDisp('author'));
  ck('stable rail: Catalog domain shown', shown(domDisp('catalog')), 'display=' + domDisp('catalog'));
  ck('stable rail: Assessments domain shown', shown(domDisp('assess')), 'display=' + domDisp('assess'));
  // UI lean slice 3: setup/admin domains folded into one Settings group (9 → 6 top-level).
  // Settings is a rail domain; Config / Prompts & Improvement / Customers & Projects / Audit
  // are no longer top-level domains — they are Settings sub-tabs.
  ck('consolidated: Settings domain shown', shown(domDisp('settings')), 'display=' + domDisp('settings'));
  ck('consolidated: Config not a top-level domain', domDisp('config') === '(missing)', 'display=' + domDisp('config'));
  ck('consolidated: Prompts/Improve not a top-level domain', domDisp('improve') === '(missing)', 'display=' + domDisp('improve'));
  ck('consolidated: Customers&Projects (org) not a top-level domain', domDisp('org') === '(missing)', 'display=' + domDisp('org'));
  ck('consolidated: Audit not a top-level domain', domDisp('audit') === '(missing)', 'display=' + domDisp('audit'));
  ck('consolidated: rail is ≤6 domains', document.querySelectorAll('.pf-nav-item[data-domain]').length <= 6,
     'count=' + document.querySelectorAll('.pf-nav-item[data-domain]').length);
  if (role === 'platform-admin') {
    // Authoring is multi-sub-view → the top strip renders ≥2 tabs (Use Cases · Scoping Sets · Discussion).
    ck('Authoring strip has ≥2 sub-tabs', document.querySelectorAll('#domainTabs .tab').length >= 2,
       'tabs=' + document.querySelectorAll('#domainTabs .tab').length);
  }
  // #137: a view-only role must not see edit affordances (data-edit-gate hidden when !canEdit).
  // project-viewer has no project.catalog → the catalog "Add capability" button is hidden; a privileged
  // role (not in view mode) sees it.
  if (role === 'project-viewer') {
    ck('view-only role: edit affordance hidden', disp('catSaveBtn') === 'none', 'display=' + disp('catSaveBtn'));
  } else if (role === 'platform-admin') {
    ck('editor role: edit affordance shown', disp('catSaveBtn') !== 'none', 'display=' + disp('catSaveBtn'));
  }
  // Capability method (#132): the catalog editor carries the DDD subdomain + R4 disposition controls.
  ck('catalog editor has subdomain + disposition controls',
     !!document.getElementById('catClass') && !!document.getElementById('catDisp') &&
     !!document.getElementById('catFit') && !!document.getElementById('catTech'),
     `class=${!!document.getElementById('catClass')} disp=${!!document.getElementById('catDisp')}`);
  // Catalog List ⇄ Board (R4 disposition decision surface) toggle present.
  ck('catalog has List/Board toggle',
     !!document.getElementById('catViewListBtn') && !!document.getElementById('catViewBoardBtn') && !!document.getElementById('catBoard'),
     `board=${!!document.getElementById('catBoard')}`);
  // #130 2b-iv: RBAC role-bindings List ⇄ Matrix (grant matrix) toggle present.
  ck('role-bindings has List/Matrix toggle',
     !!document.getElementById('bindViewListBtn') && !!document.getElementById('bindViewMatrixBtn') && !!document.getElementById('roleBindingsMatrix'),
     `matrix=${!!document.getElementById('roleBindingsMatrix')}`);
  // The separate Projects / Users & roles left-nav views are retired for everyone (→ Config → Platform):
  // no domain anchor and no view sub-tab exists for them.
  ck('separate Users/Projects nav retired',
     domDisp('users') === '(missing)' && domDisp('projects') === '(missing)' && navView('users') === '(missing)' && navView('projects') === '(missing)',
     `users=${domDisp('users')} projects=${domDisp('projects')}`);
  // Config is now tabbed: the Platform section-tab gates on the per-panel visibility; the
  // panels keep their own privilege gating (the e2e reads each panel's inline display).
  if (role === 'platform-admin') {
    ck('presence chip rendered', disp('presenceWrap') !== 'none', 'display=' + disp('presenceWrap'));
    ck('Access & Membership tab visible', disp('configTabAccess') !== 'none', 'display=' + disp('configTabAccess'));
    ck('Projects tab list present (relocated)', !!document.getElementById('projectsList'), 'projectsList in DOM');
    ck('Email (SMTP) panel visible', disp('configSmtpPanel') !== 'none', 'display=' + disp('configSmtpPanel'));
    ck('LDAP panel visible', disp('configLdapPanel') !== 'none', 'display=' + disp('configLdapPanel'));
    ck('Users & roles panel visible', disp('configUsersPanel') !== 'none', 'display=' + disp('configUsersPanel'));
    ck('Agents & tokens panel visible', disp('configAgentsPanel') !== 'none', 'display=' + disp('configAgentsPanel'));
    ck('Tenants panel visible', disp('configTenantsPanel') !== 'none', 'display=' + disp('configTenantsPanel'));
    ck('Groups panel visible', disp('configGroupsPanel') !== 'none', 'display=' + disp('configGroupsPanel'));
    ck('Bundles panel visible (usecat.manage)', disp('configBundlesPanel') !== 'none', 'display=' + disp('configBundlesPanel'));
  } else if (role === 'project-admin') {
    // project admin: manages projects in the Customers & Projects → Projects tab now; the
    // Config Platform tab is hidden (only platform-admin settings — SMTP/LDAP/Users — remain there).
    ck('presence chip hidden', disp('presenceWrap') === 'none', 'display=' + disp('presenceWrap'));
    ck('Access & Membership tab hidden', disp('configTabAccess') === 'none', 'display=' + disp('configTabAccess'));
    ck('Projects tab list present (relocated)', !!document.getElementById('projectsList'), 'projectsList in DOM');
    ck('Email (SMTP) panel hidden', disp('configSmtpPanel') === 'none', 'display=' + disp('configSmtpPanel'));
    ck('LDAP panel hidden', disp('configLdapPanel') === 'none', 'display=' + disp('configLdapPanel'));
    ck('Users & roles panel hidden', disp('configUsersPanel') === 'none', 'display=' + disp('configUsersPanel'));
    ck('Agents & tokens panel hidden', disp('configAgentsPanel') === 'none', 'display=' + disp('configAgentsPanel'));
    ck('Tenants panel hidden', disp('configTenantsPanel') === 'none', 'display=' + disp('configTenantsPanel'));
    ck('Groups panel hidden', disp('configGroupsPanel') === 'none', 'display=' + disp('configGroupsPanel'));
    ck('Bundles panel hidden (no usecat.manage)', disp('configBundlesPanel') === 'none', 'display=' + disp('configBundlesPanel'));
  } else {
    ck('presence chip hidden', disp('presenceWrap') === 'none', 'display=' + disp('presenceWrap'));
    ck('Access & Membership tab hidden', disp('configTabAccess') === 'none', 'display=' + disp('configTabAccess'));
  }
  // (Audit's gating — platform-admin only — rides the same _domainPermitted/__platAdmin
  // predicate as the Config Platform panels checked above; it surfaces in the Operator
  // persona. Not re-driven here to avoid a full Config-domain load under stub fixtures.)
  dom.window.close();
  return checks;
}

const all = [];
for (const role of Object.keys(ROLES)) all.push(...await runRole(role));
const fails = all.filter((c) => c[0] === 'FAIL');
for (const [s, n, d] of all) console.log(`[${s}] ${n}` + (s !== 'PASS' && d ? `  -- ${d}` : ''));
console.log(`\n===== UI E2E: ${all.length - fails.length} PASS / ${fails.length} FAIL =====`);
process.exit(fails.length ? 1 : 0);

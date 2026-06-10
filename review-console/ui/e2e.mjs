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
                 'project.catalog', 'blueprint.view', 'blueprint.edit'],
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

  const checks = [];
  const ck = (name, cond, detail = '') => checks.push([cond ? 'PASS' : 'FAIL', `[${role}] ${name}`, detail]);

  ck('no uncaught errors at boot', errors.length === 0, errors.slice(0, 4).join('  |  '));
  // Workspace focus (#101): switcher present; nav filters to the active focus. Default is
  // role-derived — platform-admin → Architecture; assessment-only users → Assessment.
  ck('focus switcher present', !!document.getElementById('focusSwitch'));
  ck('view-mode toggle present', !!document.getElementById('viewModeToggle'));
  if (role === 'platform-admin') {
    ck('Architecture focus: Use Cases nav shown', navView('usecases') !== 'none', 'display=' + navView('usecases'));
    ck('Architecture focus: Assessments nav hidden', disp('navAssess') === 'none', 'display=' + disp('navAssess'));
  } else {
    // project-admin + project-viewer have assessment.view but no UC/run pipeline → Assessment focus.
    ck('Assessment focus: Assessments nav shown', disp('navAssess') !== 'none', 'display=' + disp('navAssess'));
    ck('Assessment focus: Use Cases nav hidden', navView('usecases') === 'none', 'display=' + navView('usecases'));
    ck('Catalog (shared) nav shown in either focus', navView('catalog') !== 'none', 'display=' + navView('catalog'));
  }
  // The separate Projects / Users & roles left-nav views are retired for everyone (→ Config → Platform).
  ck('separate Users/Projects nav views retired', disp('navUsers') === 'none' && disp('navProjects') === 'none', `users=${disp('navUsers')} projects=${disp('navProjects')}`);
  // Config is now tabbed: the Platform section-tab gates on the per-panel visibility; the
  // panels keep their own privilege gating (the e2e reads each panel's inline display).
  if (role === 'platform-admin') {
    ck('presence chip rendered', disp('presenceWrap') !== 'none', 'display=' + disp('presenceWrap'));
    ck('Audit nav visible', disp('navAudit') !== 'none', 'display=' + disp('navAudit'));
    ck('Platform config tab visible', disp('configTabAccess') !== 'none', 'display=' + disp('configTabAccess'));
    ck('Projects panel visible', disp('configProjectsPanel') !== 'none', 'display=' + disp('configProjectsPanel'));
    ck('Email (SMTP) panel visible', disp('configSmtpPanel') !== 'none', 'display=' + disp('configSmtpPanel'));
    ck('LDAP panel visible', disp('configLdapPanel') !== 'none', 'display=' + disp('configLdapPanel'));
    ck('Users & roles panel visible', disp('configUsersPanel') !== 'none', 'display=' + disp('configUsersPanel'));
  } else if (role === 'project-admin') {
    // project admin: sees the Platform tab (via Projects) + Projects panel ONLY — not SMTP/LDAP/Users.
    ck('presence chip hidden', disp('presenceWrap') === 'none', 'display=' + disp('presenceWrap'));
    ck('Audit nav hidden', disp('navAudit') === 'none', 'display=' + disp('navAudit'));
    ck('Platform config tab visible', disp('configTabAccess') !== 'none', 'display=' + disp('configTabAccess'));
    ck('Projects panel visible', disp('configProjectsPanel') !== 'none', 'display=' + disp('configProjectsPanel'));
    ck('Email (SMTP) panel hidden', disp('configSmtpPanel') === 'none', 'display=' + disp('configSmtpPanel'));
    ck('LDAP panel hidden', disp('configLdapPanel') === 'none', 'display=' + disp('configLdapPanel'));
    ck('Users & roles panel hidden', disp('configUsersPanel') === 'none', 'display=' + disp('configUsersPanel'));
  } else {
    ck('presence chip hidden', disp('presenceWrap') === 'none', 'display=' + disp('presenceWrap'));
    ck('Audit nav hidden', disp('navAudit') === 'none', 'display=' + disp('navAudit'));
    ck('Platform config tab hidden', disp('configTabAccess') === 'none', 'display=' + disp('configTabAccess'));
  }
  dom.window.close();
  return checks;
}

const all = [];
for (const role of Object.keys(ROLES)) all.push(...await runRole(role));
const fails = all.filter((c) => c[0] === 'FAIL');
for (const [s, n, d] of all) console.log(`[${s}] ${n}` + (s !== 'PASS' && d ? `  -- ${d}` : ''));
console.log(`\n===== UI E2E: ${all.length - fails.length} PASS / ${fails.length} FAIL =====`);
process.exit(fails.length ? 1 : 0);

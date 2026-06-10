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
  'project-viewer': {
    is_platform_admin: false, is_admin: false, is_project_admin: false,
    privileges: ['project.data.read', 'assessment.view', 'blueprint.view'],
  },
};

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

  const checks = [];
  const ck = (name, cond, detail = '') => checks.push([cond ? 'PASS' : 'FAIL', `[${role}] ${name}`, detail]);

  ck('no uncaught errors at boot', errors.length === 0, errors.slice(0, 4).join('  |  '));
  if (role === 'platform-admin') {
    ck('presence chip rendered', disp('presenceWrap') !== 'none', 'display=' + disp('presenceWrap'));
    ck('Users nav visible', disp('navUsers') !== 'none', 'display=' + disp('navUsers'));
    ck('Audit nav visible', disp('navAudit') !== 'none', 'display=' + disp('navAudit'));
    ck('Email (SMTP) panel present', !!document.getElementById('configSmtpPanel'));
  } else {
    ck('presence chip hidden', disp('presenceWrap') === 'none', 'display=' + disp('presenceWrap'));
    ck('Users nav hidden', disp('navUsers') === 'none', 'display=' + disp('navUsers'));
    ck('Audit nav hidden', disp('navAudit') === 'none', 'display=' + disp('navAudit'));
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

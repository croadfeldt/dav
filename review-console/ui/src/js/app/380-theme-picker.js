// ══════════════════════════ THEME PICKER ══════════════════════════

const THEMES = ['amber', 'slate', 'solarized', 'redhat'];
const MODES  = ['auto', 'light', 'dark'];
const THEME_LABEL = { amber:'Amber', slate:'Slate', solarized:'Solarized' };
const MODE_LABEL  = { auto:'Auto', light:'Light', dark:'Dark' };

let currentTheme = 'amber';
let currentMode  = 'auto';  // user selection: 'auto' | 'light' | 'dark'

function getResolvedMode() {
  if (currentMode !== 'auto') return currentMode;
  return (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
}

function applyTheme() {
  document.documentElement.setAttribute('data-theme', currentTheme);
  document.documentElement.setAttribute('data-mode', getResolvedMode());
  // Mark the active appearance options in the account menu.
  document.querySelectorAll('#acctMenu [data-mode]').forEach(b =>
    b.classList.toggle('active', b.dataset.mode === currentMode));
  document.querySelectorAll('#acctMenu [data-theme]').forEach(b =>
    b.classList.toggle('active', b.dataset.theme === currentTheme));
}

function setTheme(t) {
  if (!THEMES.includes(t)) return;
  currentTheme = t;
  try { localStorage.setItem('davTheme', t); } catch(e) {}
  applyTheme();
  try { _persistUserSettings(); } catch {}
}
function setMode(m) {
  if (!MODES.includes(m)) return;
  currentMode = m;
  try { localStorage.setItem('davMode', m); } catch(e) {}
  applyTheme();
  try { _persistUserSettings(); } catch {}
}

function initTheme() {
  try {
    currentTheme = localStorage.getItem('davTheme') || 'amber';
    currentMode  = localStorage.getItem('davMode')  || 'auto';
  } catch(e) {}
  if (!THEMES.includes(currentTheme)) currentTheme = 'amber';
  if (!MODES.includes(currentMode))   currentMode  = 'auto';
  applyTheme();

  // Re-apply when the system preference changes (only matters in 'auto' mode)
  if (window.matchMedia) {
    const mql = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => { if (currentMode === 'auto') applyTheme(); };
    if (mql.addEventListener) mql.addEventListener('change', onChange);
    else if (mql.addListener) mql.addListener(onChange);
  }

  // Wire up the account menu (appearance + account actions).
  document.getElementById('acctChipBtn').addEventListener('click', e => {
    e.stopPropagation();
    document.getElementById('acctMenu').classList.toggle('open');
  });
  document.querySelectorAll('#acctMenu [data-mode]').forEach(b =>
    b.addEventListener('click', () => setMode(b.dataset.mode)));
  document.querySelectorAll('#acctMenu [data-theme]').forEach(b =>
    b.addEventListener('click', () => setTheme(b.dataset.theme)));
  document.getElementById('acctLogout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', { method:'POST' }); } catch(e) {}
    location.reload();
  });
  document.getElementById('acctChangePw').addEventListener('click', () => {
    document.getElementById('acctMenu').classList.remove('open');
    const c = document.getElementById('pwChangeCancel'); if (c) c.style.display = '';
    const sub = document.getElementById('pwChangeSub'); if (sub) sub.textContent = 'Enter your current password and a new one.';
    document.getElementById('pwChangeOverlay').style.display = 'flex';
  });
  document.getElementById('pwChangeCancel')?.addEventListener('click', () => {
    document.getElementById('pwChangeOverlay').style.display = 'none';
    document.getElementById('pwChangeCur').value = ''; document.getElementById('pwChangeNew').value = '';
    document.getElementById('pwChangeMsg').textContent = '';
  });
  document.addEventListener('click', e => {
    const menu = document.getElementById('acctMenu');
    const btn  = document.getElementById('acctChipBtn');
    if (!menu.contains(e.target) && !btn.contains(e.target)) menu.classList.remove('open');
  });
}

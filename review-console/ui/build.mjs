#!/usr/bin/env node
// DAV review-console UI build — assemble the single deployed `index.html` from
// editable source modules under `src/`. Modularization groundwork (no deps).
//
// WHY a build instead of <script src>/<link href>:
//   index.html is the ONE artifact the whole pipeline consumes — nginx serves it,
//   the Containerfile `COPY index.html` + `sed __DAV_BUILD__`, lint.sh extracts its
//   inline scripts, e2e.mjs loads it in jsdom. Keeping index.html as a single inline
//   file (just GENERATED from modules) means none of that has to change, and runtime
//   semantics are byte-identical (one global script scope, one CSS block). Editing
//   moves to src/; index.html becomes a generated, drift-guarded artifact — the same
//   "generate + --check" pattern as engine/dav/scripts/export_dcm_vocab.py.
//
// Modes:
//   node build.mjs            build src/ → index.html (writes)
//   node build.mjs --check    rebuild in memory; exit 1 if it differs from the
//                             committed index.html (CI/lint drift guard)
//   node build.mjs --bootstrap  one-time: carve the CURRENT index.html into src/
//                             modules, then rebuild it byte-identically (self-checks)
//
// Module layout (assembled in this order):
//   src/app.template.html   the HTML shell + all view markup, with @DAV-MODULE markers
//   src/styles/app.css      the head <style> body (pure CSS)
//   src/js/theme-init.js    the early FOUC-guard <script> body (pure JS)
//   src/js/app/*.js         the main <script> body, concatenated in filename order
//
// Marker syntax (one per line, inside the template):
//   <!--@DAV-MODULE styles/app.css style-->        → <style>…file…</style>
//   <!--@DAV-MODULE js/theme-init.js script-->     → <script>…file…</script>
//   <!--@DAV-MODULE js/app/ script-->              → <script>…concat dir/*…</script>
//
// Contract: EVERYTHING is line-based and joined with '\n'. Module files hold exact
// lines with NO trailing newline, so files.join('\n') reproduces the original bytes.

import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync, rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = dirname(fileURLToPath(import.meta.url));
const SRC = join(ROOT, 'src');
const INDEX = join(ROOT, 'index.html');
const MARKER = /^<!--@DAV-MODULE (\S+) (style|script)-->$/;

const readLines = (p) => readFileSync(p, 'utf8').split('\n');
const isDir = (name) => name.endsWith('/');

// Expand one template line: a marker → its wrapped module lines; else the line itself.
function expand(line) {
  const m = line.match(MARKER);
  if (!m) return [line];
  const [, ref, kind] = m;
  let body;
  if (isDir(ref)) {
    // Concatenate every file in the dir, sorted by name, joined by '\n'.
    const dir = join(SRC, ref);
    const files = readdirSync(dir).filter(f => !f.startsWith('.')).sort();
    body = files.flatMap(f => readLines(join(dir, f)));
  } else {
    body = readLines(join(SRC, ref));
  }
  const [open, close] = kind === 'style' ? ['<style>', '</style>'] : ['<script>', '</script>'];
  return [open, ...body, close];
}

function build() {
  const template = readLines(join(SRC, 'app.template.html'));
  return template.flatMap(expand).join('\n');
}

// ── one-time: carve current index.html into src/ modules ────────────────────────
function bootstrap() {
  const original = readFileSync(INDEX, 'utf8');
  const lines = original.split('\n');

  // Anchor by structure (robust to line drift). Head ends at first '</head>'.
  const idx = (needle, from = 0) => { const i = lines.indexOf(needle, from); if (i < 0) throw new Error(`anchor not found: ${JSON.stringify(needle)}`); return i; };
  const headEnd   = idx('</head>');
  const themeOpen = idx('<script>');                 // first inline script = FOUC theme guard (head)
  const themeClose= idx('</script>', themeOpen);
  const styleOpen = idx('<style>');                  // first <style> = head styles
  const styleClose= idx('</style>', styleOpen);
  if (themeClose >= headEnd || styleClose >= headEnd) throw new Error('head anchors out of range');
  const mainOpen  = idx('<script>', headEnd);        // the big inline script lives in <body>
  const mainClose = lines.indexOf('</script>', mainOpen);
  if (mainClose < 0) throw new Error('main </script> not found');

  // Module bodies (exclusive of the wrapping tags), stored as exact lines, no trailing NL.
  const themeBody = lines.slice(themeOpen + 1, themeClose).join('\n');
  const styleBody = lines.slice(styleOpen + 1, styleClose).join('\n');
  const mainBody  = lines.slice(mainOpen + 1, mainClose).join('\n');

  // Template = original lines with each block (tags included) collapsed to a marker line.
  // Replace from the BOTTOM up so earlier indices stay valid.
  const tpl = lines.slice();
  tpl.splice(mainOpen,  mainClose  - mainOpen  + 1, '<!--@DAV-MODULE js/app/ script-->');
  tpl.splice(styleOpen, styleClose - styleOpen + 1, '<!--@DAV-MODULE styles/app.css style-->');
  tpl.splice(themeOpen, themeClose - themeOpen + 1, '<!--@DAV-MODULE js/theme-init.js script-->');

  // Write modules.
  if (existsSync(SRC)) rmSync(SRC, { recursive: true });
  mkdirSync(join(SRC, 'styles'), { recursive: true });
  mkdirSync(join(SRC, 'js', 'app'), { recursive: true });
  writeFileSync(join(SRC, 'app.template.html'), tpl.join('\n'));
  writeFileSync(join(SRC, 'styles', 'app.css'), styleBody);
  writeFileSync(join(SRC, 'js', 'theme-init.js'), themeBody);
  // Seed the main script as a single module; future PRs carve it into 010-*.js, … by
  // filename order — each split is byte-preserving (line slices joined by '\n').
  writeFileSync(join(SRC, 'js', 'app', '000-core.js'), mainBody);

  // Self-verify: rebuild must reproduce the original byte-for-byte.
  const rebuilt = build();
  if (rebuilt !== original) {
    const a = original.split('\n'), b = rebuilt.split('\n');
    let i = 0; while (i < a.length && i < b.length && a[i] === b[i]) i++;
    throw new Error(`bootstrap round-trip MISMATCH at line ${i + 1}:\n  orig: ${JSON.stringify(a[i])}\n  built:${JSON.stringify(b[i])}`);
  }
  writeFileSync(INDEX, rebuilt);
  console.log('bootstrap OK — src/ carved, index.html regenerated byte-identical');
}

const mode = process.argv[2];
if (mode === '--bootstrap') {
  bootstrap();
} else if (mode === '--check') {
  const built = build();
  const current = readFileSync(INDEX, 'utf8');
  if (built !== current) {
    console.error('DRIFT: index.html is out of sync with src/ — run `node build.mjs` and commit the result.');
    process.exit(1);
  }
  console.log('OK: index.html matches src/');
} else {
  writeFileSync(INDEX, build());
  console.log('built index.html from src/');
}

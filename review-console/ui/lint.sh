#!/usr/bin/env bash
# UI static checks (QA pipeline — code layer). Run before every UI deploy.
#
#   ./review-console/ui/lint.sh
#
# Two checks on the inline JS extracted from index.html:
#   1. node --check     — syntax errors.
#   2. eslint no-undef  — undefined-variable references. CRITICAL: this catches the class
#      of bug node --check MISSES — a handler calling a function that doesn't exist
#      (e.g. `hasPriv(...)` instead of `can(...)`, or `log_warn?.(...)`), which throws a
#      ReferenceError at runtime and can abort a whole init path (it stripped the admin
#      status bar once). Both of those real bugs were found the day this lint was added.
#
# `openNewUC`/`newUC` are intentionally probed via `typeof X === 'function'` (optional /
# future handlers) — declared as globals so no-undef doesn't false-positive on them.
set -euo pipefail
cd "$(dirname "$0")"

TMP="$(mktemp /tmp/dav_ui.XXXXXX.js)"
trap 'rm -f "$TMP"' EXIT

python3 - "$TMP" <<'PY'
import re, sys
html = open('index.html').read()
# Concatenate every inline <script> body (skip external src= scripts).
blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
open(sys.argv[1], 'w').write('\n;\n'.join(blocks))
print(f'extracted {len(blocks)} inline script block(s)', file=sys.stderr)
PY

echo "→ node --check (syntax)"
node --check "$TMP"

echo "→ eslint no-undef (undefined references)"
npx --yes eslint@8 --no-eslintrc \
  --env browser,es2022 \
  --parser-options ecmaVersion:2022 \
  --global openNewUC --global newUC \
  --rule '{"no-undef":"error"}' "$TMP"

# Boot-smoke e2e (jsdom): load the real index.html per role, assert no boot errors +
# correct role-gated rendering. Catches runtime/gating regressions the static checks miss.
if [ "${SKIP_E2E:-0}" != "1" ]; then
  echo "→ e2e boot-smoke (jsdom, per role)"
  [ -d node_modules ] || npm install --silent >/dev/null 2>&1
  node e2e.mjs
fi

echo "✓ UI lint OK"

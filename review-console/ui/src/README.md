# DAV review-console UI — source modules

`index.html` is **generated**, not hand-edited. Edit the modules here, then run the
build. This is the groundwork for breaking up the historical 17k-line single-file app
into editable pieces **without changing runtime semantics or the deploy**.

## Why it's built this way
`index.html` is the one artifact the whole pipeline consumes — nginx serves it, the
Containerfile does `COPY index.html` + `sed __DAV_BUILD__`, `lint.sh` extracts its inline
scripts, and `e2e.mjs` loads it in jsdom. So instead of switching to `<script src>` /
`<link href>` (which would break all of that and change load semantics), we keep
`index.html` as a single inline file that is **assembled from `src/` modules**. The
deployed bytes are identical; only the *source of truth* moves to `src/`.

This mirrors the repo's existing "generate + `--check`" pattern (cf.
`engine/src/dav/scripts/export_dcm_vocab.py`).

## Layout
| Path | What |
|------|------|
| `app.template.html` | the ~168-line skeleton: `<head>`, body chrome, footer + `@DAV-MODULE` markers |
| `shell/masthead.html` | the masthead (`<header class="pf-masthead">`) markup |
| `shell/modals.html` | all the modal-overlay dialogs |
| `views/<id>.html` | one `<section class="pf-view" id="view-<id>">` per file (17 views) |
| `styles/app.css` | the head `<style>` body — **edit as CSS** |
| `js/theme-init.js` | the early FOUC-guard script body — **edit as JS** |
| `js/app/*.js` | the main script body in 16 feature modules, concatenated in **filename order** |

The 17k-line single file is now ~40 modules. Assembled in template order; `js/app/` files
concatenate by sorted filename (`000-core`, `100-runs`, …, `900-core`).
Everything is line-based and joined with `\n`, so module files hold exact lines with **no
trailing newline** — `files.join('\n')` reproduces the original bytes.

## Workflow
```bash
# edit a module under src/, then:
node build.mjs            # regenerate index.html
./lint.sh                 # drift check + syntax + no-undef + e2e boot-smoke
git add src/ index.html   # commit BOTH (index.html is the committed build output)
```
`lint.sh` runs `node build.mjs --check` first and fails if `index.html` is out of sync
with `src/` — so you can't forget to rebuild.

## Markers (in `app.template.html`)
```
<!--@DAV-MODULE styles/app.css style-->     → <style>…file…</style>
<!--@DAV-MODULE js/theme-init.js script-->  → <script>…file…</script>
<!--@DAV-MODULE js/app/ script-->           → <script>…concat dir/* by name…</script>
```

## Extracting a finer module (the incremental playbook)
The goal is to carve the 14k-line `js/app/000-core.js` (and later the template) into
small, named modules — **one small, drift-guarded PR at a time**:

1. **JS section** → move a contiguous block of lines out of `js/app/000-core.js` into a
   new `js/app/NNN-<name>.js` (pick `NNN` so sorted order == original order, e.g. split
   `000-core.js` into `000-core.js` + `300-roadmaps.js` + `900-tail.js`). Because all
   `js/app/*.js` concatenate in filename order into the **same global script scope**,
   any split point is semantically a no-op — no imports/exports, no scope changes.
2. **Run `node build.mjs` and confirm `git diff index.html` is empty** (byte-identical) —
   that's your proof the move changed nothing.
3. `./lint.sh` (e2e boot-smoke must stay green), commit, ship.
4. **View markup** → later, replace a `<section class="pf-view" id="view-X">…</section>`
   block in `app.template.html` with `<!--@DAV-MODULE views/X.html html-->` and add the
   `html` wrap kind to `build.mjs` (no `<style>/<script>` wrapper).

Rules of thumb: split at existing blank-line / function boundaries; never reorder; keep
each PR to one section; let the empty `index.html` diff + e2e be the safety net.

## Future option (not done yet)
The committed `index.html` duplicates `src/`. A later step can drop the committed artifact
by having the **Containerfile build it** (multi-stage: a node stage runs `build.mjs`, the
nginx stage copies the output). Deferred to keep this groundwork's deploy path unchanged.

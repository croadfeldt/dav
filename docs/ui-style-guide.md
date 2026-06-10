# DAV review-console — UI/UX style guide

_Chris 2026-06-10: "we should have a UI/UX style and stick to it."_

The single source of truth for how the review-console UI looks and behaves. The token +
theme layer is already strong; the drift is at the component layer (~1,400 inline
`style="…"` attributes, two different nav idioms). This guide codifies the canon and the
**adoption rule** so it stops drifting.

## Adoption rule (how we stop the drift)
- **New UI uses the component classes below — not bespoke inline styles.** Inline `style=`
  is for genuinely one-off positioning only, never for re-implementing a card, button,
  header, badge, or tab.
- **Convert opportunistically:** when you touch an area, migrate its inline styling to the
  canon. No big-bang rewrite of the existing 1,400 inline styles.
- **Tokens only — never a raw hex value** in new markup/CSS. If a needed color doesn't
  exist as a token, add a token, don't hardcode. (A lint guard for new raw-hex is a
  follow-up, paralleling the JS `no-undef` guard.)
- This guide is **living**: update it in the same change that introduces or revises a
  pattern.

## Theming & tokens
The UI is themeable via `<html data-theme data-mode>` (`amber`|`slate` × `dark`|`light`);
every color is a CSS variable redefined per theme×mode. **Always consume tokens**, so a new
theme just works.

| Token | Use |
|---|---|
| `--bg`, `--bg-raised`, `--bg-panel`, `--bg-input` | surfaces: page → card → panel header → input |
| `--border`, `--border-bright` | hairlines; `-bright` for interactive/input edges |
| `--text`, `--text-dim`, `--text-faint` | primary / secondary / tertiary text |
| `--accent`, `--accent-soft`, `--accent-bg` | **primary action + active/selected state** |
| `--green`/`--green-bg` | success · accept · positive maturity |
| `--red` | danger · reject · destructive |
| `--blue`/`--blue-bg` | applied · informational |
| `--purple` | special categorization (use sparingly) |
| `--sans`, `--serif`, `--mono` | UI text / long-form / code+IDs+numbers |

**Color semantics are fixed:** accent = primary/active, green = accept/success, red =
reject/danger, blue = applied/info. Don't repurpose them.

## Type scale
Small, dense, functional. Stick to these sizes:

| px | Role |
|---|---|
| 14 | view/detail title (only) |
| 13 | card/panel title (`.pc-title`) |
| 12 | body, form labels, nav links |
| 11 | buttons, secondary text, table cells |
| 10 | meta / sub-labels (`.pc-sub`), uppercase eyebrows |
| 9  | timestamps, faint annotations |

Eyebrow/section labels: `10px`, `uppercase`, `letter-spacing:.05–.08em`, `--text-faint`.
IDs, counts, tokens, durations → `--mono`.

## Spacing scale
Use `6 / 8 / 10 / 12 / 14 / 16 px`. Card body gap `10px`; card padding `14px 16px`; header
padding `12px 16px`; section gap `12–14px`. Don't invent in-between values.

## Components (the canon)

### Buttons — `.btn`
One compact size. Base `.btn`; variants `.btn.primary` (accent fill — the single main
action per context), `.btn.ghost` (borderless secondary), `.btn.danger` (destructive).
`.btn-sm` for inline/toolbar; `.btn-icon.btn-sm` for icon-only. Disabled handled by the
class. **Never** restyle a button inline.

### Tabs — `.tabs` / `.tab` / `.tabpanel`  ← the standardized section-switcher
Horizontal underline tabs for switching between **sibling sections within one view**
(Config sections, Prompts & Improvements modes). Active = accent text + accent underline.
```html
<div class="tabs" role="tablist">
  <button class="tab active" data-tab="models">Models</button>
  <button class="tab" data-tab="mcp">MCP servers</button>
</div>
<div class="tabpanel active" data-tabpanel="models">…</div>
<div class="tabpanel" data-tabpanel="mcp">…</div>
```
Toggle by adding/removing `.active` on the matching `.tab` + `.tabpanel`
(`wireTabs(container)` helper). Role-gating hides individual `.tab`s; the strip wraps
gracefully. **Retire** the per-feature `.improve-mode-tab` and ad-hoc tab buttons onto this.

### Cards / panels — `.panel-card`
`.panel-card` (raised surface, hairline, 2px radius) → `.panel-card-header`
(`.pc-title` 13px + optional `.pc-sub` 10px) → `.panel-card-body` (flex column, 10px gap).
Every settings/section block is a `.panel-card`.

### Navigation rule
- **Left app-nav** (the main rail) = top-level app sections only.
- **Tabs** (`.tabs`) = sibling sections *within* a view.
- The old `.config-nav` left-rail inside Config is **superseded by `.tabs`** (the Config
  conversion — task #107-adjacent). Don't add new left-rail sub-navs.

### Form rows
Label above control; label `12px --text-dim`; inputs use `--bg-input` + `--border-bright`,
2px radius, `11–12px`. Group related rows in a `.panel-card-body`.

### Tables / lists
Header row `10px uppercase --text-faint`; cells `11px`; row separators `1px solid
--border`; selected row `--bg-raised` + 2px `--accent` left border. Hover `--bg-raised`.

### Badges / pills
Small status chips: `9–10px`, `uppercase`, 2px radius, semantic color + faint bg. Existing:
proposal status (`_statusBadge`), maturity (`_matBubble`/`_capPill`, 1–5 color ramp). New
status pills follow the same shape + the color semantics above.

### Empty states
Centered, `--text-faint`, `12px`, ~22px padding, one line of guidance ("No X yet. Do Y to
create some.").

### Activity timeline
Vertical list; each row = a colored dot (semantic per action) + bold label + dim
`by <actor>` + relative time (`_ago()`) + optional note. See the Improvements proposal
Activity pane. Reuse for any entity lifecycle (audit detail #103, assessment notes #102).

### Spinners & toasts
In-flight LLM/data waits → `.llm-spinner` (inline) or the busy overlay. Transient feedback
→ `toast(msg, isError)`. Don't roll your own.

## Relative time
`_ago(iso)` for compact relative ("3d ago"); full timestamp via `toLocaleString()` in a
`title=` / parenthetical. Don't hand-format dates inline.

## Do / Don't
- **Do** compose classes + tokens; keep it dense and quiet.
- **Do** keep one primary (`.btn.primary`) action per context.
- **Don't** hardcode hex, font sizes off-scale, or bespoke spacing.
- **Don't** introduce a third nav idiom — tabs within a view, app-nav at the top.
- **Don't** restyle shared components inline.

## Enforcement / follow-ups
- Component classes live in the `<style>` block of `review-console/ui/index.html`.
- Future: extend `ui/lint.sh` to flag new raw-hex in inline styles (like `no-undef`).
- The jsdom e2e already asserts role-gated structure; add a check when a view's tab
  structure is role-dependent.

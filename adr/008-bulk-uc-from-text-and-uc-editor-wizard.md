# ADR-008: Bulk UC Creation from Text + UC Editor Wizard

**Status:** Accepted

## Context

Authoring use cases (UCs) one at a time through the single-pane raw-YAML
modal is fine for adding one or two UCs after a code change but breaks
down for two common workflows:

1. **Transcript-to-UCs.** A reviewer holds a 30-minute conversation
   with a customer or stakeholder, ends with three pages of pasted
   notes, and now needs five-to-ten distinct UCs out of those notes.
   Doing this serially through the single-UC modal is slow, loses the
   transcript context across openings, and forces the reviewer to do
   the parsing-into-distinct-scenarios work that an LLM is well-suited
   to.

2. **First-time UC authors.** The current ucModal opens to a YAML
   template that requires the author to know the schema (enum values
   for lifecycle phase, capability codes, expected-outcome structure)
   before they can type anything. The `✦ Assist` sidebar helps but
   feels bolted-on — it lives in a separate panel, doesn't share state
   with the main editor across modal opens, and the author still has
   to inspect-and-paste the YAML block themselves.

The existing flow is documented in [ADR-001](001-dav-consumer-agnostic-framework.md)'s
notion of "managed UCs" — DB-backed UCs that can be pushed to the
corpus repo later. That contract stays. This ADR only reshapes the
*creation* surface.

## Decision

Two related but independently shippable surfaces:

### A. Bulk UC from text — `M12a`

A new entry point `📋 Bulk import` in the Use Cases tab opens a
multi-step modal that:

1. Accepts a free-form text paste (transcript, design doc excerpt,
   meeting notes — anything containing latent UC material) and a
   model selection.
2. Calls a new server endpoint `POST /api/use-cases/bulk-from-text`
   that runs the text through a tailored system prompt instructing
   the model to extract **distinct** UCs, returning a YAML list of
   `{title, yaml_content, rationale, source_excerpt}` objects.
3. Renders the list as review cards with per-card include/exclude
   checkboxes and an inline expand-to-edit YAML pane.
4. On `Save selected`, iterates `POST /api/use-cases` for each
   included card (drafts; lifecycle_state = `draft`), reporting
   per-item success/failure.
5. Presents a **post-save Set assignment step**: radio between
   `Create new Set` (name input), `Add to existing Set` (dropdown),
   `Assign per-UC individually`, or `Skip`. The Sets work is deferred
   until UCs exist so the user can't lose drafts to a Set-creation
   error.

The bulk endpoint is **additive**: the existing `POST /api/use-cases`
single-UC flow is untouched, and the per-card edit path opens the
same `ucModal` (now also the M12b wizard target — see B) so a
reviewer can promote any extracted draft to the full editor.

#### Set-assignment contract

Per-UC Set membership rows use the existing
`POST /api/sets/{id}/members` endpoint. Creating a new Set uses
`POST /api/sets`. The bulk modal orchestrates these client-side; no
new `bulk-with-set` server endpoint. This keeps the server contract
small and forces UCs to be saved before Set linkage, matching how a
reviewer thinks about it: "save the work first, file it after."

### B. UC editor wizard — `M12b`

The existing `ucModal` raw-YAML editor stays as an `Advanced` tab
inside a new `ucWizardModal`. The wizard's default path is a
five-step flow:

1. **Scenario** — natural-language description of what the UC
   exercises. Optional context (linked PR comment, design doc URL).
2. **Extract** — LLM call populates structured fields:
   `title`, `scenario_summary`, `expected_outcomes`, `capabilities`,
   `lifecycle_phase`, `tags_suggested`. Fields render in editable
   form widgets — capability codes as a multi-select pre-seeded with
   the engine's `_DCM_LIFECYCLE_PHASES` vocabulary, etc.
3. **Review** — rendered YAML preview with diff against the
   pre-extraction template, plus the same `✦ Assist` chat for
   targeted revisions.
4. **Assign** — tags + Set picker (same trio as bulk: new / existing /
   skip).
5. **Save** — confirm + POST to `/api/use-cases`.

Each step is back/forward navigable; state persists in the modal's
internal object until the wizard is closed. `Skip to advanced YAML`
is always available and dumps the in-progress state into the legacy
ucModal.

`editUC(uuid)` continues to open the legacy ucModal directly — the
wizard is for **creation**, not editing.

## Consequences

### Positive

- Bulk import collapses the transcript-to-UCs latency from "an hour
  of manual triage" to "paste, review, save."
- New authors get a guided path; experienced authors keep raw-YAML
  velocity via the Advanced tab.
- Both paths share the same downstream save endpoint
  (`POST /api/use-cases`) and the same validation
  (`_validate_uc_yaml`), so server-side correctness is unchanged.
- Set assignment after creation matches the reviewer's mental model
  and avoids losing drafts to Set-creation failures.

### Negative

- Two new modals (`bulkImportModal`, `ucWizardModal`) increase UI
  surface area. Mitigated by reusing existing widgets (model picker,
  Set dropdown, tags input) rather than re-implementing.
- The bulk-extract LLM call is **best-effort**: the model may emit
  malformed YAML or merge what should be distinct UCs. Drafts always
  land as `lifecycle_state = draft` so the reviewer can prune.
- The wizard adds a step count — most authors will see five clicks
  where they used to see one modal. Acceptable for novices; power
  users skip via Advanced.

### Forward path

- M13 candidate: feed the bulk extractor's `source_excerpt` field
  into the UC's provenance metadata so a "show me where this UC came
  from" view can highlight the transcript snippet that produced it.
  Mirrors the existing `uc_pr_comment_links` provenance pattern from
  [ADR-005](005-shared-credentials-abstraction.md)-adjacent work.
- M13 candidate: wizard's structured-field extraction can also seed
  the `set_id` field when the model can infer grouping
  ("these three UCs all exercise auth provider failover" → propose a
  Set name).

## Alternatives considered

- **Build set-assignment into the bulk POST itself.** Rejected:
  forces server to know about Sets at UC creation time, couples two
  domains that the user already keeps mentally separate, and a single
  failed Set-membership row would either roll back saved UCs (bad —
  loses work) or leave partial state (also bad).
- **Skip the wizard, ship only bulk-import.** Considered. The user
  explicitly asked for a UC creation UX rethink alongside bulk
  import, and the bulk-extract logic naturally reuses the same
  structured-fields model the wizard exposes — building them
  separately would mean writing the extraction prompt twice.
- **Wizard as default, no Advanced tab.** Rejected: power-user
  velocity matters; raw-YAML is the only path for UCs with
  non-standard structures (custom expected-outcome predicates etc.)
  that the wizard fields don't cover.

## References

- [ADR-001 — DAV is a Consumer-Agnostic Framework](001-dav-consumer-agnostic-framework.md)
- [ADR-007 — Per-Role Path Overrides + Corpus Projection Parity](007-per-role-paths-and-corpus-parity.md)
- M12 milestone in `docs/dav-roadmap.md`

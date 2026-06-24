# DAV — User Guide

_Living document. DAV changes often; update this guide when behavior changes (house rule).
For the engineering/design rationale see `review-console-design.md` and the per-feature design docs._

---

## 1. What DAV is

**DAV (Design Analysis & Validation)** evaluates whether an **architecture** supports a set of
**use cases**, and turns that evaluation into decisions: where the architecture has **gaps**, what
**capabilities** are demanded, and what to **build next**.

**The mental model:**

- **Use cases** are the moving target — the questions you ask of the architecture.
- The **architecture / spec** is what's evaluated (provided as the corpus + spec sources).
- An **ingestion** (a run) sends each use case through the engine, which produces a per-UC **analysis**:
  does the spec support this use case, what's missing, which capabilities it invokes.
- The console projects those analyses into two **roadmaps** — an **Architecture** view (the gap analysis)
  and an **Engineering** view (the capability/build roadmap) — plus a **Capability Catalog**, **Cap Map**,
  and **Assessments**.

**North star:** informative, actionable output that encourages the right results — signal over noise.
Every screen should lead with the *verdict/recommendation*; the evidence is available but not in the way.

---

## 2. Key concepts (glossary)

| Term | What it is |
|---|---|
| **Project** | The top-level container for a body of work (one engagement / system). All data is project-scoped. |
| **Customer** | An organization that requests use cases. A *first-class entity*, M:N with projects (one project can serve many customers; one customer can span projects). The **universal/internal** customer is a built-in sentinel for non-customer work. |
| **Use case (UC)** | A scenario you want the architecture to support. Has a lifecycle state (draft → ready → in review → approved → deprecated). |
| **Scoping Set** | A named subset of use cases — your working scope for evaluation and the roadmaps. |
| **Ingestion (run)** | One evaluation job: the engine analyzes the in-scope use cases against the spec. "Run" = the job; the result is cached per-UC. |
| **Capability** | A stable, reusable unit of function (a noun) that use cases invoke. Lives in the **Capability Catalog**. |
| **Disposition (R4)** | What you decide to *do* about a capability: **Reuse · Refurbish · Replace · Retire** (dual-labelled with Gartner **TIME**: Tolerate / Invest / Migrate / Eliminate). |
| **Subdomain** | DDD strategic classification of a capability: **Core / Supporting / Generic** (aims investment). |
| **Assessment** | An external maturity/finding input (e.g. a DCM assessment) ingested into capability findings. |
| **Persona** | The lens you work in (Architect, Engineer, Customer, Stakeholder, Assessor, Operator). The objectives are constant; the persona selects which domains + projections you see. |
| **View mode** | A read-only browsing posture — hides every edit action and blocks mutations. |
| **Role / privilege** | RBAC: a role bundles privileges; you're granted roles per project or per customer. |
| **Seal (exclusive)** | A project/customer marked "exclusive" requires an *explicit* grant for everyone — even platform admins. |
| **Freshness** | Whether a UC's cached analysis is current (the UC or its code changed since the last eval). |

---

## 3. Getting around the interface

### The masthead (top bar)
Left to right, the persistent context:

- **Project** — the active project. Switch it to change everything below; ★ marks/sets your default.
- **Customer** — the active customer axis (defaults to *All customers*). Filters customer-attributed surfaces.
- **Scope** — the active Scoping Set (or *All use cases*). Everything the roadmaps/Cap Map show is scoped here.
- **Ingestion / Run status** — read-only status of the current/last run.
- **Freshness chip** — analysis coverage + staleness for the active project (hover for the breakdown;
  it counts the *active* use cases — deprecated ones are excluded). One-click "ingest what's stale" lives here.
- **Persona** — the lens (see below). **View-mode** toggle (Editing ⇄ View only). **Account menu** (theme,
  appearance, "Continue session across devices", sign out).

### Left rail = **domains**, top strip = **sub-views**
The left rail lists the **domains** for your active persona; selecting one shows its sub-views as a tab
strip across the top, and the detail fills the rest. Typical domains:

- **Authoring** — Use Cases · Inbox
- **Ingestion / Execution** — Runs · Results · Cap Map
- **Roadmaps** — Architecture · Engineering
- **Catalog** — the Capability Catalog
- **Assessments**
- **Prompts & Improvement**
- **Customers & Projects**
- **Config** (admins)

### Personas
The **persona** selects which domains the rail foregrounds — the objectives don't change, only the lens.
Default is role-derived (e.g. assessment-only users land on **Assessor**, others on **Architect**). Switch
it any time from the masthead. Personas + theme follow you across devices if **session sync** is on.

### View mode
Toggle **View only** to browse read-only — every edit/create/delete affordance is hidden, and the app
refuses mutating requests. Switch back to **Editing** to make changes. (A view-only *role* behaves the same,
and the server enforces it regardless.)

---

## 4. Core workflows

### 4.1 Author use cases
1. **Authoring → Use Cases**. The list shows your active use cases (deprecated are hidden by default —
   choose **all** or **deprecated** in the state filter to see them).
2. **+ New** to author one, **Bulk** to extract several from pasted notes/transcript, or **🤖 assist** to
   draft with the UC-authoring model. Use the **search/filter** row (assignment, source, state, health, priority).
3. Each UC has a **lifecycle**: click its status badge (▾) to transition (Submit for review → Approve →
   Deprecate → Reactivate). Status changes are also available in the detail pane.
4. Tag UCs into **Scoping Sets** (select rows → **★ Add to Set**, or the per-UC set membership).
5. **Customer demand:** the 👥 badge shows distinct customers who requested a UC (importance = *distinct
   customers*, which resists poisoning by repeated identical requests). Hover for the customer names.
6. **Identity is server-owned:** you don't enter a UUID — DAV assigns one on save, and fills in a missing
   `handle` (derived from the title) so a draft that omits it still saves. The dimension values and
   scenario fields must be valid; an invalid UC is rejected with the reasons (fix and re-save, or use
   **⚕ Repair** for mechanical fixes).
7. **Use a UC in more than one project (same tenant):** a managed UC is a tenant asset that can be
   *referenced* into other projects. In the Use Cases toolbar set **project scope → available to apply**
   to see UCs homed in your tenant's other projects, then **+ Apply** to reference one into the current
   project (referenced rows show **↪ ref**; **Remove** un-references — the UC itself is untouched). Corpus
   UCs aren't applied this way — they come from the project's corpus repos automatically.
8. **Deleting a UC or set** shows a **propagation warning** first (what it touches — memberships, project
   references, customer demand, past analyses) and is **audited**. Past analysis results are kept as a
   historical record by default; for a full sovereignty erasure, confirm the second prompt to also erase
   them. Deleting a **set** keeps the use cases — it only removes the grouping (past runs keep the set
   name they recorded).

### 4.2 Pick your scope
Use the masthead **Scope** to select a Scoping Set (or All use cases). Everything downstream — Results,
roadmaps, Cap Map — evaluates that scope, reading the **latest analysis per UC** (so a set's results may
span multiple runs). The masthead **pill tells the complete story** for the project: total use cases
available to ingest = **managed** UCs + **corpus** UCs (pulled from the project's corpus repos), whatever
their current ingest status. The popover breaks it down; fine-grained control lives here in Use Cases /
Scoping Sets, not the pill.

### 4.3 Run an ingestion (evaluate)
1. **Ingestion → Runs → + New Ingestion** (or the freshness chip's "ingest stale").
2. Pick the scope (UC / Set), model, and options; launch. The run status shows in the masthead; you can
   leave — completion is tab-resilient.
3. Results land in the per-UC cache. **Stop** a run from the Runs list if needed.

### 4.4 Read results & roadmaps
The **Roadmaps** domain has four sub-tabs: **Arch Review · Enhancement / PR · Cap Map · Roadmap**.
- **Ingestion → Results** — per-UC analyses for the active scope.
- **Roadmaps → Arch Review** — the **gap analysis** (what the architecture is missing for these UCs),
  scoped by the masthead Scope. A prose assessment only; turn the gaps into patches in the next tab.
- **Roadmaps → Enhancement / PR** — the two-step home for **gaps → patches → pull requests**:
  - **Step 1 · Enhancement Plan** — pick the Enhancement model and **▶ Generate Enhancement Plan** (the
    concrete, ready-to-apply spec edits, one per gap, for the active scope). When it's ready, **Route into
    PRs ↓** hands it straight to Step 2.
  - **Step 2 · Route → Pull Requests** — the **workbench**. (Or **↻ Load latest plan** to pull the cached
    plan, then **Route into PRs →**.) Each finding routes to its target repo (by the `target:` namespace),
    grouped into **one PR per repo**. Select findings **per finding, per PR group, or in bulk** (Select all
    matched), expand any finding to **view its patch + acceptance**, and **retarget** an unmatched namespace
    to an enhancement-target repo inline. **Submit selected → create PRs** opens one PR per repo (gated by a
    confirm + the `project.enhance-pr` privilege). The plan-source textarea is editable before routing.
- **Roadmaps → Cap Map** — the bidirectional UC ↔ capability matrix. Auto-loads the scope.
  Column ★ = foundational; a thin colored **underline** on each capability column = its R4 disposition.
- **Roadmaps → Roadmap** — the **capability/build roadmap**: which capabilities the UCs demand, ranked
  by demand, with **Core/Supporting/Generic** + **disposition** badges and a **👥 distinct-customers**
  funding signal. It auto-loads for the current scope and refreshes when you change scope.

### 4.5 Curate the Capability Catalog
**Catalog** is the canonical capability list (the engineering roadmap reads this, not raw model strings).

- **List view** — add/edit capabilities. Confirm **suggestions from analysis** (capabilities the model
  named) into the catalog with **+ Add** or **✨ draft** (LLM names + describes it).
- Per capability you can set: **subdomain** (Core/Supporting/Generic), **disposition** (with `fit × tech`
  drivers that *suggest* a disposition), and **ownership** (`bounded context` + `strategic provider` —
  "one capability, one source").
- **Board view** (List ⇄ Board toggle) — the **R4 disposition decision surface**: capabilities grouped into
  **Undecided · Reuse · Refurbish · Replace · Retire** columns. **Drag a capability chip** between columns to
  set its disposition; click a chip to edit it.

### 4.6 Assessments
**Assessments** ingests external maturity inputs (e.g. DCM assessments) and maps findings to capabilities,
so the engine + roadmaps consume them. Use the model-based ingest (paste text → an extractor model emits
structured findings) or the structured import.

### 4.7 Manage prompts (and A/B-test the evaluation prompt)
**Prompts & Improvement** lets you tune the prompt for each model role — the prompt names mirror the model
selectors 1:1: **Evaluation · Architecture Review · Enhancement · UC Authoring · Assessment Ingestion**.

- Pick a stage, edit its **additional context** (and section overrides where available), preview the
  assembled prompt, **Save**. Console stages apply live; the **Evaluation** (engine) prompt is **held** by default.
- **A/B the Evaluation prompt** (the safe path to change eval behavior):
  1. Edit the **Evaluation** prompt and Save.
  2. **Improve → New A/B → "evaluation prompt"**, pick an eval set, **Launch** — it runs a candidate arm
     (your prompt) vs a baseline (production) and scores them with the semantic comparator + success gate.
  3. If it wins, flip **"Apply to live runs"** on the Evaluation prompt — now normal runs inject it.
     (Until you promote, normal runs are byte-identical — the prompt only affects the A/B candidate.)

### 4.8 Customers & Projects
The **Customers & Projects** domain has three views:

- **List** — manage customers (create, exclusivity/seal, members) and their project associations.
- **⊞ Associations** — the customer × project grid; click a cell to link/unlink a customer to a project.
- **🔑 Access** (admins) — the **access grant matrix**: accounts × projects (or customers) → role.
  Toggle the **Projects | Customers** axis; click **＋** to grant a role, **✕** to revoke. 🔒 marks sealed scopes.

---

## 5. Administration (Config → Users & roles)

### Accounts
Create internal accounts (with a password or an emailed invite), enable/disable, and delete. Each account
shows its roles and any **alias identities**.

### Roles & privileges
Built-in roles: **platform-admin**, **project-admin / project-edit / project-viewer**, **customer-edit /
customer-viewer**. The **Roles** matrix shows what each role grants; you can retune privileges or add custom roles.

### Granting access
- **Per project / customer:** add members from the project or customer detail pane using the **type-ahead
  member picker** (searches users, shows only non-members).
- **Grant matrix:** the bird's-eye subject × scope → role grid (Config → Users & roles → Role bindings →
  Matrix, *or* Customers & Projects → 🔑 Access).
- **Escalation guard:** you can only grant a role whose privileges you already hold (platform admins excepted).
- **Self-service:** every authenticated user can **create a project** (soft name-dedup prevents near-duplicates);
  the creator becomes its **project-admin**.

### Seals (exclusive projects/customers)
Mark a project or customer **exclusive** to require an *explicit* grant for everyone — **platform admins
included**. Sealed scopes show 🔒. The creator's auto-grant keeps them in; a platform admin can always
*manage* a sealed scope (to grant access — break-glass) even with no data access.

### Identity unification (one human = one account)
The canonical account key is **email**. If the same person shows up under another identity (an oauth-proxy
uid, an old login, a second email), **🔗 link** that alias into their account in the accounts list. With
*migrate*, their existing roles + settings move onto the canonical account and the duplicate is removed.
After linking, any auth path resolves to the one account.

### Model endpoints
**Config → Models** — register model endpoints (probe to list available models), set per-project **defaults**
per role (Evaluation, UC Authoring, Architecture Review, Enhancement, Assessment Ingestion), and per-use
sampling profiles.

### Personal settings
The **account menu**: theme + appearance, and **"Continue session across devices"** — when on, your chrome
(theme/persona/view-mode) *and* working context (project/scope/customer) sync to the server, so a fresh
device resumes where you left off.

---

## 6. Reference

### Use-case lifecycle
`draft → ready → in_review → approved → deprecated`; `deprecated → draft` (Reactivate). Deprecated UCs are
hidden from the working list by default and excluded from analysis/freshness.

### Dispositions (R4 ↔ Gartner TIME)
| R4 | TIME | Meaning |
|---|---|---|
| Reuse | Tolerate | Strategic & tech-aligned → adopt as the reusable component |
| Refurbish | Invest | Strategic & viable → modernize & keep |
| Replace | Migrate | Strategic but too constrained → rebuild |
| Retire | Eliminate | Not strategic, no growth → sunset |

Drivers (suggest a disposition): **business/strategic fit** (high/low) × **technology fitness** (aligned/constrained).

### Subdomains (DDD)
**Core** (differentiating — best people, build) · **Supporting** (necessary, project-specific) ·
**Generic** (commodity — buy).

### Personas
Architect · Engineer · Customer · Stakeholder · Assessor · Operator. Switchable; default tied to your role;
orthogonal to view-mode (persona = which projection you consume; view-mode = edit vs read-only).

### Where things live (quick map)
- **Author / triage UCs** → Authoring
- **Evaluate** → Ingestion (Runs)
- **Gap analysis** → Roadmaps → Architecture
- **Build roadmap** → Roadmaps → Engineering
- **Capabilities + dispositions** → Catalog (List / Board) and Cap Map
- **External findings** → Assessments
- **Tune prompts / A/B** → Prompts & Improvement
- **Customers, associations, access** → Customers & Projects
- **Accounts, roles, models, integrations** → Config

---

_Questions this guide can't answer yet? It's a living doc — note the gap and extend it._

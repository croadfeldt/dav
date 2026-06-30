# DCM / UDLM 6-Week Demo — Execution Hand-off

> **Purpose:** a self-contained pickup so a fresh session (incl. a different account) can take over and
> *execute* the 6-week demo program. Assume **no shared memory** with the planning session — everything you
> need is here or linked from here.
> **Drafted:** 2026-06-26 · **Private** (this dir is gitignored; do not push internal docs).
> **Authoritative plan:** [`dcm-udlm-6-week-demo-roadmap.md`](./dcm-udlm-6-week-demo-roadmap.md) (read it first).

---

## 1. Mission in one paragraph

Build a **skeleton working demo** — thin but genuinely end-to-end — of **two co-equal acts** on a 3-tier
application, from bare metal up. **Act I (easy consumption):** an architect's pre-approved **likeC4
architectural pattern** provisions the initial resources — bare-metal node → OpenShift cluster → VM →
3-tier app (web/app/db). **Act II (the headline):** kill it, and the system **dynamically derives** what to
rebuild and in what order — by re-running placement (CEL+DAG) + policy (OPA/Rego) + provider-resolution over
the **stored data + dependency graph**, *not* a recorded script — and **shows that derived plan on screen
before executing it**. The whole thing serves two adoption north-stars: **easy consumption** (one pattern →
governed estate) and **easy provider integration** (adding a provider is cheap, contract-driven). It will be
**handed to select customers for co-engineering** and **demoed live at a community gathering**. The
deliverable is **software enablement, not a lab buildout** — it must run on plain libvirt VMs, packaged as
software profiles a customer can re-stand-up in their own homelab.

**Why 6 weeks is credible:** most of this already exists (see §4). A sovereignty-rehydrate demo already ran
on real software in **May 2026**. The job is *assemble + wrap + package*, not invent.

---

## 2. Decisions already made (do NOT re-litigate)

From Chris, 2026-06-24 → 06-26:

1. **Doc home:** keep the roadmap + this hand-off as **private drafts** in `dav/docs/internal/` (gitignored).
   Promote to `dcm/docs/roadmap/` only when Chris says so.
2. **Software enablement, not a lab buildout.** *"Dedicated lab specs are not important, only software
   enablement is."* Target software that drives the full arc on **any libvirt/KVM VM substrate**. No named-lab
   dependency. "Profiles" = software config (enabled providers / resource types / patterns / VM boot config).
3. **Bare-metal boot = dcm-bootstrap's PXE + DNS-SRV phone-home** (a *pull* model, **no BMC** for discovery;
   nodes matched on DMI serial). Runs on libvirt VMs directly. Virtual BMC only if the (private) install half
   turns out to need Redfish — fallback is PXE-next-boot or a `virsh`/sushy-emulator stand-in.
4. **Author bare-metal provisioning UCs** in DAV project 20 (wk1–2) so that leg is acceptance-backed.
5. **The headline is dynamic derivation, not replay.** The demo must show the system *computing* the rebuild
   plan, and surface it before executing.

**Still open — #5 in the roadmap (the only thing blocking nothing yet):** *if* week-1 verification finds the
`:rehydrate` trigger missing or doing a *static* replay, are we authorized to build the minimal path that
**invokes the existing planner over stored state and renders the derived plan**? Chris's lean + the
recommendation is **yes, build it** (it's wiring a trigger to existing placement/policy/provider logic, not
building a planner). Confirm with Chris once wk1 tells us which world we're in.

---

## 3. Where everything lives

### Local repos (on this host)
| Repo | Path | Branch of interest | Role |
|---|---|---|---|
| UDLM | `/Users/chris/git/udlm` | `feat/resource-type-registry` (`main` is empty) | Spec/model: 4-state lifecycle, composite-service model, likeC4 mapping, registry |
| DCM | `/Users/chris/git/dcm` | — | Design docs (control-plane components, capabilities matrix, requirements) |
| libvirt provider | `/Users/chris/git/dcm-provider-libvirt` | — | Ansible-native libvirt VM provider, Phase A done — the **WS-H** second-provider candidate |
| DAV | `/Users/chris/git/dav` | — | Review console; holds the DCM UC corpus + the whitepapers + these docs |

### External repos (the runtime lives upstream in `dcm-project`)
- **`github.com/dcm-project/control-plane`** — the **runnable** Go monolith (catalog→placement→policy(OPA)→SP,
  NATS). `make run` (SQLite, zero deps) / `make run-dev` (Postgres+NATS) / `make compose-up` (full + UI). API :8080.
- **`github.com/dcm-project/three-tier-app-demo-service-provider`** — PetClinic web/app/db; OpenShift backend
  default. Act I app tier.
- **`github.com/dcm-project/kubevirt-service-provider`** — VM tier.
- **`github.com/dcm-project/acm-cluster-service-provider`** — cluster tier.
- **`github.com/dcm-project/enhancements`** → `rehydration-flow/rehydration-flow.md` — the Act II trigger
  design (`:rehydrate`, ID-separation, dependency-order replay, policy re-eval). **Verify against the code.**
- **`github.com/heatmiser/dcm-bootstrap`** — bootc day-0 appliance; PXE/phone-home boot + ABI install via
  `rhvp.ocp_landing_zone` (that collection is **private/404** — its power model is the one unknown).
- **`likec4.dev`** / `github.com/likec4/likec4` — `likec4 export json`, `@likec4/core` Model API. Pin a version.
- Chris's mirrors: `croadfeldt/{udlm,dcm,dav}`.

### Key artifacts inside DAV
- **Whitepapers / strategy** (gitignored): `dav/docs/internal/dcm-udlm-{whitepaper,operations-whitepaper,
  thesis,executive-brief,whitepaper-sources}.md`. The operations whitepaper §7 has the honest maturity
  statement; §2–5 define the day-2 operational model you'll mine for the operational-characteristics doc.
- **The DCM use-case corpus** lives in the DAV database, project **20** (see §5 for API access).

---

## 4. What's already real vs what you build (the honest inventory)

**Real and runnable today:** the control-plane monolith (full catalog→placement→policy→SP flow, NATS),
the three-tier/kubevirt/acm-cluster providers, dcm-bootstrap (PXE boot + ABI), the UDLM composite-service +
likeC4-mapping *design*, the DAV corpus. The placement/policy/provider **reasoning machinery exists** — Act
II's "dynamic derivation" reuses it.

**You build (the gaps):**
- The **likeC4-JSON → DCM-CatalogItem mapper** (no built-in mapper exists).
- The **Act II trigger** wiring *if* `:rehydrate` is spec-only/static (verify wk1) + **the plan-surfacing UI/output**.
- **Minimum-viable UDLM registry types** the demo touches: BareMetalInstance/HostType, VirtualNetwork/Subnet,
  Storage, App/Tier (registry is ~7% populated).
- **dnsmasq dhcp-boot/HTTP-boot directives + a rebuilt discovery ISO** with our controller config (the public
  dcm-bootstrap template ships only `dhcp-range`).
- **WS-H:** wrap `dcm-provider-libvirt` as a DCM provider + an "add a provider in N steps" guide.
- **Bare-metal UCs** in DAV (corpus's thinnest leg — only `greenfield` + stubs today).
- **Software profiles + operational-characteristics doc** (WS-F).
- **WS-I: UDLM conformance in the control plane.** The control plane currently uses UDLM concepts
  (four states, providers, policies) but is NOT a UDLM-conformant realization — the data model was
  fit-for-purpose for the Summit demo, not spec-compliant. You must refactor the persistence and entity
  model so that the demo runs on **genuine UDLM data** (four-state stores per `foundations/four-states.md`,
  entities with stable UUIDs and provenance per `foundations/entity-types.md`, provider contract alignment
  per `contracts/provider-contract.md`). **Wk1 task: audit the current data model against the UDLM spec
  and produce a gap inventory.** This determines the scope for wks 2–4. The minimum is the four-state
  stores + entity model — the parts the demo visibly surfaces.

Full building-block table: roadmap §3. Workstreams A–I: roadmap §4.

Full building-block table: roadmap §3. Workstreams A–H: roadmap §4.

---

## 5. Access & environment

### DAV API (to read the corpus + author the bare-metal UCs)
- Base URL: `https://10.0.90.22:8843` (LAN load-balancer, self-signed cert → `curl -sk`).
- Auth: `Authorization: Bearer $(cat /Users/chris/.claude-personal/.dav-token)` — **never echo the token
  value** in output/logs/commits. The file is `0600`. Identity = `claude-personal@roadfeldt.com`
  (platform-admin), so it can write UCs in any project.
- Sanity check: `GET /api/me` → `authenticated:true`.
- DCM project scope: header **`X-DAV-Project: 20`**. List UCs: `GET /api/use-cases`. Read full (with
  `yaml_content` + parsed `scenario`/`dimensions`): `GET /api/use-cases/{uuid}`.
- Corpus shape: 129 UCs in project 20; 6 carry a rehydration `lifecycle_phase`; keystone =
  `cross-domain/full-dc-rehydration` (`uc-126b4231c0f8`). Acceptance criteria already distilled into roadmap §6.

### Building / running the demo software
- Start on a laptop: clone `dcm-project/control-plane`, `make compose-up` (or `make run` for SQLite),
  add `three-tier-app-demo-service-provider`, provision a 3-tier intent. This is the week-1 vertical slice.
- VM substrate: libvirt/KVM. dcm-bootstrap boots VMs via UEFI PXE/HTTP — no virtual BMC needed for discovery.

### Privileged homelab automation (only if a step needs it — most of this is dev, not ops)
- Privileged Ansible runs via the hop `ssh stark "ssh ansible@jinx '<cmd>'"` (gives ansible-user identity:
  vault-readable `/home/ansible/.vault_pass` (0600), passwordless sudo, ansible-owned repos). IPv4 only —
  never use IPv6 literals in oc/ssh/curl.

---

## 6. Week 1 — concrete first moves (do these in order)

The plan is demo-anchored; week 1 is about a thin vertical slice + de-risking the headline. From roadmap §5:

1. **★ Verify the Act II trigger (single most important task).** Clone `dcm-project/control-plane`; read
   `enhancements/rehydration-flow/rehydration-flow.md`, then the monolith code + integration tests. Determine:
   does `POST …:rehydrate` **re-run placement + policy + provider-resolution over stored state** (derives a
   plan), is it a **static replay**, or is it **spec-only**? Report which world we're in → unblocks decision #5.
2. **★★ Audit UDLM conformance gap (second most important task).** Read the control plane's Go structs and DB
   schema. Map them against UDLM's four-state stores (`foundations/four-states.md`), entity model
   (`foundations/entity-types.md`), provider contract (`contracts/provider-contract.md`), and policy contract
   (`contracts/policy-contract.md`). Produce a gap inventory: what's aligned, what needs refactoring, what's
   missing. This determines WS-I's scope for weeks 2–4 and is critical because **every piece of data the demo
   shows on screen must be genuine UDLM data, not a Summit-demo approximation.**
3. **Vertical slice on a laptop.** `make compose-up` + the three-tier provider on Kind/Podman → an app-tier
   intent provisions web/app/db (Act I in miniature). Prove rebuild-from-stored-state at the app tier.
4. **likeC4 pipeline spike.** Author the "Sovereign 3-Tier" likeC4 model; prove `likec4 export json`; sketch
   the JSON→CatalogItem mapper. Pin a likeC4 version.
5. **UDLM registry gap inventory.** List exactly which resource types the demo touches that are missing; pick
   the minimum-viable set to add.
6. **Author the bare-metal UCs** in DAV project 20 (per decision #4): intent-driven PXE provisioning of a node
   (physical or VM) discovered by serial — matching dcm-bootstrap's actual flow.
7. **WS-H scoping.** Read the provider contract (`contracts/provider-contract.md`, `dcm-providers.json`);
   scope wrapping `dcm-provider-libvirt` as a DCM provider.

**Week-1 milestone:** app-tier provision + rebuild runs on a laptop; the Act II trigger's maturity is known
(and #5 decided with Chris); **UDLM conformance gap inventory produced**; bare-metal UCs drafted;
mapper + registry gaps scoped.

Weeks 2–6 and their milestones: roadmap §5. Acceptance gates + the two north-star measures: roadmap §6.
Risks + decided cut-lines: roadmap §8. **Read §8 before you start cutting scope** — the fallbacks are
pre-decided (protect the vertical slice over breadth).

---

## 7. Working constraints (carry these)

- **Design-doc discipline:** the roadmap is the living plan — build to it and **update it as reality lands**
  (mark milestones done, record decisions, adjust cut-lines). Keep `dav/docs/internal/` edits **local**;
  it's gitignored (Chris re-exports). Don't push internal docs.
- **PR sizing:** ≤2–3k lines, one complete logical thing; subject-scoped; plan first. Split if larger.
- **Commits:** `--no-gpg-sign`; author `Chris Roadfeldt <chris@roadfeldt.com>`; end commit messages with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. End PR bodies with the Claude Code generated-with line.
- **Standards posture:** adopt wide standards by reference where they cleanly fit (UDLM's whole thesis);
  reuse existing data/sources before building new.
- **OSS / Red Hat orientation:** tools must be genuinely open source; prefer Red Hat-oriented.
- **Don't deploy or commit anything customer-facing without confirmation.** This is a demo program with a live
  customer event at the end — outward-facing artifacts get Chris's eyes first.
- **Security:** never echo the DAV token; IPv4 only; the install-half power model is unverified (private repo) —
  don't assume Redfish.

---

## 8. Tracking

- Program task (personal account): **#213 — "DCM/UDLM 6-week rehydration demo program (roadmap drafted)."**
  A work-account session should open its **own** tracking task referencing this hand-off + the roadmap.
- Related backlog: **#198** (vendor & custom provider extension model) is the home for WS-H's "add a provider"
  work beyond the demo.

---

## 9. The 60-second orientation for whoever picks this up

1. Read `dcm-udlm-6-week-demo-roadmap.md` end-to-end (it's the plan; this hand-off is the on-ramp).
2. Skim the operations whitepaper §7 (honest maturity) and §2–5 (operational model).
3. Do Week-1 task #1 (verify the Act II trigger) — it's the one thing that changes the shape of the work.
4. Report back to Chris: which world `:rehydrate` is in (→ decision #5), and confirm the wk1 vertical slice runs.
5. Then proceed through roadmap §5 week by week, updating the roadmap as you go.

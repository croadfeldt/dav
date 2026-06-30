# Truist — Kranthi Network Automation Session (2026-06-15)

## Summary

Working session between Red Hat consulting (Fernaz, Chris Roadfeldt, Adam, Lewis, Haron/Herone) and Truist network automation team (Kranthi) to refine the consulting engagement scope for network automation. This is a continuation of prior workshop sessions. Kranthi's team is ~2 months into a greenfield network automation effort with no existing catalog or workflows — ideal for building from scratch.

## Key Decisions

- **Removed CMS sections** from scope (per Kranthi's prior feedback)
- **Drift detection** folded into Phase 3 extended use cases (not a separate Phase 4 item)
- **AI/MCP intake** kept as Phase 4 but acknowledged as dependent on bank-wide AI platform availability
- **Separate ServiceNow/AAP integration thread** (Brian Dungan's call) acknowledged but kept separate from this engagement
- **Dashboard / observability** noted as future discussion, not in current scope

## Phases

### Phase 1-2 (already scoped)
Assessment, test pilot, IPAM integration, intake form basics.

### Phase 3 — Extended Automation Use Cases (prioritized)
1. **Automated branch/campus provisioning** — ship switch + router to new branch, technician follows N steps, AAP provisions via intake form → ServiceNow ticket → approval → execute → notify cabling team
2. **Hardware refresh automation** — similar to provisioning but for replacement devices (partially reusable playbooks)
3. **Network device onboarding** — phones, endpoints connecting to switches, validation
4. **Configuration backups** — ~5000 devices, controller-based infrastructure (Catalyst Center / DNA Center). Question: can AAP pull configs from the controller as a single source vs individual devices? Lewis: "about the same level of effort" — one API call vs many, but functionally equivalent
5. **DHCP configuration** — part of new branch provisioning
6. **Compliance scanning** — daily automated scans, currently done via OpenText tool, detect violations, send alerts. Cyber team defines rules. Not remediation today — just detect + email.
7. **Config drift detection + remediation** — compare yesterday's running config to today's, flag undocumented deltas, auto-remediate known-safe deviations, create incidents for unknowns
8. **Network health checks** — continuous or scheduled diagnostics, tied to EDA for incident triggers
9. **EDA integration** — event-driven triggers for incidents, ultimately zero-touch provisioning where EDA replaces manual form entry

### Phase 4 — AI/MCP Intake
- Replace intake forms with AI agent using MCP for natural language workflow generation
- Agent does conversational intake, validates inputs in real-time (e.g., "VLAN 99 not available, use 150 for prod or 151 for dev")
- Dependent on bank-wide AI platform availability (not funded/available yet)
- Chris emphasized: build as reusable Lego bricks — same MCP intake pattern should work for storage, compute, not just networking
- Lewis confirmed Brian Dungan's group is starting MCP conversations, ~6 months out

## Engagement Model

- **Truist resources:** ~2 FTEs max dedicated to co-engineering (varies by phase, may rotate)
- **Red Hat approach:** Co-engineer, not hand-off. Build framework + enable Truist team to continue independently after consulting engagement ends
- **Timeline:** Likely September-November kickoff if funded. Could be end of year / early 2027 for actual start
- **Kranthi availability:** Traveling internationally June 24 – mid-July. Available June 26-July 10 (until 4pm EST). Back in August
- **Next step:** Red Hat to deliver refined scope + cost estimate before June 23

## DAV-Relevant Takeaways

- **Greenfield network automation** — no existing workflows or catalog. Ideal for DAV to evaluate the proposed architecture against DCM patterns
- **Config-as-a-service model** — Chris explicitly pitched centralizing network config behind a service model with atomic reusable units. Direct DCM alignment
- **Config drift = DCM discovered vs realized** — the drift detection pattern maps exactly to DCM's four-store model
- **MCP intake = DCM consumer ingress** — the AI agent intake is the natural-language equivalent of DCM's consumer API
- **Cross-team reuse** — Chris and Lewis both emphasized building once for networking, reusing for storage/compute. This is the atom/molecule pattern from the Barclays meetings
- **Controller as system of record** — Kranthi wants Catalyst Center to be authoritative but acknowledges out-of-band changes happen. This is the intent vs discovered state problem DCM solves

## Use Cases to Add to DAV

1. Zero-touch branch provisioning (ship hardware → plug in → auto-provision via AAP)
2. Network hardware refresh automation
3. Controller-based config backup at scale (~5000 devices)
4. Network compliance scanning with policy-defined rules
5. Network config drift detection and auto-remediation
6. AI/MCP conversational intake for network workflows

# US Bank — Project LightWell Briefing (2026-06-08)

## Summary

30-minute briefing between Red Hat CTO Matt (Americas CTO, engineering org under Chris Wright), Kevin (chief architect), Chris Roadfeldt (FlightPath), Stephanie (new AE for US Bank), Subi (technical lead), and Ed Calusinski (US Bank — SVP/VP level, owns AI platform, data platform, engineering, pipelines/DevSecRegOps/CICD, FinOps, architecture). Ed joined to understand Project LightWell after reading the announcement, given his background in open source supply chain governance at US Bank.

## Ed Calusinski — Role & Context

- Owns: AI platform, data platform, engineering, pipelines (DevSecRegOps, CICD), FinOps, architecture
- US Bank is a member of **Project Mythos** (vulnerability scanning program)
- Working with OpenAI / ChatGPT 5.5 Cyber
- Currently participating in **Glass Wing** (ends end of June 2026)
- New CISO starts next week (announced 3 months ago)
- Previously mandated Artifactory for all open source package management — got ahead of supply chain attacks
- Background: IBM Fellow-track, 25+ years, knows Kevin from State Farm ~2000, knows Red Hat ecosystem well
- Views supply chain security as both an AppDev problem AND a cyber problem — he owns the pipeline/change/incident/imaging/packaging processes

## Project LightWell — What It Is

Red Hat initiative to harden open source **application libraries** (not Red Hat products like RHEL/OCP/Ansible — those are already hardened).

**Scope:** The dependencies enumerated in `pom.xml`, `requirements.txt`, `package.json` — the things pulled from Maven Central, PyPI, NPM.

**How it works:**
1. Red Hat ingests open source library source code
2. Builds hardened versions through Red Hat's SLSA Level 3 compliant build pipelines
3. Releases into Red Hat-hosted repositories (Java, Python, Node.js equivalents of Maven/PyPI/NPM)
4. SBOMs shipped, everything signed, trust chains verifiable
5. AI-accelerated vulnerability patching with human-in-the-loop for upstream contributions

**Key differentiator — version targeting:**
- Builds to the specific versions customers are actually using, not just latest
- Customers submit their artifact inventory + version numbers
- Red Hat builds hardened equivalents of those exact versions
- Minimizes disruption — same version, just patched

**Language priority:** Java first → Python → Node.js → then Go, Rust, C/C++, .NET, Ada, Perl

**Current status:**
- ~3 weeks into engineering
- 6 design partners (closed to new partners)
- Early Access program planned before GA (open to broader customers)
- No pricing model yet — hoping to have rough structure by end of week for ARC presentation (Friday)
- Matt presenting to the ARC (global Wall Street bank CISOs) on Friday

**What LightWell is NOT:**
- Not about Red Hat products (RHEL, OpenShift, Ansible, middleware)
- Not about container images Red Hat already releases
- Not scanning customer code (customers still scan their own code via Mythos/Glass Wing)

## Two expected tiers:
1. **Read-only access** — consume what Red Hat has already built
2. **Concierge** — submit inventory of artifacts + versions, Red Hat prioritizes and builds to order

## Ed's Key Concerns / Questions

1. **Maintainability of poorly-managed communities** — who makes architectural decisions when a vulnerability fix requires structural changes? Matt: abandoned projects are actually easier (just apply patches, no community sync needed); active communities require sync energy
2. **Performance/behavioral side effects** — fixing a vulnerability changes behavior by definition. Matt: version-targeting minimizes this; robust test suite is critical
3. **Spring libraries 8-10 years old** — Ed confirmed this is their #1 problem (same as other design partners). CISO says "just upgrade," app teams say "can't, functions changed" → stalemate
4. **Integration effort** — Ed sees it as relatively low: just add LightWell repo URL + credential to Artifactory proxy. Build process stays the same. Matt cautious: "I know better than to say it'll be smooth"

## Early Access Program Requirements
- Take what Red Hat builds
- Within a 2-week cycle: apply library to small number of apps, full rebuild, test, redeploy
- Bring feedback on build/deploy process friction
- 2 weeks is aggressive but needed to hit GA timeline

## Next Steps

- Matt to share updates as they come (not waiting for next sync)
- Re-sync in ~2 weeks with pricing model and early access details
- Ed to talk internally about early access participation
- Chris offered to help evaluate application architecture for optimization opportunities alongside library updates

## DAV Relevance

**Low-to-none for current DAV use cases.** This is a supply chain security / library hardening initiative, not infrastructure lifecycle or architecture review. However:

- The concept of "version-targeted hardening" has a parallel to DCM's versioned resource definitions — both deal with maintaining compatibility across versions while applying changes
- The build pipeline velocity discussion (6 weeks to 9 months → under 1 week → ideally 1 day) maps to the path-to-production use case already in DAV
- If DAV ever expands to evaluate software supply chain architecture, LightWell would be a spec source
- Ed's pipeline/DevSecRegOps ownership makes him a potential DAV stakeholder for CI/CD gate mode

**No new UCs to add from this transcript.**

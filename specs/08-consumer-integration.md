# DAV Specification 08 — Consumer Integration

**Status:** Stub (not yet authored)
**Audience:** Project maintainer integrating DAV for the first time
**Depends on:** `05-use-case-schema.md`, `06-prompt-contract.md`, `07-analysis-output-schema.md`, `09-deployment-standards.md`

## Purpose

This is the "how do I actually use DAV for my project" document. It walks a new consumer through integrating DAV end-to-end: repo structure, content authoring, deployment, CI integration, result review.

Topics this spec will cover when authored:

- Consumer prerequisites: spec corpus in markdown, familiarity with YAML
- Consumer repo layout: `<consumer>/dav/` subdirectory with subfolders for use-cases, prompts, calibration, stage-config, assertions (optional)
- Declaring DAV version compatibility: `dav-version.yaml`
- The minimum viable consumer: smallest set of files needed for a DAV run
- Authoring the first analytical UC: walkthrough with example
- Authoring the first assertion UC: walkthrough with Python example
- Supplying prompt content: walkthrough of slot-filling
- Deploying DAV pointed at your consumer content: ansible playbook invocation
- Running a single UC: CLI invocation examples (verification, reproduce, explore modes)
- Reading results: what the output YAML looks like, how to interpret `sample_annotations`, where to look first
- Reviewing results: Review Console workflow
- Wiring into CI: Tekton pipeline example; GitHub Actions example (future); GitLab CI example (future)
- Calibration: seeding calibration references, measuring predictable correctness over time
- Debugging: using reproduce mode to investigate unexpected verdicts; using explore mode to survey variance
- Migration from no-DAV state: suggested authoring order for the first batch of UCs
- The `examples/minimal-consumer/` companion directory: stripped-down synthetic consumer for tutorial/testing
- DCM as a worked example: pointer to DCM's `dcm/dav/` as a real-world reference

This is the most user-facing of the specs. Should be authored with real new-user feedback if possible.

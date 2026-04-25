# Contributing to DAV

DAV is open-source software released under Apache License 2.0. Contributions are welcome.

## Project state

DAV is in early development. The framework's foundational architecture is settled (see [`adr/001-dav-consumer-agnostic-framework.md`](adr/001-dav-consumer-agnostic-framework.md)) and the engine is in place; specifications, exemplars, and operational tooling continue to evolve. Both code and specification contributions are welcome.

## How to contribute

### Filing issues

Bug reports, design questions, and feature ideas all welcome as GitHub issues. For architectural questions that might warrant a decision record, flag them as `[ADR candidate]` in the title.

### Submitting changes

Fork, branch, PR against `main`. Each PR should:

- Reference the issue it addresses (if applicable)
- Include tests for new behavior
- Update relevant specifications if the change affects a consumer contract
- Update the ADR log if the change is architectural

### Spec changes

Changes to specifications in `specs/` are consumer-breaking by default. Spec changes should:

- Have a companion ADR describing the rationale
- Call out which consumer contracts are affected
- Include a migration note if the change is not backward-compatible

### Authoring ADRs

Architectural decision records go in `adr/` with the next available number. Follow the shape of the existing ADRs — context, decision, consequences, alternatives considered. One decision per ADR; don't bundle.

## Code of conduct

Be kind. Ask questions before assuming bad intent. Disagree via argument, not authority. The goal is to build something useful — treat each other accordingly.

## Licensing

By contributing to DAV, you agree that your contributions are licensed under Apache License 2.0, matching the project license.

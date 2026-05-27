# DAV Architectural Decision Records

Decisions that shape DAV's architecture. Each ADR is immutable once accepted (changes produce new ADRs that supersede old ones).

## Index

| Number | Title | Status |
|--------|-------|--------|
| 001 | [DAV is a Consumer-Agnostic Framework](001-dav-consumer-agnostic-framework.md) | Accepted |
| 002 | [DCM Integration Model: DAV as a DCM-Managed Capability](002-dcm-integration-model.md) | Proposed (forward-looking, deferred 6-12 months) |
| 003 | [Multi-Repo Registry and MCP Source-of-Truth](003-multi-repo-registry-and-mcp-source-of-truth.md) | Accepted |
| 004 | [Per-Repo Credentials in the Registry (Fernet-encrypted; Vault later)](004-per-repo-credentials-in-registry.md) | Accepted |
| 005 | [Shared Credentials Abstraction](005-shared-credentials-abstraction.md) | Accepted |
| 006 | [Consolidate code_repo_configs into managed_repos](006-consolidate-code-repos-into-managed-repos.md) | Accepted |
| 007 | [Per-Role Path Overrides + Corpus Projection Parity](007-per-role-paths-and-corpus-parity.md) | Accepted |

## Authoring

See `../CONTRIBUTING.md` for ADR authoring guidance. Short form: one decision per ADR, explicit context and alternatives, immutable once merged.

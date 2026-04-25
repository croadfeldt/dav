# TinyURL Use Cases

This is the DAV use-case corpus for TinyURL, the minimal DAV consumer example.

## Organization

Use cases are organized by functional domain:

- `authentication/` — login, session, registration flows
- `spec_integrity/` — assertions about the corpus itself (do all referenced docs exist, etc.)
- Future domains would include `url_lifecycle/`, `quota/`, `analytics/`, `admin_ops/`

## Controlled vocabulary — `domain`

TinyURL UCs use these `domain` values:

- `authentication` — login, logout, registration, session management
- `spec_integrity` — structural properties of the spec corpus itself (used by assertion UCs)
- `url_lifecycle` — creation, resolution, deletion of short URLs *(no UCs yet)*
- `quota` — quota enforcement and reconciliation *(no UCs yet)*
- `analytics` — access event recording and aggregation *(no UCs yet)*
- `admin_ops` — administrator-only operations *(no UCs yet)*

## Controlled vocabulary — `scope`

For a single-tenant product like TinyURL, `scope` is always `global` or omitted. If TinyURL ever adds multi-tenancy, scope values would expand.

## Controlled vocabulary — `provider_types_involved`

TinyURL does not have a formal provider model (the architecture is a monolith, not a framework). The `provider_types_involved` finding list will be empty for TinyURL UCs.

This is fine. Not every consumer has every finding type.

## Controlled vocabulary — `policy_modes_required`

TinyURL does not have a policy engine. The `policy_modes_required` finding list will be empty for TinyURL UCs.

## Conventions

- UC filenames: kebab-case matching the short handle, ending in `.yaml`
- UUID format: `tinyurl-uc-NNN` for analytical/hybrid, `tinyurl-assert-NNN` for assertion
- One UC per file

## Current UCs

| UUID | Type | Domain | Description |
|------|------|--------|-------------|
| `tinyurl-uc-001` | analytical | authentication | Login flow produces a valid session or clear failure response |
| `tinyurl-assert-001` | assertion | spec_integrity | All referenced spec doc handles in UCs resolve to real docs |
| `tinyurl-uc-002` | hybrid | authentication | Auth spec has all expected sections AND flow analysis is consistent |

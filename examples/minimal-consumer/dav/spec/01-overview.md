# 01 — TinyURL Overview

## Purpose

TinyURL is a URL-shortening service. Users submit long URLs; TinyURL returns short aliases. When a short alias is requested, TinyURL redirects to the original URL and records the access.

## Users

TinyURL has three user types:

- **Anonymous users** — can resolve short URLs (i.e., follow redirects). Cannot create new short URLs. Cannot access analytics.
- **Registered users** — have an account; can create short URLs up to a quota; can see analytics for their own URLs.
- **Administrators** — can see all URLs and analytics across the system; can suspend accounts.

## Core operations

1. **Shorten a URL** — registered user submits a long URL; system generates a unique short alias and stores the mapping.
2. **Resolve a URL** — anyone submits a short alias; system returns a redirect to the long URL.
3. **View analytics** — registered users see access counts and timestamps for their URLs.
4. **Manage quota** — system tracks per-user creation counts against their tier's quota.

## System boundaries

TinyURL does not:

- Proxy URL content (it redirects only)
- Perform content analysis on destination URLs
- Integrate with third-party identity providers (authentication is self-hosted; see Doc 03)
- Provide an API to administrators for programmatic management (admin is UI-only for v1.0)

## Related documents

- `02-data-model.md` — what data is stored
- `03-authentication.md` — how users log in
- `04-operations.md` — lifecycle operations and quota enforcement

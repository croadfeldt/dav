# Domain Terminology

- **Short URL / alias**: The abbreviated form of a URL, such as `tiny.example.com/abc123`. The alias is the eight-character random-base62 portion.
- **Destination**: The long URL a short URL redirects to.
- **Resolution**: The act of looking up a short URL's destination and issuing an HTTP redirect.
- **Access event**: A record of a single resolution, used for analytics.
- **Tier**: A user's quota class — `free`, `pro`, or `admin`. Determines maximum owned short-URL count.
- **Suspension**: An administrator-applied state that disables a user's access and their owned short URLs.

Avoid confusing "alias" (the short-URL identifier in the data model) with "handle" (general usage). Avoid confusing "resolution" (following a redirect) with "reconciliation" (quota counting).

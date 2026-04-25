# Consumer Overview

TinyURL is a URL-shortening service with three user types (anonymous, registered, administrator) and four core operations (shorten, resolve, analytics, admin).

The architecture is intentionally simple: a monolithic application backed by a relational database. There is no policy engine, no provider framework, and no multi-tenancy. This simplicity is intentional for the v1.0 scope.

Known v1.0 scope reductions include: no password reset flow, no third-party authentication, no cached quota counters, no scheduled maintenance, unbounded AccessEvent growth. These are deferred to v1.1.

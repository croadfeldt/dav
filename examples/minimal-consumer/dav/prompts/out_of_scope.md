# Out of Scope

Do not reason about:

- **Third-party identity providers** (SAML, OIDC, social login). TinyURL v1.0 is self-hosted auth only.
- **API for administrator operations**. Admin is UI-only in v1.0; APIs exist only for end-user and resolution flows.
- **Multi-tenancy**. TinyURL is single-tenant.
- **Cross-region replication**. Deployment is single-region.
- **Password reset**. Explicitly deferred to v1.1; see Doc 03 §3.4.

If a UC's scenario requires any of the above, flag it as a gap with severity appropriate to the scenario's criticality.

# Credentials

Secrets are never carried in intent bodies. An intent references a secret by
**credential reference** — a pointer of the form `credref://<scope>/<name>` that
Halyard resolves at realization time against the tenant's secret store.

An intent containing a literal secret value is refused at admission with
`CREDENTIAL_INLINE`. The `remediation` field names the `credref://` syntax.

## Handling of a rejected body

<!-- seeded hole: FIX-NONPERSIST-001 -->

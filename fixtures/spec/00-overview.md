# Halyard Control Plane — overview

Halyard accepts declarative **intents** from tenants and realizes them as workloads
on provider infrastructure. An intent names desired resources, their dependencies,
and a policy profile. Halyard validates the intent, selects a provider, and either
**realizes** it or **refuses** it.

This is a deliberately small synthetic platform. It exists to test the analysis
harness, not to model any real system. See `../MANIFEST.md` for what is
intentionally complete and what is intentionally missing.

## Request lifecycle

1. **Admission** — schema validation and tenancy checks
2. **Policy evaluation** — the profile's rules are applied
3. **Provider selection** — an eligible provider is chosen by declared capability
4. **Realization** — the provider materializes the resources
5. **Record** — the outcome is written to the request log

A request that fails at any stage before realization is **refused**. Refusal is a
normal, expected outcome — not an error condition of the platform.

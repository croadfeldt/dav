# Providers and capability matching

A provider declares the capabilities it offers as a set of named, versioned
capability identifiers. An intent declares the capabilities it requires.

## Selection  (SPECIFIED)

Provider selection is a total function of the declared sets: a provider is
**eligible** when its declared capability set is a superset of the intent's required
set. Among eligible providers, selection is by the profile's declared preference
order, then lexically by provider id — so selection is deterministic and
reproducible for a given intent, provider inventory and profile.

Where no provider is eligible, the intent is refused with `CAPABILITY_UNMET`, and
`remediation` names the unmet capability identifiers.

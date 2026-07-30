# Federation

A Halyard instance may register a peer instance and dispatch intents to it.

## On-behalf-of identity  (SPECIFIED)

Every dispatched intent carries the originating caller's identity, distinct from the
instance identity. The peer evaluates tenancy and policy against the ORIGINATING
caller. An intent arriving without an originating identity is refused with
`FEDERATION_ANONYMOUS`.

## Dispatch scope

<!-- seeded hole: FIX-FED-SCOPE-001 -->

Peers are registered with a display name and endpoint.

## Version compatibility

<!-- seeded hole: FIX-FED-COMPAT-001 -->

Instances exchange a protocol version string at registration.

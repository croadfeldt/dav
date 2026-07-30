# Credential rotation

Referenced credentials (30-credentials.md) may be rotated by their owner.

## Rotation operation  (SPECIFIED)

A credential's owner may issue `rotate`, which atomically replaces the secret
material behind the reference. The reference identifier is stable across rotation.

## Consumer refresh

<!-- seeded hole: FIX-ROTATE-001 -->

Resources realized against the reference hold the material current at realization.

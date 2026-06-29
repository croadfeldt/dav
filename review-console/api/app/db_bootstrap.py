"""Tenant-aware database bootstrap (tenancy Phase 2 — the schema-aware boot path).

Background
----------
Phase 2 physically siloes each tenant's *client* data into its own
``tenant_<slug>`` schema, while *control-plane* tables (identity / RBAC / tenancy /
shared vendor-IP defaults) stay in ``public``. The legacy boot path ran every
migration + ``schema.sql`` under ``search_path=public``; after the live data-move
that crashes (``ALTER TABLE use_case_sets`` can't find the table — it now lives in
``tenant_flightpath``) and, worse, ``CREATE TABLE IF NOT EXISTS`` would shadow the
other category's tables into the wrong schema (the dry-run finding in
docs/tenancy-phase2-runbook.md).

This module replaces that with a schema-aware, run-once bootstrap:

* control pass  → applies ``schema_control.sql`` in ``public`` (once, tracked).
* per-tenant    → for every non-archived tenant, ``CREATE SCHEMA IF NOT EXISTS``
  + applies ``schema_client.sql`` under ``search_path=<tenant>,public`` (once,
  tracked). Unqualified names resolve to the tenant schema; ``public.*`` cross-schema
  FKs resolve to the shared control plane.

Both base files are GENERATED from the live, validated schema by
``scripts/process_schema.py`` (regen via ``scripts/gen_base_schema.sh``) so they are
correct-by-construction — no hand-split of the 1100-line schema.sql.

Idempotency / adoption
----------------------
Applied (schema, version) pairs are recorded in ``public.schema_migrations``. A
schema that ALREADY has base tables (an existing install / the live
``tenant_flightpath``) is *adopted* — marked applied WITHOUT re-running the
(non-IF-NOT-EXISTS) base DDL. Only genuinely empty schemas get the base applied.
This makes the very first boot on the live DB a no-op on existing data and a
clean provision for any new (empty) tenant schema.

Seeds are routed by the caller: ``control_seeds(conn)`` runs once in ``public``;
``client_seeds(conn, schema)`` runs per tenant with the search_path already set.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger("dav.bootstrap")

_APP = Path(__file__).resolve().parent
CONTROL_BASE = _APP / "schema_control.sql"
CLIENT_BASE = _APP / "schema_client.sql"

# Bump these when a NEW base snapshot is generated (a fresh gen_base_schema run).
# Incremental post-snapshot changes ship as routed migration files (see _MIGRATIONS).
CONTROL_VERSION = "control-base@2026-06-23"
CLIENT_VERSION = "client-base@2026-06-23"

# Incremental routed migrations applied AFTER the base, per schema, tracked individually in
# public.schema_migrations. CONTROL_MIGRATIONS run once in `public`; CLIENT_MIGRATIONS run once per
# tenant schema (search_path=tenant_<x>,public). Each MUST be idempotent (CREATE TABLE IF NOT EXISTS /
# INSERT ... ON CONFLICT) since it can run against a base-adopted schema that already has the table.
# Ordered: append new migrations at the end; never reorder/edit a shipped one.
CONTROL_MIGRATIONS: list[tuple[str, Path]] = []
CLIENT_MIGRATIONS: list[tuple[str, Path]] = [
    ("t001-use-case-projects", _APP / "migrate_t001_use_case_projects.sql"),
    ("t002-refresh-fabricated-uuids", _APP / "migrate_t002_refresh_fabricated_uuids.sql"),
    ("t003-uc-spec-deps", _APP / "migrate_t003_uc_spec_deps.sql"),
]

_SLUG_BAD = re.compile(r"[^a-z0-9_]+")


def tenant_schema(slug: str) -> str:
    """Deterministic schema name for a tenant slug. ``flightpath`` -> ``tenant_flightpath``;
    ``acme-val`` -> ``tenant_acme_val`` (hyphens/punctuation -> underscore)."""
    s = _SLUG_BAD.sub("_", (slug or "").lower()).strip("_")
    if not s:
        raise ValueError(f"tenant slug {slug!r} yields an empty schema name")
    return f"tenant_{s}"


async def _ensure_tracking(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
          schema_name TEXT NOT NULL,
          version     TEXT NOT NULL,
          applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (schema_name, version)
        )"""
    )


async def _applied(conn, schema: str, version: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT true FROM public.schema_migrations WHERE schema_name=$1 AND version=$2",
            schema,
            version,
        )
    )


async def _mark(conn, schema: str, version: str) -> None:
    await conn.execute(
        "INSERT INTO public.schema_migrations(schema_name, version) VALUES($1,$2) "
        "ON CONFLICT DO NOTHING",
        schema,
        version,
    )


async def _has_base_tables(conn, schema: str) -> bool:
    n = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema=$1 AND table_type='BASE TABLE'",
        schema,
    )
    return (n or 0) > 0


async def _apply_base(conn, schema: str, version: str, base_path: Path, label: str) -> None:
    """Apply (or adopt) a base schema into ``schema`` exactly once. ``search_path`` must
    already be set so unqualified DDL lands in ``schema``."""
    if await _applied(conn, schema, version):
        return
    if await _has_base_tables(conn, schema):
        # Existing install already at HEAD (the live public / tenant_flightpath, or any
        # schema provisioned before tracking existed): adopt without re-running base DDL.
        log.info("bootstrap: adopting existing %s schema as %s (no DDL re-run)", schema, label)
    else:
        log.info("bootstrap: applying %s into empty schema %s", label, schema)
        await conn.execute(base_path.read_text())
    await _mark(conn, schema, version)


async def _apply_migrations(conn, schema: str, migrations: "list[tuple[str, Path]]") -> None:
    """Apply each not-yet-recorded incremental migration to ``schema`` (search_path already set),
    tracked once in public.schema_migrations. Idempotent migrations only."""
    for version, path in migrations:
        if await _applied(conn, schema, version):
            continue
        log.info("bootstrap: applying migration %s to %s", version, schema)
        await conn.execute(path.read_text())
        await _mark(conn, schema, version)


SeedFn = Callable[..., Awaitable[None]]


async def bootstrap(
    conn,
    *,
    control_seeds: Optional[SeedFn] = None,
    client_seeds: Optional[SeedFn] = None,
) -> None:
    """Run the full tenant-aware bootstrap on a single connection.

    control_seeds(conn): control-plane seeds (search_path=public).
    client_seeds(conn, schema): per-tenant seeds (search_path=<schema>,public, set by us).
    """
    await _ensure_tracking(conn)

    # ── control pass (public) ───────────────────────────────────────────────
    await conn.execute("SET search_path = public")
    await _apply_base(conn, "public", CONTROL_VERSION, CONTROL_BASE, "control-base")
    await _apply_migrations(conn, "public", CONTROL_MIGRATIONS)
    if control_seeds is not None:
        await control_seeds(conn)

    # ── per-tenant pass ─────────────────────────────────────────────────────
    tenants = await conn.fetch(
        "SELECT slug FROM public.tenants WHERE NOT archived ORDER BY id"
    )
    for row in tenants:
        schema = tenant_schema(row["slug"])
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await conn.execute(f'SET search_path = "{schema}", public')
        await _apply_base(conn, schema, CLIENT_VERSION, CLIENT_BASE, "client-base")
        await _apply_migrations(conn, schema, CLIENT_MIGRATIONS)
        if client_seeds is not None:
            await client_seeds(conn, schema)

    await conn.execute("SET search_path = public")


async def provision_tenant(conn, slug: str, *, client_seeds: Optional[SeedFn] = None) -> str:
    """Provision a brand-new tenant's schema on demand (call from the tenant-create path).
    Returns the schema name. Idempotent."""
    await _ensure_tracking(conn)
    schema = tenant_schema(slug)
    await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    await conn.execute(f'SET search_path = "{schema}", public')
    await _apply_base(conn, schema, CLIENT_VERSION, CLIENT_BASE, "client-base")
    await _apply_migrations(conn, schema, CLIENT_MIGRATIONS)
    if client_seeds is not None:
        await client_seeds(conn, schema)
    await conn.execute("SET search_path = public")
    return schema

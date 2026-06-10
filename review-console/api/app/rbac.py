"""RBAC resolver — accounts × roles × privileges.

Identity-source-agnostic: a `users` row (whatever the auth source) is matrixed to
roles in `rbac_account_roles`; roles are matrixed to privileges in
`rbac_role_privileges`. Authorization for a given (account, project) context is
the UNION of:
  - privileges from the account's *platform*-scoped roles (apply everywhere), and
  - privileges from the account's *project*-scoped roles whose project_id matches.

All functions take an asyncpg connection so callers control pooling/transactions.
"""
from __future__ import annotations

from typing import Optional

# Built-in privilege/role keys (mirrors the schema seed; handy for callers).
P_PLATFORM_ADMIN = "platform.admin"
P_PROJECT_CREATE = "project.create"
P_PROJECT_DELETE = "project.delete"
P_PROJECT_SETTINGS = "project.settings"
P_PROJECT_MEMBERS = "project.members"
P_PROJECT_READ = "project.data.read"
P_PROJECT_WRITE = "project.data.write"  # legacy umbrella (retired from built-in roles)
# Workflow / execution privileges (atomic; composed into roles).
P_PROJECT_USECASES = "project.usecases"
P_PROJECT_RUNS_MANAGE = "project.runs.manage"
P_PROJECT_RUNS_EXECUTE = "project.runs.execute"
P_PROJECT_ARCHREVIEW_EXECUTE = "project.archreview.execute"
P_PROJECT_ARCHREVIEW_CONTEXT = "project.archreview.context"
P_PROJECT_ENH_EXECUTE = "project.enhancement.execute"
P_PROJECT_ENH_PR = "project.enhancement.pr"
P_PROJECT_CATALOG = "project.catalog"
# Config-registry privileges (project-owned, strict isolation).
P_PROJECT_MODELS = "project.models"
P_PROJECT_INTEGRATIONS = "project.integrations"
P_PROJECT_REPOS = "project.repos"
P_PROMPT_MANAGE = "prompt.manage"  # F8: per-project prompt customization (all stages)
P_ASSESSMENT_VIEW = "assessment.view"   # F7: view assessments + findings
P_ASSESSMENT_EDIT = "assessment.edit"   # F7: ingest / edit assessments
P_BLUEPRINT_VIEW = "blueprint.view"     # blueprints (task #95) — inert until built
P_BLUEPRINT_EDIT = "blueprint.edit"     # blueprints (task #95) — inert until built
P_USECAT_MANAGE = "usecat.manage"       # scope & bundles (#107): manage platform / use-category-scoped config + bundles (cross-project; seeded to Platform Admin)

ROLE_PLATFORM_ADMIN = "platform-admin"
ROLE_PROJECT_ADMIN = "project-admin"
ROLE_PROJECT_EDIT = "project-edit"
ROLE_PROJECT_VIEWER = "project-viewer"


async def privileges_for(conn, reviewer: str, project_id: Optional[int] = None) -> set[str]:
    """The set of privilege keys `reviewer` holds in the given project context.
    Platform-role privileges apply regardless of project_id; project-role
    privileges only when their project_id matches the one supplied."""
    if not reviewer:
        return set()
    rows = await conn.fetch(
        """
        SELECT DISTINCT rp.privilege_key
        FROM rbac_account_roles ar
        JOIN rbac_roles ro            ON ro.id = ar.role_id
        JOIN rbac_role_privileges rp  ON rp.role_id = ar.role_id
        WHERE lower(ar.reviewer) = lower($1)
          AND ( ro.scope IN ('platform', 'cross-project')
                OR ($2::bigint IS NOT NULL AND ar.project_id = $2::bigint) )
        """,
        reviewer, project_id,
    )
    privs = {r["privilege_key"] for r in rows}
    # F8: prompt.manage supersedes project.archreview.context. Existing grants of the
    # legacy privilege keep working — treat it as an alias that confers prompt.manage.
    if P_PROJECT_ARCHREVIEW_CONTEXT in privs:
        privs.add(P_PROMPT_MANAGE)
    return privs


async def has_privilege(conn, reviewer: str, privilege: str,
                        project_id: Optional[int] = None) -> bool:
    return privilege in await privileges_for(conn, reviewer, project_id)


async def is_platform_admin(conn, reviewer: str) -> bool:
    """Platform admin = holds platform.admin (project-independent)."""
    return await has_privilege(conn, reviewer, P_PLATFORM_ADMIN)


async def roles_for(conn, reviewer: str) -> list[dict]:
    """All role assignments for an account (for display / /api/me)."""
    if not reviewer:
        return []
    rows = await conn.fetch(
        """
        SELECT ar.id, ar.role_id, ro.key, ro.name, ro.scope, ar.project_id,
               p.name AS project_name, ar.granted_by, ar.granted_at
        FROM rbac_account_roles ar
        JOIN rbac_roles ro     ON ro.id = ar.role_id
        LEFT JOIN projects p   ON p.id = ar.project_id
        WHERE lower(ar.reviewer) = lower($1)
        ORDER BY ro.scope, ro.name, p.name NULLS FIRST
        """,
        reviewer,
    )
    return [dict(r) for r in rows]


async def project_role_keys(conn, reviewer: str, project_id: int) -> list[str]:
    """Project-scoped role keys the account holds on a specific project (used to
    keep the legacy project-members view working)."""
    if not reviewer or project_id is None:
        return []
    rows = await conn.fetch(
        """
        SELECT ro.key FROM rbac_account_roles ar
        JOIN rbac_roles ro ON ro.id = ar.role_id AND ro.scope = 'project'
        WHERE lower(ar.reviewer) = lower($1) AND ar.project_id = $2
        """,
        reviewer, project_id,
    )
    return [r["key"] for r in rows]


async def representative_role(conn, reviewer: str) -> str:
    """A single legacy-style role string for back-compat (/api/me `role`).
    Highest platform standing → 'platform-admin'; else the strongest project
    role held anywhere; else 'viewer'."""
    privs = await privileges_for(conn, reviewer)  # platform-only context
    if P_PLATFORM_ADMIN in privs:
        return "platform-admin"
    # Project standing (across any project the account belongs to).
    rows = await conn.fetch(
        """
        SELECT DISTINCT ro.key FROM rbac_account_roles ar
        JOIN rbac_roles ro ON ro.id = ar.role_id AND ro.scope = 'project'
        WHERE lower(ar.reviewer) = lower($1)
        """,
        reviewer,
    )
    keys = {r["key"] for r in rows}
    if ROLE_PROJECT_ADMIN in keys:
        return "admin"
    if ROLE_PROJECT_EDIT in keys:
        return "editor"
    if keys:
        return "viewer"
    return "viewer"


# ── Role / matrix management (platform-admin gated at the API layer) ─────────
async def list_roles(conn) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT r.id, r.key, r.name, r.description, r.scope, r.is_system,
               COALESCE(array_agg(rp.privilege_key) FILTER (WHERE rp.privilege_key IS NOT NULL), '{}') AS privileges,
               (SELECT count(*) FROM rbac_account_roles ar WHERE ar.role_id = r.id) AS assignment_count
        FROM rbac_roles r
        LEFT JOIN rbac_role_privileges rp ON rp.role_id = r.id
        GROUP BY r.id
        ORDER BY r.scope DESC, r.name
        """
    )
    return [dict(r) for r in rows]


async def list_privileges(conn) -> list[dict]:
    rows = await conn.fetch(
        "SELECT key, name, description, scope FROM rbac_privileges ORDER BY scope DESC, key")
    return [dict(r) for r in rows]


async def set_role_privileges(conn, role_id: int, privilege_keys: list[str]) -> None:
    """Replace a role's privilege set (the matrix row)."""
    async with conn.transaction():
        await conn.execute("DELETE FROM rbac_role_privileges WHERE role_id = $1", role_id)
        for pk in set(privilege_keys or []):
            await conn.execute(
                "INSERT INTO rbac_role_privileges (role_id, privilege_key) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING", role_id, pk)


async def assign_role(conn, reviewer: str, role_id: int,
                      project_id: Optional[int], granted_by: str) -> None:
    await conn.execute(
        """
        INSERT INTO rbac_account_roles (reviewer, role_id, project_id, granted_by)
        VALUES (lower($1), $2, $3, $4)
        ON CONFLICT (lower(reviewer), role_id, COALESCE(project_id, 0)) DO NOTHING
        """,
        reviewer, role_id, project_id, granted_by,
    )


async def revoke_role(conn, reviewer: str, role_id: int,
                      project_id: Optional[int]) -> None:
    await conn.execute(
        """
        DELETE FROM rbac_account_roles
        WHERE lower(reviewer) = lower($1) AND role_id = $2
          AND COALESCE(project_id, 0) = COALESCE($3::bigint, 0)
        """,
        reviewer, role_id, project_id,
    )


async def enabled_platform_admin_count(conn) -> int:
    """How many ENABLED accounts currently hold a platform-admin role."""
    return await conn.fetchval(
        """
        SELECT count(DISTINCT lower(ar.reviewer))
        FROM rbac_account_roles ar
        JOIN rbac_roles ro ON ro.id = ar.role_id AND ro.key = 'platform-admin'
        JOIN users u       ON lower(u.reviewer) = lower(ar.reviewer)
        WHERE u.enabled
        """
    ) or 0


async def reconcile_default_admin(conn, default_email: str) -> dict:
    """Invariant: there is ALWAYS at least one enabled platform admin. The config
    default admin is NEVER auto-disabled (disabling is a deliberate admin action);
    but if the count of enabled platform admins reaches ZERO, the default is
    re-activated (re-enabled + granted Platform Admin) so the deployment can't
    orphan itself. Returns {"reactivated": bool, "default_email": str|None}."""
    default_email = (default_email or "").strip().lower()
    out = {"reactivated": False, "default_email": default_email or None}
    if not default_email:
        return out
    if await enabled_platform_admin_count(conn) > 0:
        return out  # at least one enabled platform admin — nothing to do
    # Zero enabled platform admins → re-activate the default (only if it exists).
    if not await conn.fetchval("SELECT 1 FROM users WHERE lower(reviewer) = $1", default_email):
        return out
    await conn.execute("UPDATE users SET enabled = true WHERE lower(reviewer) = $1", default_email)
    rid = await conn.fetchval("SELECT id FROM rbac_roles WHERE key = 'platform-admin'")
    if rid:
        await assign_role(conn, default_email, rid, None, "system-reactivate")
    out["reactivated"] = True
    return out

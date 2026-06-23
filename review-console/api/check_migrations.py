#!/usr/bin/env python3
"""Static schema-bootstrap guard — part of the CI/CD validate gate.

Tenancy Phase 2 replaced the flat per-boot migration list (MIGRATE_002..026 + schema.sql, all
run under search_path=public) with a tenant-aware bootstrap (app/db_bootstrap.py) driven by two
GENERATED base schemas. The old "every migrate_0NN.sql is declared + executed in lifespan" rule no
longer holds (the migrations are folded into the base snapshots). This guard checks, WITHOUT a
database, that the NEW model is wired:

  1. both base schema files exist and are non-empty (app/schema_control.sql, app/schema_client.sql),
  2. db_bootstrap is imported and bootstrap() is called in lifespan,
  3. the boot no longer runs the legacy flat MIGRATE_0NN list (that path shadows tenant tables),
  4. any legacy migrate_0NN_*.sql kept for provenance is BEGIN/COMMIT-balanced.

Exit non-zero on any violation. Mirrors check_routes.py's role in the gate.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
APP = HERE / "app"
MAIN = APP / "main.py"


def main() -> int:
    main_src = MAIN.read_text()
    errors: list[str] = []

    # 1: base schema files present + non-empty
    for base in ("schema_control.sql", "schema_client.sql"):
        p = APP / base
        if not p.exists():
            errors.append(f"base schema {base} missing (generate via scripts/gen_base_schema.sh)")
        elif p.stat().st_size == 0:
            errors.append(f"base schema {base} is empty")

    # 2: bootstrap wired into the app
    if "import db_bootstrap" not in main_src:
        errors.append("main.py does not import db_bootstrap")
    if not re.search(r"db_bootstrap\.bootstrap\(", main_src):
        errors.append("db_bootstrap.bootstrap() is not called in main.py (lifespan)")

    # 3: the legacy flat-migration boot path must be gone (it shadows tenant tables on reboot)
    if re.search(r"await conn\.execute\(MIGRATE_\d+_PATH\.read_text\(\)\)", main_src):
        errors.append("legacy MIGRATE_0NN execution still present in lifespan — remove it "
                      "(folded into the generated base schemas; running it shadows tenant tables)")

    # 4: BEGIN/COMMIT balance on any legacy migration files kept for provenance
    for f in sorted(APP.glob("migrate_*.sql")):
        sql = f.read_text()
        begins = len(re.findall(r"(?im)^\s*BEGIN\s*;", sql))
        commits = len(re.findall(r"(?im)^\s*COMMIT\s*;", sql))
        if begins != commits:
            errors.append(f"{f.name}: BEGIN/COMMIT unbalanced ({begins} BEGIN, {commits} COMMIT)")

    if errors:
        print("SCHEMA-BOOTSTRAP CHECK: FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("SCHEMA-BOOTSTRAP CHECK: OK (base schemas present, bootstrap wired, legacy path removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

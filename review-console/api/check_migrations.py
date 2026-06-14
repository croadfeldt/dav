#!/usr/bin/env python3
"""Static migration-wiring guard — part of the CI/CD validate gate.

Migrations run in `lifespan` on every boot with no per-migration isolation (a broken or
unwired migration is an outage-class mistake). This checks, WITHOUT a database, that:

  1. every app/migrate_0NN_*.sql file has a `MIGRATE_0NN_PATH = ...` declaration in main.py,
  2. every declared MIGRATE_0NN_PATH is actually executed in `lifespan`,
  3. the migration numbers are contiguous (no gap that means a skipped/renamed file),
  4. each migration file's BEGIN/COMMIT are balanced (a forgotten COMMIT leaves a txn open).

Exit non-zero on any violation. Mirrors check_routes.py's role in the gate.
"""
import re
import sys
from pathlib import Path

APP = Path(__file__).parent / "app"
MAIN = APP / "main.py"


def main() -> int:
    main_src = MAIN.read_text()
    errors: list[str] = []

    files = sorted(APP.glob("migrate_*.sql"))
    file_nums = {}
    for f in files:
        m = re.match(r"migrate_(\d+)_", f.name)
        if not m:
            errors.append(f"{f.name}: does not match migrate_<NNN>_*.sql")
            continue
        file_nums[int(m.group(1))] = f.name

    declared = {int(n): name for n, name in
                re.findall(r"MIGRATE_(\d+)_PATH\s*=.*?\"(migrate_\d+_[^\"]+\.sql)\"", main_src)}
    executed = {int(n) for n in re.findall(r"await conn\.execute\(MIGRATE_(\d+)_PATH\.read_text\(\)\)", main_src)}

    # 1 + 2: file <-> declaration <-> execution
    for num, name in file_nums.items():
        if num not in declared:
            errors.append(f"{name}: file exists but no MIGRATE_{num:03d}_PATH declaration in main.py")
        elif declared[num] != name:
            errors.append(f"MIGRATE_{num:03d}_PATH points at {declared[num]!r}, file is {name!r}")
        if num not in executed:
            errors.append(f"{name}: declared but never executed in lifespan()")
    for num in declared:
        if num not in file_nums:
            errors.append(f"MIGRATE_{num:03d}_PATH declared but migrate_{num:03d}_*.sql file missing")
    for num in executed:
        if num not in declared:
            errors.append(f"MIGRATE_{num:03d}_PATH executed in lifespan but never declared")

    # 3: contiguous numbering (migrations start at 002)
    nums = sorted(file_nums)
    if nums:
        expected = list(range(nums[0], nums[-1] + 1))
        missing = sorted(set(expected) - set(nums))
        if missing:
            errors.append(f"non-contiguous migrations — missing: {missing}")

    # 4: BEGIN/COMMIT balance per file
    for num, name in file_nums.items():
        sql = (APP / name).read_text()
        begins = len(re.findall(r"(?im)^\s*BEGIN\s*;", sql))
        commits = len(re.findall(r"(?im)^\s*COMMIT\s*;", sql))
        if begins != commits:
            errors.append(f"{name}: BEGIN/COMMIT unbalanced ({begins} BEGIN, {commits} COMMIT)")

    if errors:
        print("MIGRATION-WIRING CHECK: FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"MIGRATION-WIRING CHECK: OK ({len(file_nums)} migrations, contiguous, wired, balanced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

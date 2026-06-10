#!/usr/bin/env python3
"""Post-deploy smoke / validation harness for the DAV review-console API.

This is an IN-POD INTEGRATION harness (distinct from the pytest unit tests in
review-console/api/test_*.py): it runs inside the deployed API pod and exercises the
real code against the LIVE Postgres + run-workspace PVC. Read-only checks against live
data; every WRITE test runs inside a throwaway project that is deleted (CASCADE) at the
end, so production data is never touched.

Run it after a deploy:

    POD=$(oc get pods -n dav -o name | grep review-api | grep -v build | head -1 | cut -d/ -f2)
    oc cp review-console/api/validation/qa_validate.py "dav/$POD:/tmp/qa_validate.py"
    oc exec -n dav "$POD" -- python3 /tmp/qa_validate.py

Exit is informational (prints "N PASS / M FAIL"). Add a check by calling
`ck(name, condition, detail)` in the relevant section; group new features under their own
"── <feature> ──" block. Keep write tests inside the throwaway-project scope.
"""
import os, sys, asyncio, glob, traceback

# Importable whether launched as a file in-pod (sys.path[0] = script dir) or locally.
for _p in ("/opt/app-root/src", os.getcwd(),
           os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if os.path.isdir(os.path.join(_p, "app")) and _p not in sys.path:
        sys.path.insert(0, _p)

import asyncpg
from app import (capability_catalog as CC, assessment_ingest as AI,
                 prompts_registry as PR, analysis_compare as AC, results as R)

REPORT = []
def ck(name, cond, detail=""):
    REPORT.append((("PASS" if cond else "FAIL"), name, detail))
def note(name, detail):
    REPORT.append(("NOTE", name, detail))


async def main():
    dsn = os.environ.get("DB_DSN") or os.environ.get("DATABASE_URL")
    c = await asyncpg.connect(dsn)
    test_pid = None
    try:
        # ── Capability-catalog collapse (one UDLM table) ────────────────────
        inv = await c.fetchval("SELECT to_regclass('capability_inventory')")
        ck("collapse: capability_inventory dropped", inv is None, f"to_regclass={inv}")
        cols = set(r["column_name"] for r in await c.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name='capability_catalog'"))
        need = {"family", "normalized_to_term_id", "created_via", "classification", "domain_prefix"}
        ck("collapse: catalog UDLM cols present", need <= cols, f"missing={need - cols}")
        pn = await c.fetchval("""SELECT is_nullable FROM information_schema.columns
            WHERE table_name='capability_catalog' AND column_name='project_id'""")
        ck("collapse: catalog.project_id nullable", pn == "YES", f"is_nullable={pn}")

        # ── F7 assessment ingestion (schema) ────────────────────────────────
        ck("F7: assessments table", await c.fetchval("SELECT to_regclass('assessments')") is not None)
        ck("F7: assessment_findings table", await c.fetchval("SELECT to_regclass('assessment_findings')") is not None)

        # ── F8 prompt management (schema + RBAC) ────────────────────────────
        ck("F8: section_overrides column",
           "section_overrides" in set(r["column_name"] for r in await c.fetch(
               "SELECT column_name FROM information_schema.columns WHERE table_name='project_stage_context'")))
        ck("F8: prompt.manage privilege seeded",
           await c.fetchval("SELECT count(*) FROM rbac_privileges WHERE key='prompt.manage'") == 1)
        pmroles = [r["key"] for r in await c.fetch(
            """SELECT ro.key FROM rbac_role_privileges rp JOIN rbac_roles ro ON ro.id=rp.role_id
               WHERE rp.privilege_key='prompt.manage' ORDER BY ro.key""")]
        ck("F8: prompt.manage on admin+edit roles",
           "project-admin" in pmroles and "project-edit" in pmroles, f"roles={pmroles}")

        # ── Capability taxonomy (read) ──────────────────────────────────────
        st = await CC.stats(c)
        ck("taxonomy: ~204 terms seeded", st["taxonomy_terms"] >= 200, f"stats={st}")
        ck("taxonomy: aliases seeded", st["aliases"] >= 10, f"aliases={st['aliases']}")
        n1 = await CC.normalize(c, "Policy Engine")
        ck("normalize: exact term hit", n1["status"] == "normalized" and bool(n1["term_id"]), f"{n1}")
        n2 = await CC.normalize(c, "zxqw nonexistent capability 9931")
        ck("normalize: unknown -> taxonomy gap", n2["status"] == "proposed-taxonomy-gap", f"{n2}")

        # ── Prompt registry + assemble (incl. Review/Enhancement split) ─────
        keys = [s["key"] for s in PR.registry()]
        ck("F8: registry has 3 stages incl. split",
           {"stage2-analysis", "arch_review", "enhancement"} <= set(keys), f"keys={keys}")
        asm = PR.assemble("arch_review", content="QA-CTX", section_overrides={})
        ck("F8: assemble appends context", "QA-CTX" in asm["text"], asm["text"][:60])
        asm2 = PR.assemble("stage2-analysis", section_overrides={"system": "QA-OVERRIDE"})
        ck("F8: assemble section override",
           asm2["sections"][0]["overridden"] and "QA-OVERRIDE" in asm2["text"], "")

        # ── Static A/B comparator (vendored from engine) on REAL runs ───────
        ck("A/B: comparator vendored/available", AC.available())
        root = str(R._results_root())
        runs = []
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            uu = set(os.path.splitext(os.path.basename(a))[0]
                     for a in glob.glob(os.path.join(d, "analyses", "*.yaml")))
            if uu:
                runs.append((os.path.basename(d), uu, os.path.getmtime(d)))
        runs.sort(key=lambda r: r[2])
        pair = None
        for i in range(len(runs) - 1, 0, -1):
            for j in range(i - 1, -1, -1):
                common = runs[i][1] & runs[j][1]
                if len(common) >= 10:
                    pair = (runs[j][0], runs[i][0], sorted(common)); break
            if pair:
                break
        if pair:
            s = AC.compare_runs(pair[0], pair[1], pair[2])["summary"]
            ck("A/B: compare_runs over real runs",
               s["compared"] == len(pair[2]) and s["verdict"] in ("changed", "equivalent"),
               f"{pair[0]} vs {pair[1]} :: {s}")
            note("A/B sample", f"{s['compared']} compared, {s['changed']} changed, max={s.get('max_severity')}")
        else:
            note("A/B", "no run pair with >=10 shared UCs found")

        # ── WRITE tests in a throwaway project (deleted at end) ─────────────
        await c.execute("DELETE FROM projects WHERE slug='zzz-qa-validate'")
        test_pid = await c.fetchval(
            "INSERT INTO projects (slug, name) VALUES ('zzz-qa-validate','zzz QA validate') RETURNING id")
        note("write-scope", f"throwaway project id={test_pid}")

        summ = await AI.ingest(c, AI.synthetic_fixture(), actor="qa", project_id=test_pid)
        ck("F7: ingest synthetic fixture",
           summ["findings"] == 6 and summ["mapped"] >= 4 and summ["gaps"] >= 1, f"{summ}")
        det = await AI.get_assessment(c, summ["assessment_id"])
        ck("F7: get_assessment + gap_summary",
           bool(det) and len(det["findings"]) == 6 and det["gap_summary"]["by_state"]["absent"] >= 2,
           f"by_state={det['gap_summary']['by_state'] if det else None}")
        ck("F7: findings landed on catalog (observed)",
           await c.fetchval("SELECT count(*) FROM capability_catalog WHERE status='observed' AND project_id=$1", test_pid) == 6)
        ck("F7: observed caps normalized to taxonomy",
           await c.fetchval("SELECT count(*) FROM capability_catalog WHERE project_id=$1 AND normalized_to_term_id IS NOT NULL", test_pid) >= 4)

        await c.execute("""INSERT INTO project_stage_context (project_id,stage,content,section_overrides,updated_by)
            VALUES ($1,'arch_review','AR ctx','{}'::jsonb,'qa'),
                   ($1,'enhancement','ENH ctx','{}'::jsonb,'qa'),
                   ($1,'stage2-analysis','S2 ctx','{"system":"S2 override"}'::jsonb,'qa')""", test_pid)
        ar = await c.fetchval("SELECT content FROM project_stage_context WHERE project_id=$1 AND stage='arch_review'", test_pid)
        en = await c.fetchval("SELECT content FROM project_stage_context WHERE project_id=$1 AND stage='enhancement'", test_pid)
        ck("split: review & enhancement independent", ar == "AR ctx" and en == "ENH ctx" and ar != en, f"ar={ar} en={en}")
        so = await c.fetchval("SELECT section_overrides->>'system' FROM project_stage_context WHERE project_id=$1 AND stage='stage2-analysis'", test_pid)
        ck("F8: section_overrides round-trip", so == "S2 override", f"so={so}")

        rs = await CC.seed_dcm_taxonomy(c)
        ck("taxonomy: reseed idempotent (0 new)",
           rs.get("terms_added", -1) == 0 and rs.get("aliases_added", -1) == 0, f"{rs}")
    except Exception:
        note("HARNESS EXCEPTION", traceback.format_exc())
    finally:
        if test_pid is not None:
            try:
                await c.execute("DELETE FROM projects WHERE id=$1", test_pid)
                ck("cleanup: throwaway project + cascade removed",
                   await c.fetchval("SELECT count(*) FROM assessments WHERE project_id=$1", test_pid) == 0
                   and await c.fetchval("SELECT count(*) FROM capability_catalog WHERE project_id=$1", test_pid) == 0)
            except Exception:
                note("cleanup FAILED", traceback.format_exc())
        await c.close()

    p = sum(1 for r in REPORT if r[0] == "PASS")
    f = sum(1 for r in REPORT if r[0] == "FAIL")
    print(f"\n===== QA RESULTS: {p} PASS / {f} FAIL =====")
    for status, name, detail in REPORT:
        line = f"[{status}] {name}"
        if status != "PASS" and detail:
            line += f"  --  {str(detail)[:300]}"
        print(line)
    print("===== END =====")
    return f


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)

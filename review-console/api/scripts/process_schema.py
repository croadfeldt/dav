#!/usr/bin/env python3
"""Split pg_dump --schema-only output (per-schema) into clean control + client base
schema files for the tenant-aware runner.

- Strips pg_dump preamble noise (SET, set_config, \\restrict, pure comments).
- Relocates any CONTROL (public) object that REFERENCES a client (tenant_flightpath)
  table into the CLIENT file (e.g. the legacy review_* views derived from
  files/review_events, which are client tables). Those follow their tables.
- In the CLIENT file: strips `tenant_flightpath.` qualification (client tables +
  client->client FKs become unqualified -> resolve via the per-tenant search_path),
  while KEEPING `public.` qualification (client->control cross-schema FKs).
- In the CONTROL file: keeps `public.` qualification.
"""
import re, sys

SCRATCH = sys.argv[1] if len(sys.argv) > 1 else "."

def read(p):
    with open(p) as f:
        return f.read()

def split_statements(sql):
    """Split into top-level statements on ';' at line-end, respecting $$ dollar-quotes."""
    stmts, buf, dollar = [], [], False
    for line in sql.splitlines():
        s = line.strip()
        if s.startswith("--") or s == "" or s.startswith("\\restrict") or s.startswith("\\unrestrict"):
            # keep blank/comment only if inside a statement buffer (rare); else drop
            if buf:
                buf.append(line)
            continue
        if re.match(r"^(SET |SELECT pg_catalog\.set_config)", s):
            continue
        buf.append(line)
        # toggle dollar-quote on lines containing an odd count of $$ ... simple heuristic
        if s.count("$$") % 2 == 1:
            dollar = not dollar
        if not dollar and s.endswith(";"):
            stmts.append("\n".join(buf))
            buf = []
    if buf:
        stmts.append("\n".join(buf))
    return stmts

control_raw = read(f"{SCRATCH}/schema_control.raw.sql")
client_raw  = read(f"{SCRATCH}/schema_client.raw.sql")

ctrl_stmts = split_statements(control_raw)
cli_stmts  = split_statements(client_raw)

REF_CLIENT = re.compile(r"\btenant_flightpath\.")

control_out, relocated = [], []
for st in ctrl_stmts:
    if "CREATE SCHEMA" in st:
        continue
    if REF_CLIENT.search(st):
        # a control object that depends on client tables -> move to client file
        relocated.append(st)
    else:
        control_out.append(st)

# Transitive relocation: a control VIEW that references an ALREADY-relocated view
# (e.g. file_current_status -> review_current -> client tables) must also move.
def view_name(st):
    m = re.search(r"CREATE(?: OR REPLACE)? VIEW (?:public\.)?([a-z_]+)", st)
    return m.group(1) if m else None
changed = True
while changed:
    changed = False
    moved_names = [view_name(s) for s in relocated if view_name(s)]
    for st in list(control_out):
        if "CREATE" in st and " VIEW " in st:
            if any(re.search(r"\b%s\b" % re.escape(n), st) for n in moved_names if n and view_name(st) != n):
                control_out.remove(st); relocated.append(st); changed = True

def declient(st):
    # strip tenant_flightpath. (own schema + client->client refs); keep public.
    st = st.replace("tenant_flightpath.", "")
    return st

client_out = []
for st in cli_stmts:
    if "CREATE SCHEMA" in st:
        continue
    client_out.append(declient(st))
# relocated control objects (e.g. review_* views): also strip public. from the
# object's OWN name so it lands in the active (tenant) schema, and declient refs.
reloc_names = [view_name(s) for s in relocated if view_name(s)]
for st in relocated:
    st = declient(st)
    # the view's own name was public.<v>; make it unqualified so it's created in the tenant schema
    st = re.sub(r"CREATE( OR REPLACE)? VIEW public\.", r"CREATE\1 VIEW ", st)
    st = re.sub(r"ALTER TABLE (ONLY )?public\.", r"ALTER TABLE \1", st)  # safety if any
    # references to OTHER relocated views were public.<name>; they now live in the tenant schema
    for n in reloc_names:
        st = re.sub(r"\bpublic\.%s\b" % re.escape(n), n, st)
    client_out.append(st)

HDR_C = "-- DAV control-plane base schema (public). GENERATED from the live DB by the\n-- tenancy schema split; do not hand-edit — regenerate via scripts/gen_base_schema.sh.\n-- Run-once per install under search_path=public (tracked in public.schema_migrations).\n"
HDR_T = "-- DAV per-tenant (client) base schema. GENERATED from the live DB by the tenancy\n-- schema split; do not hand-edit — regenerate via scripts/gen_base_schema.sh.\n-- Run-once per tenant schema under search_path=tenant_<slug>,public (tracked).\n-- Unqualified names resolve to the tenant schema; public.* are cross-schema FKs to control.\n"

with open(f"{SCRATCH}/schema_control.final.sql", "w") as f:
    f.write(HDR_C + "\n" + ";\n\n".join(s.rstrip().rstrip(";") for s in control_out) + ";\n")
with open(f"{SCRATCH}/schema_client.final.sql", "w") as f:
    f.write(HDR_T + "\n" + ";\n\n".join(s.rstrip().rstrip(";") for s in client_out) + ";\n")

print(f"control statements: {len(control_out)}  client statements: {len(client_out)}  relocated(control->client): {len(relocated)}")
print("relocated objects:")
for st in relocated:
    first = st.splitlines()[0][:90]
    print("  ", first)

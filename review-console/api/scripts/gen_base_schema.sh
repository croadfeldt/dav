#!/usr/bin/env bash
# Regenerate the tenant-aware base schema files (app/schema_control.sql, app/schema_client.sql)
# from a *validated* database, splitting control (public) vs client (per-tenant) by where each
# table physically lives. Correct-by-construction: we never hand-split the 1100-line schema.sql.
#
# Usage (against a restored prod copy in a local podman postgres — see the runbook):
#   DB_CONTAINER=dav-dryrun DB=dav_review CLIENT_SCHEMA=tenant_flightpath ./gen_base_schema.sh
#
# PREREQUISITE: the source DB must already be in the corrected partition (the 3 mis-placed
# tables customers/customer_projects/bundle_attachments moved back to public) — see the runbook
# reclassification step — otherwise the generated control base will carry control->client FKs.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(cd "$HERE/../app" && pwd)"
DB_CONTAINER="${DB_CONTAINER:-dav-dryrun}"
DB="${DB:-dav_review}"
CLIENT_SCHEMA="${CLIENT_SCHEMA:-tenant_flightpath}"
TMP="$(mktemp -d)"

podman exec "$DB_CONTAINER" bash -lc "pg_dump --schema-only --no-owner --no-privileges -n public -U dav $DB" > "$TMP/schema_control.raw.sql"
podman exec "$DB_CONTAINER" bash -lc "pg_dump --schema-only --no-owner --no-privileges -n $CLIENT_SCHEMA -U dav $DB" > "$TMP/schema_client.raw.sql"

# process_schema.py reads <dir>/schema_{control,client}.raw.sql and writes *.final.sql
python3 "$HERE/process_schema.py" "$TMP"
cp "$TMP/schema_control.final.sql" "$APP/schema_control.sql"
cp "$TMP/schema_client.final.sql"  "$APP/schema_client.sql"
echo "regenerated $APP/schema_control.sql + schema_client.sql"
echo "REMEMBER: bump CONTROL_VERSION/CLIENT_VERSION in app/db_bootstrap.py when the base snapshot changes."
rm -rf "$TMP"

#!/usr/bin/env bash
# Load the DAV self-evaluation Findings & Resolutions use cases into a live DAV
# instance and create the "Findings & Resolutions (self-eval)" scoping set.
#
# Idempotent: an already-present UC (409) or set is treated as success.
#
# Usage:
#   DAV_PAT=dav_pat_xxxxx DAV_PROJECT=<project-slug> ./load-into-dav.sh
#
# Env:
#   DAV_PAT      (required)  a DAV Personal Access Token with project usecases priv
#   DAV_PROJECT  (required)  the target project slug (e.g. the DAV self-eval project)
#   DAV_HOST     (optional)  default https://dav.roadfeldt.com
set -euo pipefail

HOST="${DAV_HOST:-https://dav.roadfeldt.com}"
: "${DAV_PAT:?set DAV_PAT to a dav_pat_... token}"
: "${DAV_PROJECT:?set DAV_PROJECT to the target project slug}"
DIR="$(cd "$(dirname "$0")/dav/use-cases/findings_resolution" && pwd)"
SET_NAME="Findings & Resolutions (self-eval)"
SET_DESC="DAV's own ADR-driven change-submission loop (uc-fr-001..007); validates the Findings & Resolutions capability. See dav/docs/findings-resolution-design.md."

auth=(-H "Authorization: Bearer ${DAV_PAT}" -H "X-DAV-Project: ${DAV_PROJECT}")
jq_get() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }

echo "== Loading UCs from ${DIR} into ${HOST} (project=${DAV_PROJECT}) =="
for f in "${DIR}"/uc-fr-*.yaml; do
  uuid="$(grep -m1 '^use_case_uuid:' "$f" | awk '{print $2}')"
  body="$(python3 -c "import json,sys;print(json.dumps({'yaml_content':open(sys.argv[1]).read()}))" "$f")"
  code="$(curl -sS -o /tmp/uc_resp.json -w '%{http_code}' "${auth[@]}" \
    -H 'Content-Type: application/json' -X POST "${HOST}/api/use-cases" -d "$body" || true)"
  case "$code" in
    200|201) echo "  + ${uuid}  created" ;;
    409)     echo "  = ${uuid}  already exists" ;;
    *)       echo "  ! ${uuid}  HTTP ${code}: $(cat /tmp/uc_resp.json)"; exit 1 ;;
  esac
done

echo "== Creating scoping set: ${SET_NAME} =="
setbody="$(python3 -c "import json;print(json.dumps({'name':'${SET_NAME}','description':'''${SET_DESC}'''}))")"
curl -sS -o /tmp/set_resp.json -w '' "${auth[@]}" -H 'Content-Type: application/json' \
  -X POST "${HOST}/api/sets" -d "$setbody" || true
set_id="$(jq_get id </tmp/set_resp.json || true)"
if [ -z "${set_id}" ] || [ "${set_id}" = "None" ]; then
  # already exists — find it by name
  set_id="$(curl -sS "${auth[@]}" "${HOST}/api/sets" | python3 -c "
import sys,json
name='''${SET_NAME}'''
for s in json.load(sys.stdin):
    if s.get('name','').lower()==name.lower(): print(s['id']); break
")"
fi
echo "  set_id=${set_id}"

echo "== Adding members =="
for f in "${DIR}"/uc-fr-*.yaml; do
  uuid="$(grep -m1 '^use_case_uuid:' "$f" | awk '{print $2}')"
  mbody="$(python3 -c "import json;print(json.dumps({'uc_uuid':'${uuid}','uc_source':'managed'}))")"
  code="$(curl -sS -o /dev/null -w '%{http_code}' "${auth[@]}" -H 'Content-Type: application/json' \
    -X PUT "${HOST}/api/sets/${set_id}/members" -d "$mbody" || true)"
  echo "  ${uuid} -> set ${set_id}  HTTP ${code}"
done
echo "== Done. Set '${SET_NAME}' now scopes uc-fr-001..007. =="

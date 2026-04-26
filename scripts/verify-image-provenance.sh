#!/usr/bin/env bash
# verify-image-provenance.sh
#
# Verifies that the four DAV container images currently deployed in the
# cluster were built from the expected git commit. Reads the
# org.opencontainers.image.revision OCI label that the Ansible role embeds
# at build time and compares it against `git rev-parse HEAD` from the
# checkout you're running this script in.
#
# Background: 2026-04-26 incident. A binary build appeared to succeed
# (`Build #N ... Complete`), the imagestream got a new digest, but the
# image content was a previous version because the Ansible playbook's
# build-context staging step had been interrupted earlier. We had to
# grep for marker code inside the image to figure out what was actually
# running. With the OCI revision label embedded, this check is a
# one-liner per image.
#
# Usage:
#   scripts/verify-image-provenance.sh           # check all 4 images
#   scripts/verify-image-provenance.sh dav-engine
#
# Output:
#   Per image: name, digest, embedded commit, expected commit, MATCH/MISMATCH
#   Exit code 0 if all match (clean), 1 if any mismatch (or dirty).

set -euo pipefail

# Resolve repo root (script lives at <repo>/scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
NAMESPACE="${DAV_NAMESPACE:-dav}"

# All four images we manage
ALL_IMAGES=(dav-engine dav-review-api dav-review-ui dav-docs-mcp)
IMAGES=("${@:-${ALL_IMAGES[@]}}")

EXPECTED_COMMIT="$(cd "${REPO_ROOT}" && git rev-parse HEAD)"
WORKING_TREE_DIRTY="false"
if [[ -n "$(cd "${REPO_ROOT}" && git status --porcelain)" ]]; then
    WORKING_TREE_DIRTY="true"
fi

echo "=================================================================="
echo " DAV image provenance check"
echo "=================================================================="
echo "  Repo:             ${REPO_ROOT}"
echo "  Expected commit:  ${EXPECTED_COMMIT}"
echo "  Working tree:     $([ "${WORKING_TREE_DIRTY}" = "true" ] && echo 'DIRTY (uncommitted changes)' || echo 'clean')"
echo "  Cluster ns:       ${NAMESPACE}"
echo

mismatches=0
for image in "${IMAGES[@]}"; do
    echo "── ${image} ──"

    if ! oc get is "${image}" -n "${NAMESPACE}" >/dev/null 2>&1; then
        echo "  [SKIP] ImageStream not found in namespace ${NAMESPACE}"
        echo
        continue
    fi

    # Pull the latest tag's digest. Resolves through the imagestream
    # rather than through any deployment, so it represents what the next
    # pipeline run / pod restart will pull.
    digest_ref="$(oc get is "${image}" -n "${NAMESPACE}" \
        -o jsonpath='{.status.tags[?(@.tag=="latest")].items[0].dockerImageReference}')"
    if [[ -z "${digest_ref}" ]]; then
        echo "  [SKIP] No :latest tag found"
        echo
        continue
    fi

    # Inspect labels via `oc image info`. Outputs JSON; pluck the
    # revision label and dirty flag.
    info_json="$(oc image info "${digest_ref}" --output=json 2>/dev/null || true)"
    if [[ -z "${info_json}" ]]; then
        echo "  [ERROR] oc image info failed for ${digest_ref}"
        mismatches=$((mismatches + 1))
        echo
        continue
    fi

    embedded_commit="$(echo "${info_json}" | python3 -c '
import json, sys
data = json.load(sys.stdin)
labels = data.get("config", {}).get("config", {}).get("Labels") or {}
print(labels.get("org.opencontainers.image.revision", ""))
')"
    embedded_dirty="$(echo "${info_json}" | python3 -c '
import json, sys
data = json.load(sys.stdin)
labels = data.get("config", {}).get("config", {}).get("Labels") or {}
print(labels.get("io.dav.repo.dirty", ""))
')"

    echo "  Digest:           ${digest_ref##*@}"
    echo "  Embedded commit:  ${embedded_commit:-<absent>}"
    echo "  Embedded dirty:   ${embedded_dirty:-<absent>}"

    if [[ -z "${embedded_commit}" || "${embedded_commit}" = "unknown" ]]; then
        echo "  [STALE] Image has no provenance label — built before commit-stamping was added."
        echo "          Rebuild with: ansible-playbook ansible/playbook.yaml --tags engine,mcp,review-console"
        mismatches=$((mismatches + 1))
    elif [[ "${embedded_commit}" != "${EXPECTED_COMMIT}" ]]; then
        echo "  [MISMATCH] Image was built from a different commit than HEAD."
        mismatches=$((mismatches + 1))
    elif [[ "${embedded_dirty}" = "true" ]]; then
        echo "  [DIRTY] Image was built from a working tree with uncommitted changes."
        mismatches=$((mismatches + 1))
    else
        echo "  [MATCH] Image matches HEAD."
    fi
    echo
done

echo "=================================================================="
if [[ ${mismatches} -eq 0 ]]; then
    echo " All checked images match HEAD ✓"
    exit 0
else
    echo " ${mismatches} image(s) MISMATCH or STALE"
    echo "=================================================================="
    exit 1
fi

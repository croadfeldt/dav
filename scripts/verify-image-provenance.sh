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
# running. With the OCI revision label embedded, this check is fast.
#
# Implementation note: queries `oc get image` (the OpenShift Image API
# server, which is reachable through the cluster API endpoint from any
# machine that has a kubeconfig). Earlier versions of this script used
# `oc image info` against the internal registry hostname, which only
# resolves from inside the cluster. The Image API approach works from
# any laptop with `oc whoami` succeeding.
#
# Usage:
#   scripts/verify-image-provenance.sh           # check all 4 images
#   scripts/verify-image-provenance.sh dav-engine
#
# Output:
#   Per image: name, digest, embedded commit, MATCH/MISMATCH/STALE/DIRTY
#   Exit code 0 if all match (clean), 1 if any mismatch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
NAMESPACE="${DAV_NAMESPACE:-dav}"

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
for stream in "${IMAGES[@]}"; do
    echo "── ${stream} ──"

    if ! oc get is "${stream}" -n "${NAMESPACE}" >/dev/null 2>&1; then
        echo "  [SKIP] ImageStream not found in namespace ${NAMESPACE}"
        echo
        continue
    fi

    # Resolve :latest -> Image object name (digest-form, e.g. sha256:abc...)
    # The ImageStream's status.tags[?(@.tag=="latest")].items[0].image is the
    # canonical Image API object name. Use it as the lookup key.
    image_name="$(oc get is "${stream}" -n "${NAMESPACE}" \
        -o jsonpath='{.status.tags[?(@.tag=="latest")].items[0].image}')"
    if [[ -z "${image_name}" ]]; then
        echo "  [SKIP] No :latest tag found"
        echo
        continue
    fi

    # Pull the actual Image object from the Image API and read its labels.
    # Labels live at .dockerImageMetadata.Config.Labels (camelCase varies by
    # OCP version; query a few known shapes).
    labels_json="$(oc get image "${image_name}" -o json 2>/dev/null || true)"
    if [[ -z "${labels_json}" ]]; then
        echo "  [ERROR] oc get image failed for ${image_name}"
        mismatches=$((mismatches + 1))
        echo
        continue
    fi

    # Pull labels via python — handles both `Labels` and `labels` and survives
    # versions where dockerImageMetadata is a JSON-encoded string.
    read -r embedded_commit embedded_dirty <<<"$(echo "${labels_json}" | python3 -c '
import json, sys
data = json.load(sys.stdin)
md = data.get("dockerImageMetadata", {})
# Some OCP versions ship dockerImageMetadata as a JSON string, not an object.
if isinstance(md, str):
    try:
        md = json.loads(md)
    except Exception:
        md = {}
# Walk a couple of known shapes to find Labels.
candidates = [
    md.get("Config", {}).get("Labels"),
    md.get("config", {}).get("Labels"),
    md.get("config", {}).get("labels"),
    md.get("ContainerConfig", {}).get("Labels"),
]
labels = next((c for c in candidates if c), {}) or {}
commit = labels.get("org.opencontainers.image.revision", "")
dirty = labels.get("io.dav.repo.dirty", "")
print(commit or "<absent>", dirty or "<absent>")
')"

    echo "  Digest:           ${image_name}"
    echo "  Embedded commit:  ${embedded_commit}"
    echo "  Embedded dirty:   ${embedded_dirty}"

    if [[ "${embedded_commit}" = "<absent>" || "${embedded_commit}" = "unknown" ]]; then
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

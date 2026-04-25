"""
Assertion module for tinyurl-assert-001: check that all doc handles referenced
in UC metadata.references resolve to real files under the spec corpus.

This is a deterministic check — no LLM involved, no tool calls. Pure filesystem
walk plus simple string comparison.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Import shape: these come from DAV's engine package.
from dav.core.schema import (  # type: ignore[import-not-found]
    AssertionResult,
    SeverityDescriptor,
)


def check_referenced_docs_exist(
    spec_root: str,
    use_case_root: str,
    **kwargs: Any,
) -> AssertionResult:
    """
    Walk every UC file under `use_case_root`, extract `metadata.references`
    entries that look like doc handles (end in `.md` or contain a section
    reference like `doc.md/Section Title`), and verify each resolves to a
    file under `spec_root`.

    Returns an AssertionResult describing pass/fail plus, on failure, the
    list of broken references with UC context.
    """
    spec_root_path = Path(spec_root).resolve()
    uc_root_path = Path(use_case_root).resolve()

    if not spec_root_path.is_dir():
        return AssertionResult(
            passed=False,
            diagnostic=f"Spec root {spec_root} does not exist or is not a directory.",
            severity="critical",
            confidence="high",
        )

    if not uc_root_path.is_dir():
        return AssertionResult(
            passed=False,
            diagnostic=f"UC root {use_case_root} does not exist or is not a directory.",
            severity="critical",
            confidence="high",
        )

    broken: list[dict[str, str]] = []
    uc_count = 0
    refs_checked = 0

    for uc_file in sorted(uc_root_path.rglob("*.yaml")):
        if uc_file.name == "README.md":
            continue
        uc_count += 1

        try:
            uc_data = yaml.safe_load(uc_file.read_text())
        except yaml.YAMLError as exc:
            broken.append(
                {
                    "uc": str(uc_file.relative_to(uc_root_path)),
                    "error": f"YAML parse error: {exc}",
                    "handle": "(parse failure)",
                }
            )
            continue

        if not isinstance(uc_data, dict):
            continue

        references = (uc_data.get("metadata") or {}).get("references") or []
        if not isinstance(references, list):
            continue

        for ref in references:
            if not isinstance(ref, str):
                continue
            refs_checked += 1

            # Strip section suffix if present (e.g., "doc.md/Section Title")
            doc_part = ref.split("/")[0] if "/" in ref else ref

            # Only check refs that look like doc filenames
            if not doc_part.endswith(".md"):
                continue

            candidate = spec_root_path / doc_part
            if not candidate.is_file():
                broken.append(
                    {
                        "uc": str(uc_file.relative_to(uc_root_path)),
                        "handle": ref,
                        "resolved_path_tried": str(candidate.relative_to(spec_root_path.parent)),
                    }
                )

    if not broken:
        return AssertionResult(
            passed=True,
            diagnostic=(
                f"All referenced doc handles resolve. "
                f"Checked {refs_checked} references across {uc_count} UCs."
            ),
            details={
                "uc_count": uc_count,
                "references_checked": refs_checked,
            },
            severity="advisory",
            confidence="high",
        )

    # Failed. Severity escalates with the number of broken references.
    count = len(broken)
    if count == 1:
        severity: SeverityDescriptor | str = "minor"
    elif count <= 3:
        severity = "major"
    else:
        severity = SeverityDescriptor(
            label="critical",
            score=88,
            factors={
                "override_rationale": (
                    f"{count} broken references — indicates systemic corpus drift, not isolated typo"
                ),
            },
        )

    return AssertionResult(
        passed=False,
        diagnostic=f"{count} referenced doc handle(s) failed to resolve.",
        details={
            "broken_references": broken,
            "uc_count": uc_count,
            "references_checked": refs_checked,
        },
        severity=severity,
        confidence="high",
    )

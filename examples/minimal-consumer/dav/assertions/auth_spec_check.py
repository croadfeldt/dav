"""
Assertion module for tinyurl-uc-002 (hybrid UC): check that the auth spec
contains all required section headings. This is the assertion portion of the
hybrid UC; if it passes, DAV proceeds to the analytical portion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dav.core.schema import (  # type: ignore[import-not-found]
    AssertionResult,
    SeverityDescriptor,
)


_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)


def check_auth_spec_sections(
    spec_path: str,
    required_sections: list[str],
    **kwargs: Any,
) -> AssertionResult:
    """
    Verify that the markdown file at `spec_path` contains a heading matching
    each name in `required_sections` (case-insensitive substring match).

    Returns pass if every required section is found. On miss, returns fail
    with the list of missing section names.
    """
    path = Path(spec_path)
    if not path.is_file():
        return AssertionResult(
            passed=False,
            diagnostic=f"Spec file {spec_path} does not exist.",
            severity="critical",
            confidence="high",
        )

    content = path.read_text()
    headings = [m.group(1).lower() for m in _HEADING_RE.finditer(content)]

    missing: list[str] = []
    for required in required_sections:
        key = required.lower()
        if not any(key in heading for heading in headings):
            missing.append(required)

    if not missing:
        return AssertionResult(
            passed=True,
            diagnostic=(
                f"All {len(required_sections)} required sections present in "
                f"{path.name}."
            ),
            details={
                "sections_checked": required_sections,
                "headings_found": headings,
            },
            severity="advisory",
            confidence="high",
        )

    return AssertionResult(
        passed=False,
        diagnostic=(
            f"{len(missing)} of {len(required_sections)} required sections "
            f"missing from {path.name}."
        ),
        details={
            "missing_sections": missing,
            "headings_found": headings,
        },
        severity=SeverityDescriptor(
            label="major",
            score=72,
            factors={
                "override_rationale": (
                    "Hybrid UC: missing sections block the analytical portion "
                    "from running meaningfully"
                ),
            },
        ),
        confidence="high",
    )

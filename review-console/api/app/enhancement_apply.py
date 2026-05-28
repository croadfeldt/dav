"""Parse and apply ENHANCEMENT blocks emitted by /api/enhancements.

The enhancement endpoint (arch_review.py) produces structured blocks of the form:

    ENHANCEMENT <id> (gap: <gap_id>[, ...])
    target: <doc handle>
    action: add_section | update_section | replace_text | new_document
    section_title: <verbatim>
    position: <after "..." | end_of_document | top>
    rationale: <text>
    ```markdown
    <verbatim content>
    ```
    acceptance: <text>

This module parses that text into Enhancement objects, applies the patches
to file contents, and is consumed by the /api/enhancements/apply endpoint
which pushes the result through corpus_push.push_uc_to_github.

Scope intentionally limited to:
  * `add_section`        — insert a new heading + content at `position`
  * `update_section`     — replace existing section keyed on section_title
  * `new_document`       — create a new file with content verbatim
  * `replace_text`       — NOT yet implemented; returns parse_error so the
                           caller can surface "manual review needed" without
                           silently failing

Path resolution: target field is a doc handle like `dcm/components/foo.md`.
The first path segment is the namespace; the remainder is the file path
inside the target managed_repos row's content root.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Enhancement:
    """One parsed ENHANCEMENT block."""
    id: str
    gap_ids: list[str] = field(default_factory=list)
    uc_handles: list[str] = field(default_factory=list)
    target: str = ""             # full doc handle, e.g. "dcm/components/foo.md"
    action: str = ""              # add_section | update_section | replace_text | new_document
    section_title: str = ""
    position: str = ""            # "top" | "end_of_document" | 'after "..."'
    rationale: str = ""
    content: str = ""             # verbatim markdown body, no fences
    acceptance: str = ""
    parse_errors: list[str] = field(default_factory=list)

    @property
    def target_namespace(self) -> str:
        return self.target.split("/", 1)[0] if "/" in self.target else self.target

    @property
    def target_path(self) -> str:
        """Path portion after the leading namespace — relative to the repo root_path."""
        return self.target.split("/", 1)[1] if "/" in self.target else self.target


# ── Parser ──────────────────────────────────────────────────────────────────

_HEADER_RE = re.compile(
    r"^\s*ENHANCEMENT\s+(?P<id>\S+)\s*(?:\((?P<meta>[^)]*)\))?\s*$",
    re.IGNORECASE,
)
_KV_RE = re.compile(r"^\s*(?P<key>target|action|section_title|position|rationale|acceptance)\s*:\s*(?P<val>.*)$", re.IGNORECASE)
_FENCE_OPEN_RE = re.compile(r"^\s*```(?P<lang>\w+)?\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")
_VALID_ACTIONS = {"add_section", "update_section", "replace_text", "new_document"}


def parse_enhancement_blocks(text: str) -> list[Enhancement]:
    """Parse all ENHANCEMENT blocks out of the raw text emitted by the LLM.

    Tolerant — extra prose between blocks is skipped; malformed blocks are
    returned with `parse_errors` populated so the caller can show them
    without silently dropping data.
    """
    # Strip any leading <think>...</think> blocks just in case the upstream
    # stream stripper missed them (defence in depth).
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    lines = text.split("\n")
    out: list[Enhancement] = []
    i = 0
    while i < len(lines):
        m = _HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        enh = Enhancement(id=m.group("id"))
        meta = (m.group("meta") or "").strip()
        # meta looks like: "gap: GAP-001"  or  "gaps: GAP-001, GAP-002, UCs: foo/bar, baz/qux"
        for part in re.split(r"\s*,\s*", meta):
            kv = re.match(r"^(?P<k>\w+)\s*:\s*(?P<v>.+)$", part)
            if not kv:
                if part:
                    enh.gap_ids.append(part)
                continue
            k = kv.group("k").lower()
            v = kv.group("v").strip()
            if k.startswith("gap"):
                enh.gap_ids.append(v)
            elif k.startswith("uc"):
                enh.uc_handles.append(v)
        i += 1
        # Read key:value lines until we hit a fence or the next ENHANCEMENT
        while i < len(lines):
            line = lines[i]
            if _HEADER_RE.match(line):
                break
            if _FENCE_OPEN_RE.match(line):
                break
            kvm = _KV_RE.match(line)
            if kvm:
                key = kvm.group("key").lower()
                val = kvm.group("val").strip()
                if key == "target":
                    enh.target = val
                elif key == "action":
                    enh.action = val.strip().lower()
                elif key == "section_title":
                    enh.section_title = val.strip('"\'')
                elif key == "position":
                    enh.position = val
                elif key == "rationale":
                    enh.rationale = val
                elif key == "acceptance":
                    enh.acceptance = val
            i += 1
        # Read the fenced markdown block (if present)
        if i < len(lines) and _FENCE_OPEN_RE.match(lines[i]):
            i += 1  # skip opening fence
            buf: list[str] = []
            while i < len(lines) and not _FENCE_CLOSE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # skip closing fence
            enh.content = "\n".join(buf).rstrip() + "\n"
        # Trailing key:value lines (acceptance often comes after the fence)
        while i < len(lines):
            line = lines[i]
            if _HEADER_RE.match(line):
                break
            kvm = _KV_RE.match(line)
            if kvm:
                key = kvm.group("key").lower()
                val = kvm.group("val").strip()
                if key == "acceptance":
                    enh.acceptance = val
                elif key == "rationale" and not enh.rationale:
                    enh.rationale = val
            i += 1
        # Validate
        if enh.action not in _VALID_ACTIONS:
            enh.parse_errors.append(f"unknown action {enh.action!r}; must be one of {sorted(_VALID_ACTIONS)}")
        if not enh.target:
            enh.parse_errors.append("missing target")
        if enh.action in {"add_section", "update_section"} and not enh.section_title:
            enh.parse_errors.append("section_title required for add_section/update_section")
        if enh.action != "replace_text" and not enh.content:
            enh.parse_errors.append("empty content block — model emitted no body")
        out.append(enh)
    return out


# ── Patch applier ───────────────────────────────────────────────────────────

# Markdown ATX heading: ^(#+) <title>$  (allow trailing #s per the spec)
_HEADING_RE = re.compile(r"^(?P<hashes>#+)\s+(?P<title>.*?)\s*#*\s*$")


def _find_section_bounds(content: str, title: str) -> Optional[tuple[int, int, int]]:
    """Locate a section by heading title.

    Returns (heading_line_idx, body_start_idx, body_end_idx) where body_end_idx
    is the line index of the next same-or-higher-level heading (exclusive),
    or len(lines) if the section runs to EOF. Title match is case-sensitive
    and ignores trailing punctuation/whitespace.
    """
    lines = content.split("\n")
    target = title.strip()
    target_lc = target.lower()
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if not m:
            continue
        if m.group("title").strip().lower() == target_lc:
            level = len(m.group("hashes"))
            end = len(lines)
            for j in range(idx + 1, len(lines)):
                mj = _HEADING_RE.match(lines[j])
                if mj and len(mj.group("hashes")) <= level:
                    end = j
                    break
            return (idx, idx + 1, end)
    return None


def apply_add_section(current: str, enh: Enhancement) -> str:
    """Insert a new section. Position decoded from enh.position."""
    pos = (enh.position or "").strip()
    body = enh.content if enh.content.endswith("\n") else enh.content + "\n"
    if not body.endswith("\n\n"):
        body = body.rstrip("\n") + "\n\n"

    if pos.lower() in {"end_of_document", "end", "bottom"}:
        sep = "" if current.endswith("\n\n") else ("" if current.endswith("\n") else "\n")
        return current + sep + ("\n" if not current.endswith("\n\n") else "") + body

    if pos.lower() == "top":
        # Preserve frontmatter (if any), then insert after it
        if current.startswith("---\n"):
            end_fm = current.find("\n---\n", 4)
            if end_fm != -1:
                cut = end_fm + len("\n---\n")
                return current[:cut] + "\n" + body + current[cut:]
        return body + current

    # `after "Section X"` — find that section and insert after its body
    am = re.match(r'^\s*after\s+["\'](?P<anchor>.+?)["\']?\s*$', pos, re.IGNORECASE)
    if am:
        anchor = am.group("anchor")
        bounds = _find_section_bounds(current, anchor)
        if bounds:
            _, _, end = bounds
            lines = current.split("\n")
            insert = body.rstrip("\n").split("\n")
            lines = lines[:end] + [""] + insert + [""] + lines[end:]
            return "\n".join(lines)
    # Fallback: append at end with a note
    sep = "" if current.endswith("\n\n") else ("" if current.endswith("\n") else "\n")
    return current + sep + ("\n" if not current.endswith("\n\n") else "") + body


def apply_update_section(current: str, enh: Enhancement) -> str:
    """Replace the body of an existing section keyed on section_title.

    If the section isn't found, fall back to add_section semantics so the
    operator still gets the proposed content in the PR.
    """
    bounds = _find_section_bounds(current, enh.section_title)
    if not bounds:
        return apply_add_section(current, enh)
    heading_idx, body_start, end = bounds
    lines = current.split("\n")
    body = enh.content.rstrip("\n").split("\n")
    new_lines = lines[: body_start] + body + [""] + lines[end:]
    return "\n".join(new_lines)


def apply_new_document(enh: Enhancement) -> str:
    """The content block IS the new document — return as-is, ensuring trailing newline."""
    body = enh.content
    if not body.endswith("\n"):
        body += "\n"
    return body


def apply_enhancement(current: str, enh: Enhancement) -> tuple[str, Optional[str]]:
    """Apply one enhancement to one file's content.

    Returns (new_content, error). On `replace_text` (NYI), returns the
    current content unchanged with an explanatory error so the caller
    can surface manual-review-needed in the PR description.
    """
    if enh.action == "add_section":
        return apply_add_section(current, enh), None
    if enh.action == "update_section":
        return apply_update_section(current, enh), None
    if enh.action == "new_document":
        return apply_new_document(enh), None
    if enh.action == "replace_text":
        return current, (
            "replace_text action not yet implemented in apply path — "
            "patch left in PR description for manual application"
        )
    return current, f"unknown action {enh.action!r}"


# ── Grouping ────────────────────────────────────────────────────────────────


def group_by_target(enhancements: list[Enhancement]) -> dict[str, list[Enhancement]]:
    """Group enhancements by their target doc handle so each file gets one
    consolidated commit. Preserves source order within each group."""
    by_target: dict[str, list[Enhancement]] = {}
    for e in enhancements:
        if not e.target:
            continue
        by_target.setdefault(e.target, []).append(e)
    return by_target

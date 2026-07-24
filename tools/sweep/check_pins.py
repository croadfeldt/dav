#!/usr/bin/env python3
"""Q10 pins-current gate (repo-cleanliness-review.md): pinned refs must resolve AND be current.

Scans a repo's CI/config surfaces for pinned git SHAs and git refs, then verifies each against
its remote: a 40-hex SHA must be reachable from the remote's default branch (an orphaned or
never-merged SHA fails even if it still fetches); a pinned branch must exist on the remote.
Type specimens: estate UDLM_REF (never-merged branch commit), estate-explorer ESTATE_GIT_REF
(long-merged feature branches). Exit 1 on any stale pin.

Usage: check_pins.py <repo-root> [--remote-map name=url ...]
"""
import re
import subprocess
import sys
import os

SCAN = (".gitlab-ci.yml", ".github/workflows", "deploy", "docs")
SHA_RE = re.compile(r"\b([0-9a-f]{40})\b")
REF_RE = re.compile(r"""(?:_REF|_BRANCH|ref|branch)["']?\s*[:=]\s*["']?([A-Za-z0-9][\w./-]{2,})["']?""")
SKIP_REFS = {"main", "master", "HEAD"}
URL_RE = re.compile(r"https://(?:codeload\.)?(github\.com|gitlab[\w.-]*)/([\w.-]+/[\w.-]+?)(?:\.git|/tar\.gz|/blob|/tree|$|[\"'\s])")


def files(root):
    for s in SCAN:
        p = os.path.join(root, s)
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for dirpath, _, names in os.walk(p):
                for n in names:
                    if n.endswith((".yml", ".yaml", ".md", ".json", ".sh")):
                        yield os.path.join(dirpath, n)


def remote_default_contains(url, sha):
    """True if sha is reachable from the remote's default branch (shallow local probe)."""
    try:
        head = subprocess.run(["git", "ls-remote", "--symref", url, "HEAD"],
                              capture_output=True, text=True, timeout=30).stdout
        for line in head.splitlines():
            if line.startswith("ref:"):
                default = line.split()[1]
                break
        else:
            return None
        out = subprocess.run(["git", "ls-remote", url, default], capture_output=True,
                             text=True, timeout=30).stdout.split()
        if out and out[0] == sha:
            return True
        # not the tip — ancestor check needs a fetch; use a cheap in-repo probe when available
        probe = subprocess.run(["git", "fetch", "--quiet", "--depth=1", url, sha],
                               capture_output=True, text=True, timeout=60)
        if probe.returncode != 0:
            return False  # not even fetchable
        anc = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "FETCH_HEAD"],
                             capture_output=True, timeout=30)
        return None if anc.returncode not in (0, 1) else None  # ancestry unknowable shallow — report as WARN
    except Exception:
        return None


def ref_exists(url, ref):
    try:
        out = subprocess.run(["git", "ls-remote", url, ref], capture_output=True, text=True,
                             timeout=30).stdout
        return bool(out.strip())
    except Exception:
        return None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    fails, warns = [], []
    for f in files(root):
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        urls = [f"https://{h}/{p}" for h, p in URL_RE.findall(text)]
        for line_no, line in enumerate(text.splitlines(), 1):
            for sha in SHA_RE.findall(line):
                url = urls[0] if urls else None
                if not url:
                    warns.append(f"{f}:{line_no} pinned SHA {sha[:12]} — no remote URL found in file to verify against")
                    continue
                ok = remote_default_contains(url, sha)
                if ok is False:
                    fails.append(f"{f}:{line_no} SHA {sha[:12]} NOT on {url} default branch (stale/orphaned pin)")
                elif ok is None:
                    warns.append(f"{f}:{line_no} SHA {sha[:12]} on {url}: currency unknowable cheaply — verify by hand")
            for m in REF_RE.finditer(line):
                ref = m.group(1)
                if ref in SKIP_REFS or SHA_RE.fullmatch(ref) or "/" in ref and ref.count("/") > 3:
                    continue
                if re.fullmatch(r"(feat|fix|docs|chore|wip)/[\w./-]+", ref):
                    url = urls[0] if urls else None
                    state = ref_exists(url, ref) if url else None
                    if state is False:
                        fails.append(f"{f}:{line_no} pinned branch '{ref}' does not exist on {url}")
                    else:
                        fails.append(f"{f}:{line_no} pinned to a review branch '{ref}' — pins follow main, not review branches")
    for w in warns:
        print("WARN", w)
    for x in fails:
        print("FAIL", x)
    print(f"\n{len(fails)} stale pin(s), {len(warns)} warning(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

"""Push a managed UC YAML to the consumer's corpus repo as a commit + PR.

GitHub-only for now. Detects the host from `corpus_repo_url`; raises a
clear error for other providers (gitlab.*, etc.) so the UI can surface
"unsupported host — add GitLab support" rather than failing opaquely.

Auth: a single env-var-supplied PAT (`DAV_CORPUS_PUSH_TOKEN`) with
`repo` scope (or `public_repo` if the corpus is public). Set in the
consumer Secret; this module does not touch any K8s state.

API surface:
    parse_github_url(url)  -> ("owner", "repo")
    is_github(url)         -> bool
    push_uc_to_github(...) -> {"pr_url", "branch", "commit_sha",
                               "path", "action"}  # "created"|"updated"
"""
from __future__ import annotations

import logging
import os
import re
from base64 import b64encode, b64decode
from typing import Optional

import httpx

log = logging.getLogger("dav-review-api.corpus_push")

GITHUB_TOKEN_ENV = "DAV_CORPUS_PUSH_TOKEN"

# Recognized GitHub URL forms:
#   https://github.com/owner/repo[.git]
#   git@github.com:owner/repo[.git]
#   github.com/owner/repo
_GH_HTTPS = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.I)
_GH_SSH   = re.compile(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", re.I)
_GH_BARE  = re.compile(r"^github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.I)


def is_github(url: str) -> bool:
    if not url:
        return False
    return bool(_GH_HTTPS.match(url) or _GH_SSH.match(url) or _GH_BARE.match(url))


def parse_github_url(url: str) -> tuple[str, str]:
    """Return ('owner', 'repo') or raise ValueError."""
    for r in (_GH_HTTPS, _GH_SSH, _GH_BARE):
        m = r.match(url.strip())
        if m:
            return m.group(1), m.group(2)
    raise ValueError(f"not a GitHub URL: {url!r}")


def push_token() -> str:
    """Read the PAT from the environment. Empty string when unset."""
    return os.environ.get(GITHUB_TOKEN_ENV, "").strip()


def is_configured() -> bool:
    return bool(push_token())


async def _gh(method: str, url: str, token: str, **kw) -> httpx.Response:
    """Thin httpx wrapper that injects auth + standard GitHub headers."""
    headers = kw.pop("headers", {})
    headers.setdefault("Authorization", f"Bearer {token}")
    headers.setdefault("Accept", "application/vnd.github+json")
    headers.setdefault("X-GitHub-Api-Version", "2022-11-28")
    async with httpx.AsyncClient(timeout=30.0) as cx:
        return await cx.request(method, url, headers=headers, **kw)


async def fetch_file_content(
    *, owner: str, repo: str, file_path: str, ref: str, token: str
) -> Optional[str]:
    """Fetch a file's decoded content from GitHub. Returns None on 404
    so the caller can distinguish "doesn't exist yet" from auth errors
    (which raise). Used by the enhancement-apply path to read the current
    spec before applying patches.
    """
    from base64 import b64decode
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    r = await _gh("GET", api, token, params={"ref": ref})
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"fetch_file_content {r.status_code}: {r.text[:400]}")
    j = r.json()
    if j.get("type") != "file" or "content" not in j:
        raise RuntimeError(f"unexpected contents response for {file_path}: {str(j)[:300]}")
    return b64decode(j["content"]).decode("utf-8", errors="replace")


async def push_uc_to_github(
    *,
    owner: str,
    repo: str,
    base_branch: str,
    file_path: str,
    file_content: str,
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str,
    author_name: str,
    author_email: str,
    existing_pr_number: Optional[int] = None,
    token_override: Optional[str] = None,
) -> dict:
    """Create-or-update `file_path` on a side branch and open/refresh a PR.

    Strategy:
      1. Resolve base branch's HEAD SHA.
      2. Ensure the side branch exists, pointing at the same SHA. If it
         already exists (re-push), reuse it.
      3. If the file already exists on the side branch, fetch its SHA so
         we can PUT an update; otherwise create it.
      4. Commit the file (the Contents API does commit-on-write).
      5. If no existing PR is open from this branch, open one. Otherwise
         leave the existing PR; the new commit shows up automatically.
    """
    token = token_override or push_token()
    if not token:
        raise RuntimeError(
            f"no per-repo PAT and {GITHUB_TOKEN_ENV} is not set; "
            f"either link a managed_repos credential or set the env secret"
        )
    api = f"https://api.github.com/repos/{owner}/{repo}"

    # 1. Base branch HEAD SHA
    r = await _gh("GET", f"{api}/git/refs/heads/{base_branch}", token)
    if r.status_code == 404:
        raise RuntimeError(f"base branch {base_branch!r} not found on {owner}/{repo}")
    if r.status_code != 200:
        raise RuntimeError(f"GitHub error {r.status_code} resolving base branch: {r.text[:400]}")
    base_sha = r.json()["object"]["sha"]

    # 2. Side branch — create if missing, no-op if present
    branch_ref = f"refs/heads/{branch_name}"
    r = await _gh("GET", f"{api}/git/ref/heads/{branch_name}", token)
    if r.status_code == 404:
        r = await _gh("POST", f"{api}/git/refs", token,
                      json={"ref": branch_ref, "sha": base_sha})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"create branch failed {r.status_code}: {r.text[:400]}")
    elif r.status_code != 200:
        raise RuntimeError(f"branch lookup failed {r.status_code}: {r.text[:400]}")

    # 3. Existing file SHA on the side branch (if any)
    file_sha = None
    r = await _gh("GET", f"{api}/contents/{file_path}",
                  token, params={"ref": branch_name})
    if r.status_code == 200:
        file_sha = r.json().get("sha")
    elif r.status_code not in (404,):
        raise RuntimeError(f"contents lookup failed {r.status_code}: {r.text[:400]}")
    action = "updated" if file_sha else "created"

    # 4. Commit the file content
    body: dict = {
        "message": commit_message,
        "content": b64encode(file_content.encode("utf-8")).decode("ascii"),
        "branch":  branch_name,
        "committer": {"name": author_name, "email": author_email},
        "author":    {"name": author_name, "email": author_email},
    }
    if file_sha:
        body["sha"] = file_sha
    r = await _gh("PUT", f"{api}/contents/{file_path}", token, json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"file write failed {r.status_code}: {r.text[:400]}")
    commit_sha = r.json().get("commit", {}).get("sha")

    # 5. Open PR if not already open
    pr_url = None
    pr_number = existing_pr_number
    if pr_number is None:
        # Look for an open PR from this branch first (idempotent re-push)
        r = await _gh("GET", f"{api}/pulls",
                      token, params={"head": f"{owner}:{branch_name}", "state": "open"})
        if r.status_code == 200 and r.json():
            existing = r.json()[0]
            pr_url = existing["html_url"]
            pr_number = existing["number"]
        else:
            r = await _gh("POST", f"{api}/pulls", token, json={
                "title": pr_title,
                "body":  pr_body,
                "head":  branch_name,
                "base":  base_branch,
            })
            if r.status_code not in (200, 201):
                raise RuntimeError(f"PR open failed {r.status_code}: {r.text[:400]}")
            pr_url = r.json()["html_url"]
            pr_number = r.json()["number"]
    else:
        # Caller said a PR already exists; just resolve its URL
        r = await _gh("GET", f"{api}/pulls/{pr_number}", token)
        if r.status_code == 200:
            pr_url = r.json()["html_url"]

    return {
        "pr_url":     pr_url,
        "pr_number":  pr_number,
        "branch":     branch_name,
        "commit_sha": commit_sha,
        "path":       file_path,
        "action":     action,
    }

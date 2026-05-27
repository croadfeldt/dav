"""Thin async GitHub REST client for PR comment ingestion (M5+).

Scope: just what the poller / webhook needs — list open PRs, list issue
comments on a PR, list pull-request review comments on a PR. Uses httpx
(already in the review-api deps).

All functions take an explicit `token` parameter. Per ADR-004, tokens are
per-repo and stored Fernet-encrypted in managed_repos.github_pat_encrypted;
callers (poller, webhook self-setup helpers, etc.) fetch via
repos.get_repo_secrets() and pass through here.

Anonymous mode (`token=None`) is supported but rate-limited to 60 req/hour
per source IP — fine for one-shot tests, hopeless for periodic polling.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger("dav-review-api.github_client")

GITHUB_API = "https://api.github.com"
USER_AGENT = "dav-review-api/0.9 (+https://github.com/croadfeldt/dav)"


class GitHubError(Exception):
    """Wraps an HTTP error response from GitHub with useful context."""

    def __init__(self, status: int, message: str, url: str):
        super().__init__(f"GitHub {status} {url}: {message}")
        self.status = status
        self.message = message
        self.url = url


def _headers(token: Optional[str]) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        # Per GitHub docs: pin the API version to avoid surprise breakages
        # when GitHub ships a new default.
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def parse_owner_repo(repo_url: str) -> Optional[tuple[str, str]]:
    """Best-effort parse of a github repo_url into (owner, repo).

    Accepts:
        https://github.com/owner/repo[.git]
        https://github.com/owner/repo/
        git@github.com:owner/repo[.git]

    Returns None for non-GitHub URLs.
    """
    if not repo_url:
        return None
    # SSH form
    m = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url)
    if m:
        return m.group(1), m.group(2)
    # HTTPS form
    parsed = urlparse(repo_url)
    if parsed.netloc != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


async def _get_paginated(
    client: httpx.AsyncClient, path: str, *, token: Optional[str],
    params: Optional[dict] = None,
    page_size: int = 100, max_pages: int = 20,
) -> list[dict]:
    """Walk GitHub's pagination (Link rel="next") until exhausted or
    max_pages reached. Returns the flat concatenation of all pages.

    max_pages is a safety cap. With page_size=100 and max_pages=20, that's
    2000 items. PRs with >2000 comments are unheard of; if a repo somehow
    hits this, the poller logs a warning and processes what it got.
    """
    params = dict(params or {})
    params.setdefault("per_page", page_size)
    url = f"{GITHUB_API}{path}"
    out: list[dict] = []
    pages = 0
    while url and pages < max_pages:
        resp = await client.get(url, params=params if pages == 0 else None,
                                headers=_headers(token), timeout=30.0)
        pages += 1
        if resp.status_code != 200:
            try:
                body = resp.json()
                msg = body.get("message") or str(body)[:200]
            except Exception:
                msg = resp.text[:200]
            raise GitHubError(resp.status_code, msg, url)
        out.extend(resp.json())
        # Parse Link header for rel="next"
        next_url = None
        link = resp.headers.get("Link", "")
        for part in link.split(","):
            part = part.strip()
            if not part:
                continue
            m = re.match(r'<([^>]+)>;\s*rel="next"', part)
            if m:
                next_url = m.group(1)
                break
        url = next_url
    if pages >= max_pages and url:
        log.warning(
            "github: pagination cap (%d pages) reached for %s; some items "
            "may not have been fetched", max_pages, path,
        )
    return out


async def list_open_pull_requests(
    client: httpx.AsyncClient, owner: str, repo: str, *,
    token: Optional[str] = None,
) -> list[dict]:
    """List open PRs for a repo. Returns raw GitHub PR objects."""
    return await _get_paginated(
        client, f"/repos/{owner}/{repo}/pulls", token=token,
        params={"state": "open", "sort": "updated", "direction": "desc"},
    )


async def list_issue_comments(
    client: httpx.AsyncClient, owner: str, repo: str, pr_number: int, *,
    token: Optional[str] = None,
) -> list[dict]:
    """List issue-style comments on a PR (the main PR thread)."""
    return await _get_paginated(
        client, f"/repos/{owner}/{repo}/issues/{pr_number}/comments", token=token,
        params={"sort": "updated", "direction": "desc"},
    )


async def list_review_comments(
    client: httpx.AsyncClient, owner: str, repo: str, pr_number: int, *,
    token: Optional[str] = None,
) -> list[dict]:
    """List per-line review comments on a PR."""
    return await _get_paginated(
        client, f"/repos/{owner}/{repo}/pulls/{pr_number}/comments", token=token,
        params={"sort": "updated", "direction": "desc"},
    )

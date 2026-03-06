"""GitHub issues fetcher for repo overview.

Fetches open issues from GitHub API, categorizes by labels.
Works with public repos (no token) or authenticated (higher rate limit).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
import httpx

from rtalk.clone import is_github_url, parse_github_url


@dataclass
class Issue:
    number: int
    title: str
    state: str
    html_url: str
    labels: list[str]
    created_at: str
    body_preview: str = ""
    updated_at: str = ""
    comments: int = 0


def _get_owner_repo(repo_input: str) -> tuple[str, str]:
    """Resolve repo input to (owner, repo)."""
    repo_input = repo_input.strip()
    if is_github_url(repo_input):
        owner, repo, _ = parse_github_url(repo_input)
        return owner, repo
    if "/" in repo_input and not os.path.isdir(repo_input):
        parts = repo_input.split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1].replace(".git", "")
    if os.path.isdir(repo_input):
        try:
            result = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=repo_input,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                url = result.stdout.strip()
                if "github.com" in url:
                    owner, repo, _ = parse_github_url(
                        url.replace("git@github.com:", "https://github.com/").replace(".git", "")
                    )
                    return owner, repo
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    raise ValueError(f"Cannot resolve GitHub owner/repo from: {repo_input}")


def fetch_issues(
    repo_input: str,
    per_page: int = 5,
    page: int = 1,
    state: str = "open",
    github_token: str | None = None,
    labels: str | None = None,
    sort: str = "created",
    direction: str = "desc",
) -> tuple[list[Issue], dict[str, list[Issue]], bool]:
    """Fetch issues from GitHub API.

    Returns (all_issues, by_label, has_more).
    by_label: label_name -> list of issues (each issue can appear under multiple labels).
    """
    owner, repo = _get_owner_repo(repo_input)
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token and github_token.strip():
        headers["Authorization"] = f"Bearer {github_token.strip()}"

    params: dict[str, str | int] = {
        "state": state,
        "per_page": per_page,
        "page": page,
        "sort": sort,
        "direction": direction,
    }
    if labels and labels.strip():
        params["labels"] = labels.strip()
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, headers=headers, params=params)
        if resp.status_code == 404:
            raise ValueError(f"Repo not found: {owner}/{repo}")
        if resp.status_code == 401:
            raise ValueError("Invalid or expired GitHub token")
        if resp.status_code != 200:
            raise ValueError(f"GitHub API error: {resp.status_code} - {resp.text[:200]}")
        data = resp.json()

    issues: list[Issue] = []
    for item in data:
        if "pull_request" in item:
            continue
        labels = [lb.get("name", "") for lb in item.get("labels", []) if lb.get("name")]
        body = item.get("body") or ""
        issues.append(
            Issue(
                number=item["number"],
                title=item.get("title", ""),
                state=item.get("state", "open"),
                html_url=item.get("html_url", ""),
                labels=labels,
                created_at=item.get("created_at", ""),
                body_preview=body[:150] + "..." if len(body) > 150 else body,
                updated_at=item.get("updated_at", ""),
                comments=item.get("comments", 0),
            )
        )

    by_label: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.labels:
            for lb in issue.labels:
                if lb not in by_label:
                    by_label[lb] = []
                by_label[lb].append(issue)
        else:
            if "_unlabeled" not in by_label:
                by_label["_unlabeled"] = []
            by_label["_unlabeled"].append(issue)

    has_more = len(data) >= per_page
    return issues, by_label, has_more

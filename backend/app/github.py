"""GitHub access: listing and downloading Markdown files from your repositories."""

from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

API_URL = "https://api.github.com"
FILE_TYPES = {".md": "md", ".markdown": "md"}


def get_client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
        follow_redirects=True,
    )


def _get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    resp = client.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def list_repos(client: httpx.Client, include_forks: bool = False) -> Iterator[dict]:
    """Yield the public and private repositories owned by the token's user."""
    page = 1
    while True:
        repos = _get(
            client,
            "/user/repos",
            params={"affiliation": "owner", "visibility": "all", "per_page": 100, "page": page},
        ).json()
        for repo in repos:
            if include_forks or not repo["fork"]:
                yield repo
        if len(repos) < 100:
            return
        page += 1


def _tree(client: httpx.Client, repo: str, ref: str, prefix: str = "") -> Iterator[dict]:
    """Yield a tree's blobs with full paths."""
    url = f"/repos/{repo}/git/trees/{ref}"
    resp = client.get(url, params={"recursive": 1})
    if resp.status_code == 409:  # empty repository
        return
    resp.raise_for_status()
    tree = resp.json()
    if tree["truncated"]:
        # Too large for one response, so walk it one directory at a time instead.
        for entry in _get(client, url).json()["tree"]:
            if entry["type"] == "tree":
                yield from _tree(client, repo, entry["sha"], f"{prefix}{entry['path']}/")
            elif entry["type"] == "blob":
                yield {**entry, "path": prefix + entry["path"]}
        return
    for entry in tree["tree"]:
        if entry["type"] == "blob":
            yield {**entry, "path": prefix + entry["path"]}


def list_files(client: httpx.Client, include_forks: bool = False) -> Iterator[dict]:
    """Yield the Markdown files on the default branch of each repository."""
    for repo in list_repos(client, include_forks):
        branch = repo["default_branch"]
        for entry in _tree(client, repo["full_name"], branch):
            suffix = PurePosixPath(entry["path"]).suffix.lower()
            if suffix not in FILE_TYPES:
                continue
            yield {
                "id": f"github:{repo['full_name']}:{entry['path']}",
                "repo": repo["full_name"],
                "branch": branch,
                "path": entry["path"],
                "sha": entry["sha"],
                "size": entry.get("size"),
                "file_type": FILE_TYPES[suffix],
                "name": f"{repo['name']}/{entry['path']}",
                "url": f"{repo['html_url']}/blob/{quote(branch)}/{quote(entry['path'])}",
            }


def last_commit(client: httpx.Client, file: dict) -> dict:
    """Return the most recent commit that touched the file."""
    commits = _get(
        client,
        f"/repos/{file['repo']}/commits",
        params={"path": file["path"], "sha": file["branch"], "per_page": 1},
    ).json()
    return commits[0]["commit"]


def download(client: httpx.Client, file: dict) -> bytes:
    return _get(
        client,
        f"/repos/{file['repo']}/git/blobs/{file['sha']}",
        headers={"Accept": "application/vnd.github.raw+json"},
    ).content

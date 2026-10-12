import httpx

from app import github

REPOS = [
    {
        "name": "notes",
        "full_name": "me/notes",
        "fork": False,
        "default_branch": "main",
        "html_url": "https://github.com/me/notes",
    },
    {
        "name": "empty",
        "full_name": "me/empty",
        "fork": False,
        "default_branch": "main",
        "html_url": "https://github.com/me/empty",
    },
    {
        "name": "forked",
        "full_name": "me/forked",
        "fork": True,
        "default_branch": "main",
        "html_url": "https://github.com/me/forked",
    },
]


def blob(path, sha):
    return {"path": path, "type": "blob", "sha": sha, "size": 7}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/user/repos":
        assert request.url.params["affiliation"] == "owner"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(200, json=REPOS)
    if path == "/repos/me/empty/git/trees/main":
        return httpx.Response(409, json={"message": "Git Repository is empty."})
    if path == "/repos/me/notes/git/trees/main":
        return httpx.Response(
            200,
            json={
                "truncated": False,
                "tree": [
                    blob("README.md", "a1"),
                    blob("main.py", "a2"),
                    {"path": "docs", "type": "tree", "sha": "t1"},
                    blob("docs/How To.MD", "a3"),
                    blob("docs/guide.adoc", "a4"),
                ],
            },
        )
    if path == "/repos/me/forked/git/trees/main":
        return httpx.Response(200, json={"truncated": False, "tree": [blob("README.md", "f1")]})
    if path == "/repos/me/big/git/trees/main":
        if "recursive" in request.url.params:
            return httpx.Response(200, json={"truncated": True, "tree": []})
        return httpx.Response(
            200,
            json={
                "truncated": False,
                "tree": [blob("README.md", "b1"), {"path": "docs", "type": "tree", "sha": "t2"}],
            },
        )
    if path == "/repos/me/big/git/trees/t2":
        return httpx.Response(200, json={"truncated": False, "tree": [blob("guide.md", "b2")]})
    if path == "/repos/me/notes/git/blobs/a1":
        assert request.headers["Accept"] == "application/vnd.github.raw+json"
        return httpx.Response(200, content=b"# Notes")
    return httpx.Response(404)


def client():
    real = github.get_client("secret")
    return httpx.Client(
        base_url=real.base_url, headers=real.headers, transport=httpx.MockTransport(handler)
    )


def test_list_files_keeps_markdown_from_owned_non_fork_repos():
    files = list(github.list_files(client()))
    assert [f["id"] for f in files] == [
        "github:me/notes:README.md",
        "github:me/notes:docs/How To.MD",
    ]
    assert files[1] == {
        "id": "github:me/notes:docs/How To.MD",
        "repo": "me/notes",
        "branch": "main",
        "path": "docs/How To.MD",
        "sha": "a3",
        "size": 7,
        "file_type": "md",
        "name": "notes/docs/How To.MD",
        "url": "https://github.com/me/notes/blob/main/docs/How%20To.MD",
    }


def test_list_files_can_include_forks():
    ids = [f["id"] for f in github.list_files(client(), include_forks=True)]
    assert "github:me/forked:README.md" in ids


def test_truncated_tree_is_walked_by_directory():
    paths = [entry["path"] for entry in github._tree(client(), "me/big", "main")]
    assert paths == ["README.md", "docs/guide.md"]


def test_download_returns_raw_bytes():
    assert github.download(client(), {"repo": "me/notes", "sha": "a1"}) == b"# Notes"

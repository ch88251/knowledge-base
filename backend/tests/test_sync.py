from unittest.mock import MagicMock

import httpx
import pytest

from app import drive, sync
from app.config import Settings


def drive_file(file_id, modified, size="5"):
    return {
        "id": file_id,
        "name": f"{file_id}.txt",
        "mimeType": "text/plain",
        "createdTime": "2026-01-01T00:00:00.000Z",
        "modifiedTime": modified,
        "size": size,
        "webViewLink": f"https://drive.google.com/file/d/{file_id}",
        "owners": [{"displayName": "Me"}],
    }


def github_file(path, sha, size=5):
    return {
        "id": f"github:me/notes:{path}",
        "repo": "me/notes",
        "branch": "main",
        "path": path,
        "sha": sha,
        "size": size,
        "file_type": "md",
        "name": f"notes/{path}",
        "url": f"https://github.com/me/notes/blob/main/{path}",
    }


def fake_es(monkeypatch, hits):
    es = MagicMock()
    es.indices.exists.return_value = True
    monkeypatch.setattr(sync.helpers, "scan", lambda *a, **k: hits)
    return es


def settings(tmp_path, drive_connected=False, **kwargs):
    token_file = tmp_path / "token.json"
    if drive_connected:
        token_file.write_text("{}")
    return Settings(google_token_file=token_file, max_file_bytes=100, _env_file=None, **kwargs)


def patch_drive(monkeypatch, files):
    monkeypatch.setattr(sync.drive, "get_service", lambda token_file: object())
    monkeypatch.setattr(sync.drive, "list_files", lambda service, folder: iter(files))
    monkeypatch.setattr(sync.drive, "download", lambda service, file: b"hello")


def patch_github(monkeypatch, files):
    monkeypatch.setattr(sync.github, "list_files", lambda client, include_forks: iter(files))
    monkeypatch.setattr(sync.github, "download", lambda client, file: b"# Notes")
    monkeypatch.setattr(
        sync.github,
        "last_commit",
        lambda client, file: {
            "author": {"name": "Me"},
            "committer": {"date": "2026-03-01T00:00:00Z"},
        },
    )


def test_sync_indexes_changed_skips_unchanged_and_deletes_removed(monkeypatch, tmp_path):
    es = fake_es(
        monkeypatch,
        [
            {"_id": "same", "_source": {"source": "drive", "version": "2026-02-01T00:00:00.000Z"}},
            {"_id": "changed", "_source": {"source": "drive", "version": "2026-02-01T00:00:00.000Z"}},
            {"_id": "gone", "_source": {"source": "drive", "version": "2026-02-01T00:00:00.000Z"}},
            # Indexed before documents recorded a source and version.
            {"_id": "legacy", "_source": {}},
            {"_id": "legacy-gone", "_source": {}},
        ],
    )
    patch_drive(
        monkeypatch,
        [
            drive_file("same", "2026-02-01T00:00:00.000Z"),
            drive_file("changed", "2026-03-01T00:00:00.000Z"),
            drive_file("new", "2026-03-01T00:00:00.000Z"),
            drive_file("huge", "2026-03-01T00:00:00.000Z", size="999"),
            drive_file("legacy", "2026-02-01T00:00:00.000Z"),
        ],
    )

    stats, errors = sync.run_sync(es, settings(tmp_path, drive_connected=True))

    assert errors == []
    assert stats.as_dict() == {
        "indexed": 4,
        "unchanged": 1,
        "deleted": 2,
        "failed": 0,
        "without_content": 1,
    }
    indexed = {c.kwargs["id"]: c.kwargs["document"] for c in es.index.call_args_list}
    assert set(indexed) == {"changed", "new", "huge", "legacy"}
    assert indexed["new"]["content"] == "hello"
    assert indexed["new"]["owners"] == ["Me"]
    assert indexed["new"]["source"] == "drive"
    assert indexed["new"]["version"] == "2026-03-01T00:00:00.000Z"
    assert indexed["huge"]["content"] == ""
    assert {c.kwargs["id"] for c in es.delete.call_args_list} == {"gone", "legacy-gone"}


def test_sync_github_leaves_drive_documents_alone(monkeypatch, tmp_path):
    es = fake_es(
        monkeypatch,
        [
            {"_id": "drive-doc", "_source": {"source": "drive", "version": "v"}},
            {"_id": "github:me/notes:same.md", "_source": {"source": "github", "version": "aaa"}},
            {"_id": "github:me/notes:changed.md", "_source": {"source": "github", "version": "aaa"}},
            {"_id": "github:me/notes:gone.md", "_source": {"source": "github", "version": "aaa"}},
        ],
    )
    patch_github(
        monkeypatch,
        [github_file("same.md", "aaa"), github_file("changed.md", "bbb"), github_file("new.md", "ccc")],
    )

    stats, errors = sync.run_sync(es, settings(tmp_path, github_token="t"))

    assert errors == []
    assert stats.as_dict() == {
        "indexed": 2,
        "unchanged": 1,
        "deleted": 1,
        "failed": 0,
        "without_content": 0,
    }
    indexed = {c.kwargs["id"]: c.kwargs["document"] for c in es.index.call_args_list}
    assert set(indexed) == {"github:me/notes:changed.md", "github:me/notes:new.md"}
    assert indexed["github:me/notes:new.md"] == {
        "title": "notes/new.md",
        "mime_type": "text/markdown",
        "owners": ["Me"],
        "url": "https://github.com/me/notes/blob/main/new.md",
        "modified_time": "2026-03-01T00:00:00Z",
        "source": "github",
        "version": "ccc",
        "content": "# Notes",
        "file_type": "md",
        "size": 5,
        "indexed_at": indexed["github:me/notes:new.md"]["indexed_at"],
    }
    es.delete.assert_called_once_with(index="knowledge-base", id="github:me/notes:gone.md")


def test_failing_source_is_reported_and_deletes_nothing(monkeypatch, tmp_path):
    es = fake_es(
        monkeypatch,
        [{"_id": "github:me/notes:old.md", "_source": {"source": "github", "version": "aaa"}}],
    )
    patch_drive(monkeypatch, [drive_file("new", "2026-03-01T00:00:00.000Z")])

    def broken_listing(client, include_forks):
        yield github_file("new.md", "ccc")
        raise httpx.ConnectError("GitHub is down")

    patch_github(monkeypatch, [])
    monkeypatch.setattr(sync.github, "list_files", broken_listing)

    updates = []
    stats, errors = sync.run_sync(
        es, settings(tmp_path, drive_connected=True, github_token="t"), updates.append
    )

    assert errors == ["GitHub: GitHub is down"]
    assert stats.indexed == 1
    es.delete.assert_not_called()
    # The failed source is left out of the total.
    assert updates[-1] == {
        "phase": "syncing",
        "source": "Google Drive",
        "current": "new.txt",
        "done": 0,
        "total": 1,
    }


def test_progress_lists_every_source_before_counting_through_the_total(monkeypatch, tmp_path):
    es = fake_es(monkeypatch, [])
    patch_drive(monkeypatch, [drive_file("a", "v1"), drive_file("b", "v1")])
    patch_github(monkeypatch, [github_file("c.md", "ccc")])

    updates = []
    sync.run_sync(es, settings(tmp_path, drive_connected=True, github_token="t"), updates.append)

    assert updates == [
        {"phase": "listing", "source": "Google Drive", "found": 0},
        {"phase": "listing", "source": "Google Drive", "found": 1},
        {"phase": "listing", "source": "Google Drive", "found": 2},
        {"phase": "listing", "source": "GitHub", "found": 2},
        {"phase": "listing", "source": "GitHub", "found": 3},
        {"phase": "syncing", "source": "Google Drive", "current": "a.txt", "done": 0, "total": 3},
        {"phase": "syncing", "source": "Google Drive", "current": "b.txt", "done": 1, "total": 3},
        {"phase": "syncing", "source": "GitHub", "current": "notes/c.md", "done": 2, "total": 3},
    ]


def test_sync_without_a_connected_source_fails(monkeypatch, tmp_path):
    with pytest.raises(drive.NotAuthorized):
        sync.run_sync(fake_es(monkeypatch, []), settings(tmp_path))

from unittest.mock import MagicMock

from app import sync
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


def test_sync_indexes_changed_skips_unchanged_and_deletes_removed(monkeypatch):
    es = MagicMock()
    es.indices.exists.return_value = True
    monkeypatch.setattr(
        sync.helpers,
        "scan",
        lambda *a, **k: [
            {"_id": "same", "_source": {"modified_time": "2026-02-01T00:00:00.000Z"}},
            {"_id": "changed", "_source": {"modified_time": "2026-02-01T00:00:00.000Z"}},
            {"_id": "gone", "_source": {"modified_time": "2026-02-01T00:00:00.000Z"}},
        ],
    )
    files = [
        drive_file("same", "2026-02-01T00:00:00.000Z"),
        drive_file("changed", "2026-03-01T00:00:00.000Z"),
        drive_file("new", "2026-03-01T00:00:00.000Z"),
        drive_file("huge", "2026-03-01T00:00:00.000Z", size="999"),
    ]
    monkeypatch.setattr(sync.drive, "list_files", lambda service, folder: iter(files))
    monkeypatch.setattr(sync.drive, "download", lambda service, file: b"hello")

    stats = sync.run_sync(es, object(), Settings(max_file_bytes=100, _env_file=None))

    assert stats.as_dict() == {
        "indexed": 3,
        "unchanged": 1,
        "deleted": 1,
        "failed": 0,
        "without_content": 1,
    }
    indexed = {c.kwargs["id"]: c.kwargs["document"] for c in es.index.call_args_list}
    assert set(indexed) == {"changed", "new", "huge"}
    assert indexed["new"]["content"] == "hello"
    assert indexed["new"]["owners"] == ["Me"]
    assert indexed["huge"]["content"] == ""
    es.delete.assert_called_once_with(index="knowledge-base", id="gone")

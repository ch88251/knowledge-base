"""Mirror Google Drive documents into the Elasticsearch index."""

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from elasticsearch import Elasticsearch, helpers
from googleapiclient.errors import HttpError

from . import drive
from .config import Settings
from .extract import extract_text
from .search import ensure_index

log = logging.getLogger(__name__)


@dataclass
class SyncStats:
    indexed: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    without_content: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def run_sync(es: Elasticsearch, service, settings: Settings) -> SyncStats:
    index = settings.index_name
    ensure_index(es, index)
    stats = SyncStats()

    existing = {
        hit["_id"]: hit["_source"].get("modified_time")
        for hit in helpers.scan(es, index=index, query={"_source": ["modified_time"]})
    }
    seen: set[str] = set()

    for file in drive.list_files(service, settings.drive_folder_id):
        file_id = file["id"]
        seen.add(file_id)
        if existing.get(file_id) == file["modifiedTime"]:
            stats.unchanged += 1
            continue

        file_type = drive.FILE_TYPES[file["mimeType"]]
        size = int(file["size"]) if "size" in file else None
        content = ""
        if size is not None and size > settings.max_file_bytes:
            log.warning("%s is too large (%d bytes); indexing metadata only", file["name"], size)
            stats.without_content += 1
        else:
            try:
                data = drive.download(service, file)
            except HttpError:
                # Not indexed, so the next sync retries it.
                log.exception("Download failed for %s", file["name"])
                stats.failed += 1
                continue
            try:
                content = extract_text(file_type, data)
            except Exception:
                log.exception("Text extraction failed for %s; indexing metadata only", file["name"])
                stats.without_content += 1

        es.index(
            index=index,
            id=file_id,
            document={
                "title": file["name"],
                "content": content,
                "file_type": file_type,
                "mime_type": file["mimeType"],
                "owners": [o["displayName"] for o in file.get("owners", [])],
                "size": size,
                "url": file.get("webViewLink"),
                "created_time": file["createdTime"],
                "modified_time": file["modifiedTime"],
                "indexed_at": datetime.now(UTC).isoformat(),
            },
        )
        stats.indexed += 1

    # Only reached when the Drive listing completed, so a partial listing never deletes.
    for file_id in existing.keys() - seen:
        es.delete(index=index, id=file_id)
        stats.deleted += 1

    es.indices.refresh(index=index)
    return stats

"""Mirror Google Drive and GitHub documents into the Elasticsearch index."""

import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx
from elasticsearch import Elasticsearch, helpers
from googleapiclient.errors import HttpError

from . import drive, github
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


@dataclass
class Item:
    """One document in a source, with its content and metadata fetched on demand."""

    id: str
    # Changes whenever the document does; unchanged documents are skipped.
    version: str
    name: str
    file_type: str
    size: int | None
    download: Callable[[], bytes]
    metadata: Callable[[], dict]


def _drive_items(settings: Settings, resources: ExitStack) -> Iterator[Item]:
    service = drive.get_service(settings.google_token_file)
    for file in drive.list_files(service, settings.drive_folder_id):
        yield Item(
            id=file["id"],
            version=file["modifiedTime"],
            name=file["name"],
            file_type=drive.FILE_TYPES[file["mimeType"]],
            size=int(file["size"]) if "size" in file else None,
            download=lambda file=file: drive.download(service, file),
            metadata=lambda file=file: {
                "title": file["name"],
                "mime_type": file["mimeType"],
                "owners": [o["displayName"] for o in file.get("owners", [])],
                "url": file.get("webViewLink"),
                "created_time": file["createdTime"],
                "modified_time": file["modifiedTime"],
            },
        )


def _github_metadata(client: httpx.Client, file: dict) -> dict:
    commit = github.last_commit(client, file)
    return {
        "title": file["name"],
        "mime_type": "text/markdown",
        "owners": [commit["author"]["name"]],
        "url": file["url"],
        "modified_time": commit["committer"]["date"],
    }


def _github_items(settings: Settings, resources: ExitStack) -> Iterator[Item]:
    client = resources.enter_context(github.get_client(settings.github_token))
    for file in github.list_files(client, settings.github_include_forks):
        yield Item(
            id=file["id"],
            version=file["sha"],
            name=file["name"],
            file_type=file["file_type"],
            size=file["size"],
            download=lambda file=file: github.download(client, file),
            metadata=lambda file=file: _github_metadata(client, file),
        )


def _sync_source(
    es: Elasticsearch,
    index: str,
    source: str,
    existing: dict[str, str | None],
    items: Iterable[Item],
    max_file_bytes: int,
    stats: SyncStats,
    on_item: Callable[[Item], None],
) -> None:
    seen: set[str] = set()

    for item in items:
        on_item(item)
        seen.add(item.id)
        if existing.get(item.id) == item.version:
            stats.unchanged += 1
            continue

        too_large = item.size is not None and item.size > max_file_bytes
        try:
            metadata = item.metadata()
            data = None if too_large else item.download()
        except (HttpError, httpx.HTTPError):
            # Not indexed, so the next sync retries it.
            log.exception("Download failed for %s", item.name)
            stats.failed += 1
            continue

        content = ""
        if data is None:
            log.warning("%s is too large (%d bytes); indexing metadata only", item.name, item.size)
            stats.without_content += 1
        else:
            try:
                content = extract_text(item.file_type, data)
            except Exception:
                log.exception("Text extraction failed for %s; indexing metadata only", item.name)
                stats.without_content += 1

        es.index(
            index=index,
            id=item.id,
            document={
                **metadata,
                "source": source,
                "version": item.version,
                "content": content,
                "file_type": item.file_type,
                "size": item.size,
                "indexed_at": datetime.now(UTC).isoformat(),
            },
        )
        stats.indexed += 1

    for item_id in existing.keys() - seen:
        es.delete(index=index, id=item_id)
        stats.deleted += 1


def run_sync(
    es: Elasticsearch, settings: Settings, progress: Callable[[dict], None] = lambda p: None
) -> tuple[SyncStats, list[str]]:
    """Sync every connected source; a failing source is reported without stopping the others.

    `progress` is called with the current state as the sync advances.
    """
    sources: list[tuple[str, str, Callable[[Settings, ExitStack], Iterator[Item]]]] = []
    if settings.google_token_file.exists():
        sources.append(("drive", "Google Drive", _drive_items))
    if settings.github_token:
        sources.append(("github", "GitHub", _github_items))
    if not sources:
        raise drive.NotAuthorized(
            "No source is connected. Run `python -m app.cli auth` for Google Drive "
            "or set KB_GITHUB_TOKEN for GitHub."
        )

    index = settings.index_name
    ensure_index(es, index)
    stats = SyncStats()
    errors: list[str] = []

    existing: dict[str, dict[str, str | None]] = {"drive": {}, "github": {}}
    for hit in helpers.scan(es, index=index, query={"_source": ["source", "version"]}):
        # Documents indexed before GitHub support have no source and came from Drive.
        source = hit["_source"].get("source", "drive")
        existing[source][hit["_id"]] = hit["_source"].get("version")

    with ExitStack() as resources:
        # List every source up front so progress has an exact total. A source whose
        # listing fails is skipped entirely, so a partial listing never deletes.
        listed: list[tuple[str, str, list[Item]]] = []
        found = 0
        for source, label, list_items in sources:
            progress({"phase": "listing", "source": label, "found": found})
            items: list[Item] = []
            try:
                for item in list_items(settings, resources):
                    items.append(item)
                    progress({"phase": "listing", "source": label, "found": found + len(items)})
            except Exception as exc:
                log.exception("%s listing failed", label)
                errors.append(f"{label}: {str(exc) or type(exc).__name__}")
                continue
            found += len(items)
            listed.append((source, label, items))

        done = 0

        def on_item(item: Item) -> None:
            nonlocal done
            progress(
                {
                    "phase": "syncing",
                    "source": label,
                    "current": item.name,
                    "done": done,
                    "total": found,
                }
            )
            done += 1

        for source, label, items in listed:
            try:
                _sync_source(
                    es, index, source, existing[source], items, settings.max_file_bytes, stats, on_item
                )
            except Exception as exc:
                log.exception("%s sync failed", label)
                errors.append(f"{label}: {str(exc) or type(exc).__name__}")

    es.indices.refresh(index=index)
    return stats, errors

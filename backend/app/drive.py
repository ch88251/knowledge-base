"""Google Drive access: OAuth, listing and downloading documents."""

import io
from collections.abc import Iterator
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

FOLDER = "application/vnd.google-apps.folder"
GOOGLE_DOC = "application/vnd.google-apps.document"
FILE_TYPES = {
    "text/plain": "txt",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    GOOGLE_DOC: "gdoc",
}

_FIELDS = (
    "nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size, "
    "webViewLink, owners(displayName))"
)
_TYPE_CLAUSE = " or ".join(f"mimeType = '{m}'" for m in FILE_TYPES)


class NotAuthorized(Exception):
    """No usable Google token; run `python -m app.cli auth`."""


def authorize(credentials_file: Path, token_file: Path, port: int) -> None:
    """Run the interactive OAuth consent flow and save the token."""
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    # Bind on all interfaces so the redirect to localhost reaches us inside Docker.
    creds = flow.run_local_server(bind_addr="0.0.0.0", port=port, open_browser=False)
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)


def get_service(token_file: Path):
    if not token_file.exists():
        raise NotAuthorized("Google Drive is not connected. Run: python -m app.cli auth")
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise NotAuthorized("Google token expired. Run: python -m app.cli auth")
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list(service, query: str) -> Iterator[dict]:
    page_token = None
    while True:
        resp = (
            service.files()
            .list(q=query, fields=_FIELDS, pageSize=1000, pageToken=page_token)
            .execute()
        )
        yield from resp.get("files", [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def list_files(service, folder_id: str | None = None) -> Iterator[dict]:
    """Yield supported documents: the whole Drive, or one folder recursively."""
    if not folder_id:
        yield from _list(service, f"trashed = false and ({_TYPE_CLAUSE})")
        return

    seen: set[str] = set()
    pending = [folder_id]
    while pending:
        parent = pending.pop()
        query = (
            f"'{parent}' in parents and trashed = false "
            f"and (mimeType = '{FOLDER}' or {_TYPE_CLAUSE})"
        )
        for item in _list(service, query):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            if item["mimeType"] == FOLDER:
                pending.append(item["id"])
            else:
                yield item


def download(service, file: dict) -> bytes:
    if file["mimeType"] == GOOGLE_DOC:
        request = service.files().export_media(fileId=file["id"], mimeType="text/plain")
    else:
        request = service.files().get_media(fileId=file["id"])
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()

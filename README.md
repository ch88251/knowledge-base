# Knowledge Base

Full-text search over the documents in your Google Drive. A sync job reads each
document's metadata and text into Elasticsearch; the files themselves stay in Drive
and search results link back to them.

## User Interface

![User Interface](docs/image.png)

- **Backend**: Python, FastAPI (`backend/`)
- **Frontend**: React + Vite, served by nginx (`frontend/`)
- **Search**: Elasticsearch 8, with Kibana for inspecting the index

Supported formats: `.txt`, `.pdf`, `.docx`, and native Google Docs. Legacy `.doc`
files and scanned PDFs without a text layer are not searchable by content.

## Setup

### 1. Create Google OAuth credentials (one time)

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project
   and enable the **Google Drive API**.
2. Configure the OAuth consent screen and add your Google account as a test user.
3. Create an **OAuth client ID** of type **Desktop app**, download the JSON, and save
   it as `secrets/credentials.json`.

### 2. Start the stack

```bash
docker compose up -d --build
```

| Service       | URL                        |
| ------------- | -------------------------- |
| Search app    | http://localhost:3000      |
| API docs      | http://localhost:8000/docs |
| Kibana        | http://localhost:5601      |
| Elasticsearch | http://localhost:9200      |

### 3. Connect Google Drive (one time)

```bash
docker compose run --rm -p 8090:8090 backend python -m app.cli auth
```

Open the printed URL in your browser and approve read-only access. The token is saved
to `secrets/token.json` and refreshed automatically afterwards.

### 4. Index your documents

Click **Sync from Google Drive** in the app, or run:

```bash
docker compose exec backend python -m app.cli sync
```

Sync is incremental: unchanged files are skipped, and files removed from Drive are
removed from the index. Run it again whenever you want to pick up changes.

To index a single folder (and its subfolders) instead of the whole Drive, copy
`.env.example` to `.env` and set `KB_DRIVE_FOLDER_ID` to the ID from the folder's URL.

## Searching

- **Text**: all words must match by default. Use `"quotes"` for an exact phrase,
  `a | b` for either term, `-word` to exclude, and `prefix*` for prefix matches.
- **Date range** (optional): filters on the document's last-modified date, inclusive
  on both ends. Either end can be left empty.

Leaving the text empty lists documents newest first.

## Development without Docker

```bash
# backend (expects Elasticsearch on localhost:9200)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/uvicorn app.main:app --reload

# frontend (proxies /api to localhost:8000)
cd frontend
npm install && npm run dev
```

Backend settings are environment variables prefixed with `KB_`; see
`backend/app/config.py`.

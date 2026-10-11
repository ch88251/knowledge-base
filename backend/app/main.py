import logging
import threading
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from zoneinfo import available_timezones

from elastic_transport import ConnectionError as ESConnectionError
from elasticsearch import Elasticsearch
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import drive
from .config import get_settings
from .search import search
from .sync import run_sync

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_sync_lock = threading.Lock()
_sync_state: dict = {"running": False, "last_finished": None, "last_result": None, "last_error": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.es = Elasticsearch(get_settings().elasticsearch_url)
    yield
    app.state.es.close()


app = FastAPI(title="Knowledge Base", lifespan=lifespan)


@app.exception_handler(ESConnectionError)
def es_unavailable(request: Request, exc: ESConnectionError):
    return JSONResponse(status_code=503, content={"detail": "Elasticsearch is not reachable."})


@app.get("/api/health")
def health(request: Request):
    return {"status": "ok", "elasticsearch": request.app.state.es.ping()}


@app.get("/api/search")
def search_documents(
    request: Request,
    q: str = Query("", max_length=500),
    date_from: date | None = None,
    date_to: date | None = None,
    tz: str = "UTC",
    page: int = Query(1, ge=1, le=200),
    size: int = Query(10, ge=1, le=50),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    if tz not in available_timezones():
        raise HTTPException(422, f"Unknown time zone: {tz}")
    return search(
        request.app.state.es,
        get_settings().index_name,
        q.strip(),
        date_from,
        date_to,
        tz,
        page,
        size,
    )


def _sync_job(es: Elasticsearch) -> None:
    settings = get_settings()
    try:
        service = drive.get_service(settings.google_token_file)
        result = run_sync(es, service, settings).as_dict()
        _sync_state.update(last_result=result, last_error=None)
    except Exception as exc:
        log.exception("Sync failed")
        _sync_state.update(last_error=str(exc) or type(exc).__name__)
    finally:
        _sync_state.update(running=False, last_finished=datetime.now(UTC).isoformat())
        _sync_lock.release()


@app.post("/api/sync", status_code=202)
def start_sync(request: Request, background: BackgroundTasks):
    if not _sync_lock.acquire(blocking=False):
        raise HTTPException(409, "A sync is already running")
    _sync_state.update(running=True)
    background.add_task(_sync_job, request.app.state.es)
    return _sync_state


@app.get("/api/sync")
def sync_status():
    return _sync_state

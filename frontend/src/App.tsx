import { FormEvent, useEffect, useState } from "react";
import {
  getSyncStatus,
  searchDocuments,
  SearchParams,
  SearchResponse,
  startSync,
  SyncStatus,
} from "./api";

const FILE_TYPE_LABELS: Record<string, string> = {
  txt: "TXT",
  pdf: "PDF",
  docx: "Word",
  gdoc: "Google Doc",
  md: "Markdown",
};

function SyncPanel() {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSyncStatus().then(setStatus, () => undefined);
  }, []);

  // Poll while a sync is in progress.
  useEffect(() => {
    if (!status?.running) return;
    const timer = setInterval(() => {
      getSyncStatus().then(setStatus, () => undefined);
    }, 1000);
    return () => clearInterval(timer);
  }, [status?.running]);

  const onSync = () => {
    setError(null);
    startSync().then(setStatus, (e: Error) => setError(e.message));
  };

  const result = status?.last_result;
  const progress = status?.running ? status.progress : null;
  return (
    <div className="sync">
      <button type="button" className="secondary" onClick={onSync} disabled={status?.running}>
        {status?.running ? "Syncing…" : "Sync documents"}
      </button>
      {error && <span className="error">{error}</span>}
      {progress?.phase === "listing" && (
        <div className="progress">
          <progress aria-label="Sync progress" />
          <span className="muted">
            Listing {progress.source}… {progress.found} found
          </span>
        </div>
      )}
      {progress?.phase === "syncing" && (
        <div className="progress">
          <progress aria-label="Sync progress" value={progress.done} max={progress.total} />
          <span className="muted">
            {progress.done} of {progress.total} · {progress.source} · {progress.current}
          </span>
        </div>
      )}
      {!status?.running && status?.last_error && (
        <span className="error">Last sync failed: {status.last_error}</span>
      )}
      {!status?.running && result && (
        <span className="muted">
          Last sync: {result.indexed} indexed, {result.unchanged} unchanged, {result.deleted}{" "}
          removed
          {result.failed > 0 && `, ${result.failed} failed`}
        </span>
      )}
    </div>
  );
}

export default function App() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState<SearchParams | null>(null);
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!submitted) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    searchDocuments(submitted, controller.signal)
      .then((response) => {
        setData(response);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (controller.signal.aborted) return;
        setError(e.message);
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
  }, [submitted]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted({ q, page: 1 });
  };

  const goToPage = (page: number) => {
    if (submitted) setSubmitted({ ...submitted, page });
  };

  const lastPage = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  return (
    <main>
      <header>
        <h1>Knowledge Base</h1>
        <SyncPanel />
      </header>

      <form onSubmit={onSubmit}>
        <input
          type="search"
          className="query"
          placeholder="Search documents…"
          aria-label="Search text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoFocus
        />
        <div className="filters">
          <button type="submit">Search</button>
        </div>
      </form>

      {error && <p className="error">{error}</p>}
      {loading && !data && <p className="muted">Searching…</p>}

      {data && (
        <section className={loading ? "results loading" : "results"}>
          <p className="muted">
            {data.total === 0
              ? "No matching documents."
              : `${data.total} matching document${data.total === 1 ? "" : "s"}`}
          </p>
          <ul>
            {data.results.map((doc) => (
              <li key={doc.id}>
                <a href={doc.url ?? undefined} target="_blank" rel="noreferrer">
                  {/* Highlights are HTML-escaped by Elasticsearch; only <mark> tags are added. */}
                  {doc.title_highlight ? (
                    <span dangerouslySetInnerHTML={{ __html: doc.title_highlight }} />
                  ) : (
                    doc.title
                  )}
                </a>
                <div className="meta">
                  <span className="badge">{FILE_TYPE_LABELS[doc.file_type] ?? doc.file_type}</span>
                  Modified {new Date(doc.modified_time).toLocaleDateString()}
                  {doc.owners.length > 0 && ` · ${doc.owners.join(", ")}`}
                </div>
                {doc.snippets.map((snippet, i) => (
                  <p key={i} className="snippet" dangerouslySetInnerHTML={{ __html: `…${snippet}…` }} />
                ))}
              </li>
            ))}
          </ul>
          {lastPage > 1 && (
            <nav>
              <button className="secondary" onClick={() => goToPage(data.page - 1)} disabled={data.page <= 1}>
                Previous
              </button>
              <span className="muted">
                Page {data.page} of {lastPage}
              </span>
              <button
                className="secondary"
                onClick={() => goToPage(data.page + 1)}
                disabled={data.page >= lastPage}
              >
                Next
              </button>
            </nav>
          )}
        </section>
      )}
    </main>
  );
}

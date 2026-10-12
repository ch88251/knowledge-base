export interface SearchResult {
  id: string;
  score: number | null;
  title: string;
  title_highlight: string | null;
  snippets: string[];
  file_type: string;
  url: string | null;
  owners: string[];
  modified_time: string;
}

export interface SearchResponse {
  total: number;
  page: number;
  size: number;
  results: SearchResult[];
}

export interface SearchParams {
  q: string;
  page: number;
}

export type SyncProgress =
  | { phase: "listing"; source: string; found: number }
  | { phase: "syncing"; source: string; current: string; done: number; total: number };

export interface SyncStatus {
  running: boolean;
  progress: SyncProgress | null;
  last_finished: string | null;
  last_result: Record<string, number> | null;
  last_error: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${resp.status})`;
    throw new Error(detail);
  }
  return resp.json();
}

export function searchDocuments(params: SearchParams, signal?: AbortSignal): Promise<SearchResponse> {
  const query = new URLSearchParams({ q: params.q, page: String(params.page) });
  return request(`/api/search?${query}`, { signal });
}

export const startSync = () => request<SyncStatus>("/api/sync", { method: "POST" });
export const getSyncStatus = () => request<SyncStatus>("/api/sync");

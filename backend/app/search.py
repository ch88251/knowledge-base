"""Elasticsearch index definition and search."""

from elasticsearch import Elasticsearch

MAPPINGS = {
    "dynamic": "strict",
    "properties": {
        "title": {"type": "text", "analyzer": "english"},
        "content": {"type": "text", "analyzer": "english", "index_options": "offsets"},
        "source": {"type": "keyword"},
        "version": {"type": "keyword", "index": False},
        "file_type": {"type": "keyword"},
        "mime_type": {"type": "keyword"},
        "owners": {"type": "keyword"},
        "size": {"type": "long"},
        "url": {"type": "keyword", "index": False},
        "created_time": {"type": "date"},
        "modified_time": {"type": "date"},
        "indexed_at": {"type": "date"},
    },
}


def ensure_index(es: Elasticsearch, index: str) -> None:
    if not es.indices.exists(index=index):
        es.indices.create(index=index, mappings=MAPPINGS)
    else:
        # Adds any fields introduced since the index was created.
        es.indices.put_mapping(index=index, properties=MAPPINGS["properties"])


def build_query(q: str) -> dict:
    if q:
        return {
            "simple_query_string": {
                "query": q,
                "fields": ["title^3", "content"],
                "default_operator": "and",
            }
        }
    return {"match_all": {}}


def search(
    es: Elasticsearch,
    index: str,
    q: str,
    page: int,
    size: int,
) -> dict:
    resp = es.search(
        index=index,
        ignore_unavailable=True,
        query=build_query(q),
        sort=["_score", {"modified_time": "desc"}] if q else [{"modified_time": "desc"}],
        from_=(page - 1) * size,
        size=size,
        source_excludes=["content"],
        track_total_hits=True,
        highlight={
            "encoder": "html",
            "pre_tags": ["<mark>"],
            "post_tags": ["</mark>"],
            "max_analyzed_offset": 1_000_000,
            "fields": {
                "title": {"number_of_fragments": 0},
                "content": {"fragment_size": 180, "number_of_fragments": 3},
            },
        },
    )
    results = []
    for hit in resp["hits"]["hits"]:
        highlight = hit.get("highlight", {})
        results.append(
            {
                "id": hit["_id"],
                "score": hit["_score"],
                **hit["_source"],
                "title_highlight": (highlight.get("title") or [None])[0],
                "snippets": highlight.get("content", []),
            }
        )
    return {"total": resp["hits"]["total"]["value"], "page": page, "size": size, "results": results}

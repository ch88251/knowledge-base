from elastic_transport import ConnectionError as ESConnectionError
from fastapi.testclient import TestClient

from app.main import app
from app.search import build_query


class FakeES:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def search(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "abc",
                        "_score": 1.5,
                        "_source": {"title": "VPN setup", "file_type": "pdf"},
                        "highlight": {"content": ["connect to the <mark>vpn</mark>"]},
                    }
                ],
            }
        }

    def close(self):
        pass


def client(fake):
    test_client = TestClient(app)
    test_client.__enter__()
    app.state.es = fake
    return test_client


def test_build_query_text_only():
    assert build_query("vpn setup")["simple_query_string"]["query"] == "vpn setup"


def test_build_query_without_text_matches_everything():
    assert build_query("") == {"match_all": {}}


def test_search_endpoint():
    fake = FakeES()
    resp = client(fake).get("/api/search", params={"q": " vpn ", "page": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["results"][0]["id"] == "abc"
    assert body["results"][0]["snippets"] == ["connect to the <mark>vpn</mark>"]
    assert body["results"][0]["title_highlight"] is None
    call = fake.calls[0]
    assert call["from_"] == 10
    assert call["query"]["simple_query_string"]["query"] == "vpn"


def test_search_rejects_bad_input():
    c = client(FakeES())
    assert c.get("/api/search", params={"page": 0}).status_code == 422
    assert c.get("/api/search", params={"size": 500}).status_code == 422


def test_search_returns_503_when_elasticsearch_is_down():
    resp = client(FakeES(error=ESConnectionError("down"))).get("/api/search")
    assert resp.status_code == 503

import pytest

from crawler.sources import derstandard
from crawler.sources.derstandard import DerStandard, PersistedQueryError

MISS = {"errors": [{"message": "PersistedQueryNotFound"}]}


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(derstandard.time, "sleep", lambda s: None)


def test_persisted_query_miss_is_retried():
    http = FakeHttp([MISS, {"data": {"getForumByContextUri": {"id": "f1", "totalPostingCount": 0}}}])
    ds = DerStandard(http)
    data = ds._query("GetForumInfo", "hash", {"contextUri": "https://www.derstandard.at/story/1"})
    assert data["getForumByContextUri"]["id"] == "f1"
    assert len(http.calls) == 2
    url, params, headers = http.calls[0]
    assert params["operationName"] == "GetForumInfo"
    assert '"sha256Hash":"hash"' in params["extensions"]
    assert headers["Content-Type"] == "application/json"


def test_persistent_miss_raises():
    ds = DerStandard(FakeHttp([MISS] * derstandard.PERSISTED_RETRIES))
    with pytest.raises(PersistedQueryError):
        ds._query("GetForumInfo", "hash", {})


def test_other_graphql_errors_raise_immediately():
    ds = DerStandard(FakeHttp([{"errors": [{"message": "boom"}]}]))
    with pytest.raises(RuntimeError, match="boom"):
        ds._query("GetForumInfo", "hash", {})

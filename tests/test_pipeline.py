from datetime import UTC, datetime, timedelta

from crawler.firestore import ALREADY_EXISTS, Firestore, WriteResult
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.pipeline import RunOptions, SourceRunner, comment_doc
from crawler.sources.base import Source

NOW = datetime.now(UTC).replace(microsecond=0)


class FakeStore(Firestore):
    """In-memory stand-in implementing the subset of the REST client used by the pipeline."""

    def __init__(self):
        self.root = "projects/p/databases/(default)/documents"
        self.docs: dict[str, dict] = {}
        self.write_count = 0
        self.read_count = 0

    def batch_get(self, paths):
        return {p: self.docs.get(p) for p in paths}

    def run_query(self, collection, filters, order_by=None, limit=None, select=None):
        rows = []
        for path, doc in self.docs.items():
            if not path.startswith(collection + "/"):
                continue
            if all(
                (op == "EQUAL" and doc.get(f) == v) or (op == "GREATER_THAN_OR_EQUAL" and doc.get(f) >= v)
                for f, op, v in filters
            ):
                rows.append((path.split("/", 1)[1], doc))
        return rows

    def batch_write(self, writes):
        res = WriteResult()
        for w in writes:
            self.write_count += 1
            body = w.body
            path = body["update"]["name"].removeprefix(self.root + "/")
            from crawler.firestore import decode_fields

            data = decode_fields(body["update"]["fields"])
            pre = body.get("currentDocument", {})
            if pre.get("exists") is False and path in self.docs:
                res.already_exists.append(w)
                continue
            doc = self.docs.setdefault(path, {})
            mask = body.get("updateMask", {}).get("fieldPaths")
            if mask is None:
                doc.clear()
            doc.update(data)
            for t in body.get("updateTransforms", []):
                inc = int(t["increment"]["integerValue"])
                *parents, leaf = t["fieldPath"].split(".")
                target = doc
                for key in parents:
                    target = target.setdefault(key, {})
                target[leaf] = target.get(leaf, 0) + inc
            res.ok.append(w)
        return res


class FakeSource(Source):
    id = "fake"
    name = "Fake"
    domain = "fake.example"
    country = "DE"
    news_sitemap = "https://fake.example/news.xml"

    def __init__(self, comments_per_call):
        super().__init__(http=None)
        self.calls = 0
        self.comments_per_call = comments_per_call

    def canonical_url(self, url):
        return url

    def discover(self, since, until):
        return [ArticleRef(url="https://fake.example/a/1", source_id=self.id, title="A", published_at=NOW)]

    def fetch(self, ref):
        comments = self.comments_per_call[min(self.calls, len(self.comments_per_call) - 1)]
        self.calls += 1
        return FetchResult(ref, comments, len(comments))


def _c(native, minutes_ago, parent=None):
    return Comment("fake", native, f"Ein Text {native}", NOW - timedelta(minutes=minutes_ago), parent, 1)


def test_comment_doc_matches_schema_and_has_no_author():
    doc = comment_doc(_c("1", 5, parent="0"), "art", NOW)
    assert set(doc) == {
        "articleId", "sourceId", "text", "publishedAt", "crawledAt", "day", "parentId",
        "upvotes", "analyzed", "analyzedAt", "analysisVersion",
    }  # fmt: skip
    assert doc["analyzed"] is False
    assert doc["parentId"] is not None and len(doc["parentId"]) == 20


def test_rerun_is_idempotent_and_keeps_analysis_state():
    store = FakeStore()
    first = [_c("1", 30), _c("2", 20, parent="1")]
    second = first + [_c("3", 1)]
    source = FakeSource([first, second])
    opts = RunOptions(max_articles=10, since_hours=48)

    stats1 = SourceRunner(source, store, opts, None).run()
    assert stats1.comments_new == 2 and stats1.articles_new == 1 and stats1.error is None
    comment_paths = [p for p in store.docs if p.startswith("comments/")]
    assert len(comment_paths) == 2
    article = next(v for k, v in store.docs.items() if k.startswith("articles/"))
    assert article["commentCount"] == 2

    # analysis marks a comment as analysed; a rerun must not reset it
    store.docs[comment_paths[0]]["analyzed"] = True
    # pretend the last crawl was long ago so the article is due again, with overlap covering all comments
    article["lastCommentCrawlAt"] = NOW - timedelta(hours=2)

    stats2 = SourceRunner(source, store, opts, None).run()
    assert stats2.comments_new == 1
    assert stats2.comments_existing == 2
    assert stats2.articles_new == 0
    assert len([p for p in store.docs if p.startswith("comments/")]) == 3
    assert store.docs[comment_paths[0]]["analyzed"] is True
    assert article["commentCount"] == 3


def test_already_exists_code_constant():
    assert ALREADY_EXISTS == 6

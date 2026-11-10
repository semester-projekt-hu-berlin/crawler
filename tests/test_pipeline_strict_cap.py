"""A strict comment cap also limits the article that crosses it (newest comments kept)."""

from datetime import UTC, datetime, timedelta

from crawler.models import ArticleRef, Comment, FetchResult
from crawler.pipeline import RunOptions, SourceRunner


def _result(n: int) -> FetchResult:
    ref = ArticleRef(url="https://example.org/a", source_id="derstandard")
    t0 = datetime(2026, 10, 26, tzinfo=UTC)
    comments = [
        Comment(
            source_id="derstandard",
            native_id=str(i),
            text=f"Kommentar Nummer {i}",
            published_at=t0 + timedelta(minutes=i),
        )
        for i in range(n)
    ]
    return FetchResult(article=ref, comments=comments)


class _Src:
    id = "derstandard"
    source_type = "news"


def test_strict_cap_truncates_article_to_newest():
    runner = SourceRunner(_Src(), None, RunOptions(dry_run=True), None)  # type: ignore[arg-type]
    runner.store_result(_result(600), datetime.now(UTC), room=100)
    assert runner.stats.comments_new == 100
    assert runner.stats.articles_capped == 1


def test_no_room_given_keeps_everything():
    runner = SourceRunner(_Src(), None, RunOptions(dry_run=True), None)  # type: ignore[arg-type]
    runner.store_result(_result(50), datetime.now(UTC))
    assert runner.stats.comments_new == 50

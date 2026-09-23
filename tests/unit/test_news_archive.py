from datetime import datetime, timedelta, timezone

ITEMS = [{"title": "Bitcoin ETF approved", "published": "2026-09-23T10:00:00Z", "source": "a"},
         {"title": "Exchange hacked, funds stolen", "published": "2026-09-23T11:00:00Z", "source": "b"}]
NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def test_headlines_are_archived_once_with_sentiment_and_seen_at(paper):
    store = paper.store
    assert store.record_news(ITEMS, NOW) == 2
    assert store.record_news(ITEMS, NOW + timedelta(hours=1)) == 0        # same items next cycle: no duplicates
    rows = store._query("SELECT * FROM news_snapshots ORDER BY id")
    assert [r["seen_at"][:19] for r in rows] == [NOW.isoformat()[:19]] * 2
    assert rows[0]["sentiment"] > 0 > rows[1]["sentiment"]
    assert store.news_count() == 2


def test_same_title_from_a_later_publication_is_a_new_row(paper):
    paper.store.record_news(ITEMS, NOW)
    later = [{**ITEMS[0], "published": "2026-09-24T10:00:00Z"}]
    assert paper.store.record_news(later, NOW + timedelta(days=1)) == 1


def test_empty_and_malformed_items_are_ignored(paper):
    assert paper.store.record_news([{"title": ""}, {"source": "x"}, {"title": "   "}], NOW) == 0
    assert paper.store.news_count() == 0


def test_cycle_archives_headlines_without_blocking_trading(paper, monkeypatch):
    from engine.market_data.fetcher import MarketSnapshot
    real = paper.market.snapshot

    def with_news(symbol, with_context=True):
        snap = real(symbol, with_context)
        return MarketSnapshot(**{**snap.__dict__, "headlines": ITEMS})

    monkeypatch.setattr(paper.market, "snapshot", with_news)
    assert paper.cycle("BUY").action_taken == "BUY"
    assert paper.store.news_count() == 2

    def boom(*a, **k):
        raise RuntimeError("archive down")

    monkeypatch.setattr(paper.store, "record_news", boom)
    assert paper.cycle("HOLD").action_taken.startswith(("HOLD", "SELL", "STOP"))   # archiving never blocks trading

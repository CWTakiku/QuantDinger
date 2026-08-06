from datetime import date

from app.services.industry_glass_fiber.fetch import aggregate_week_from_texts


def test_aggregate_week_majority_vote():
    texts = [
        "电子布报价上调，库存下降。",
        "电子布报价上调，库存去化。",
        "电子布价格连续下跌，玻纤库存持续累积。",
    ]
    out = aggregate_week_from_texts(texts, as_of=date(2026, 8, 7))
    assert out["cloth_trend"] == 1
    assert out["inventory_trend"] == -1


def test_aggregate_week_picks_highest_confidence_price():
    texts = [
        "电子布报价上调。",
        "7628电子布报价6.8元/米，价格上调。",
    ]
    out = aggregate_week_from_texts(texts, as_of=date(2026, 8, 7))
    assert out.get("cloth_7628_mid") == 6.8


def test_sync_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_GLASS_FIBER_INDUSTRY_SYNC", "false")
    monkeypatch.setenv("GLASS_FIBER_NEWS_URLS", "https://example.invalid/a")

    from app.tasks.glass_fiber_industry import run_glass_fiber_industry_sync

    result = run_glass_fiber_industry_sync()
    assert result["ok"] is True
    assert result["skipped"] is True


def test_sync_skipped_when_urls_empty(monkeypatch):
    monkeypatch.setenv("ENABLE_GLASS_FIBER_INDUSTRY_SYNC", "true")
    monkeypatch.setenv("GLASS_FIBER_NEWS_URLS", "")

    from app.tasks.glass_fiber_industry import run_glass_fiber_industry_sync

    result = run_glass_fiber_industry_sync()
    assert result["ok"] is True
    assert result["skipped"] is True


def test_sync_upserts_public_news(monkeypatch):
    calls = []

    monkeypatch.setenv("ENABLE_GLASS_FIBER_INDUSTRY_SYNC", "true")
    monkeypatch.setenv("GLASS_FIBER_NEWS_URLS", "https://example.invalid/a")
    monkeypatch.setattr(
        "app.tasks.glass_fiber_industry.fetch_url",
        lambda url, timeout=15.0: "电子布报价上调，库存下降，暂无新窑点火。",
    )
    monkeypatch.setattr(
        "app.tasks.glass_fiber_industry.upsert_glass_fiber_week",
        lambda row: calls.append(row) or row,
    )
    monkeypatch.setattr(
        "app.tasks.glass_fiber_industry._week_as_of",
        lambda: date(2026, 8, 7),
    )

    from app.tasks.glass_fiber_industry import run_glass_fiber_industry_sync

    result = run_glass_fiber_industry_sync()
    assert result["ok"] is True
    assert calls and calls[0]["source"] == "public_news"
    assert calls[0]["cloth_trend"] == 1
    assert calls[0]["as_of"] == date(2026, 8, 7)
    assert "https://example.invalid/a" in calls[0]["raw_refs"].get("urls", [])


def test_sync_continues_after_fetch_failure(monkeypatch):
    calls = []

    monkeypatch.setenv("ENABLE_GLASS_FIBER_INDUSTRY_SYNC", "true")
    monkeypatch.setenv(
        "GLASS_FIBER_NEWS_URLS",
        "https://example.invalid/bad,https://example.invalid/good",
    )

    def fake_fetch(url, timeout=15.0):
        if "bad" in url:
            raise OSError("network down")
        return "电子布报价上调，库存下降。"

    monkeypatch.setattr("app.tasks.glass_fiber_industry.fetch_url", fake_fetch)
    monkeypatch.setattr(
        "app.tasks.glass_fiber_industry.upsert_glass_fiber_week",
        lambda row: calls.append(row) or row,
    )
    monkeypatch.setattr(
        "app.tasks.glass_fiber_industry._week_as_of",
        lambda: date(2026, 8, 7),
    )

    from app.tasks.glass_fiber_industry import run_glass_fiber_industry_sync

    result = run_glass_fiber_industry_sync()
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["cloth_trend"] == 1


def test_celery_task_registered():
    from app.celery_app import celery_app

    assert "quantdinger.tasks.glass_fiber_industry_sync" in celery_app.tasks
    routes = celery_app.conf.task_routes or {}
    assert routes.get("quantdinger.tasks.glass_fiber_industry_sync") == {
        "queue": "maintenance"
    }

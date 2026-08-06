from datetime import date
from unittest.mock import patch

from app.services.industry_glass_fiber.store import (
    SOURCE_PRIORITY,
    _pick_best_row,
    resolve_glass_fiber_week,
    upsert_glass_fiber_week,
)


def _as_date_key(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _install_in_memory_store(monkeypatch):
    rows: dict[str, list[dict]] = {}

    def fake_find_effective(requested):
        req = _as_date_key(requested)
        candidates = sorted((k for k in rows if k <= req), reverse=True)
        for key in candidates:
            if _pick_best_row(rows.get(key, [])):
                from datetime import date as date_cls

                return date_cls.fromisoformat(key)
        return None

    def fake_load(as_of):
        key = _as_date_key(as_of)
        return list(rows.get(key, []))

    def fake_upsert(row):
        key = _as_date_key(row["as_of"])
        rows.setdefault(key, [])
        rows[key] = [r for r in rows[key] if r["source"] != row["source"]] + [dict(row)]
        return dict(row)

    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store._find_effective_as_of",
        fake_find_effective,
    )
    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store._load_rows_for_as_of",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store._upsert_row",
        fake_upsert,
    )
    return rows


def test_source_priority_order():
    assert SOURCE_PRIORITY == ("manual", "zhuochuang", "oilchem", "public_news")


def test_manual_overrides_public_news(monkeypatch):
    _install_in_memory_store(monkeypatch)

    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 1),
            "cloth_trend": 1,
            "inventory_trend": -1,
            "new_capacity_flag": 0,
            "source": "public_news",
            "confidence": 0.7,
        }
    )
    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 1),
            "cloth_trend": -1,
            "inventory_trend": 1,
            "new_capacity_flag": 0,
            "source": "manual",
            "confidence": 1.0,
        }
    )
    out = resolve_glass_fiber_week(date(2026, 8, 1))
    assert out is not None
    assert out["source"] == "manual"
    assert out["cloth_trend"] == -1
    assert out["industry_available"] is True


def test_low_confidence_skipped(monkeypatch):
    _install_in_memory_store(monkeypatch)

    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 1),
            "cloth_trend": 1,
            "inventory_trend": -1,
            "new_capacity_flag": 0,
            "source": "public_news",
            "confidence": 0.4,
        }
    )
    out = resolve_glass_fiber_week(date(2026, 8, 1))
    assert out is None


def test_zhuochuang_overrides_public_news(monkeypatch):
    _install_in_memory_store(monkeypatch)

    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 1),
            "cloth_trend": 0,
            "inventory_trend": 0,
            "new_capacity_flag": 0,
            "source": "public_news",
            "confidence": 0.8,
        }
    )
    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 1),
            "cloth_trend": 1,
            "inventory_trend": -1,
            "new_capacity_flag": 0,
            "source": "zhuochuang",
            "confidence": 0.6,
        }
    )
    out = resolve_glass_fiber_week(date(2026, 8, 1))
    assert out is not None
    assert out["source"] == "zhuochuang"


def test_pit_snaps_to_latest_week(monkeypatch):
    rows = _install_in_memory_store(monkeypatch)

    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 7, 25),
            "cloth_trend": 1,
            "inventory_trend": -1,
            "new_capacity_flag": 0,
            "source": "public_news",
            "confidence": 0.7,
        }
    )
    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 1),
            "cloth_trend": -1,
            "inventory_trend": 1,
            "new_capacity_flag": 1,
            "source": "public_news",
            "confidence": 0.8,
        }
    )

    # Query mid-week: should snap to 2026-08-01 row set, not 2026-07-25
    out = resolve_glass_fiber_week(date(2026, 8, 3))
    assert out is not None
    assert out["as_of"] == "2026-08-01"
    assert out["cloth_trend"] == -1

    # Query before first week → None
    assert resolve_glass_fiber_week(date(2026, 7, 1)) is None


def test_pit_skips_week_with_only_low_confidence_rows(monkeypatch):
    _install_in_memory_store(monkeypatch)

    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 1),
            "cloth_trend": 1,
            "inventory_trend": -1,
            "new_capacity_flag": 0,
            "source": "manual",
            "confidence": 1.0,
        }
    )
    upsert_glass_fiber_week(
        {
            "as_of": date(2026, 8, 8),
            "cloth_trend": -1,
            "inventory_trend": 1,
            "new_capacity_flag": 0,
            "source": "public_news",
            "confidence": 0.4,
        }
    )

    out = resolve_glass_fiber_week(date(2026, 8, 10))
    assert out is not None
    assert out["as_of"] == "2026-08-01"
    assert out["source"] == "manual"
    assert out["cloth_trend"] == 1


def test_upsert_uses_on_conflict():
    captured = []

    class _Cur:
        def execute(self, sql, params=None):
            if "INSERT" in sql.upper():
                captured.append((sql, params))

        def fetchone(self):
            return {
                "as_of": date(2026, 8, 1),
                "cloth_7628_mid": None,
                "yarn_2400_mid": None,
                "cloth_trend": 1,
                "inventory_trend": -1,
                "new_capacity_flag": 0,
                "source": "manual",
                "confidence": 1.0,
                "raw_refs": {},
            }

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch(
        "app.services.industry_glass_fiber.store.get_db_connection",
        return_value=_Db(),
    ):
        out = upsert_glass_fiber_week(
            {
                "as_of": date(2026, 8, 1),
                "cloth_trend": 1,
                "inventory_trend": -1,
                "new_capacity_flag": 0,
                "source": "manual",
                "confidence": 1.0,
            }
        )

    assert len(captured) == 1
    sql, params = captured[0]
    assert "ON CONFLICT" in sql.upper()
    assert "qd_industry_glass_fiber_weekly" in sql
    assert params[0] == date(2026, 8, 1)
    assert params[6] == "manual"
    assert out["source"] == "manual"
    assert out["cloth_trend"] == 1

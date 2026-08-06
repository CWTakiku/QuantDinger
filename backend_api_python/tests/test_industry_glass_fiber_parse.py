from pathlib import Path

from app.services.industry_glass_fiber.parse import parse_glass_fiber_news

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "glass_fiber_news_sample.txt"


def test_parse_up_price_down_inventory():
    text = FIXTURE.read_text(encoding="utf-8")
    out = parse_glass_fiber_news(text)
    assert out["cloth_trend"] == 1
    assert out["inventory_trend"] == -1
    assert out["new_capacity_flag"] == 0
    assert out["confidence"] >= 0.5
    assert out.get("cloth_7628_mid") in (6.5, None) or out.get("cloth_7628_mid") == 6.5


def test_parse_down_price_inventory_build():
    out = parse_glass_fiber_news("电子布价格连续下跌，玻纤库存持续累积抬升。")
    assert out["cloth_trend"] == -1
    assert out["inventory_trend"] == 1


def test_inventory_only_huiluo_not_cloth_down():
    out = parse_glass_fiber_news("行业库存回落，价格持平。")
    assert out["cloth_trend"] == 0
    assert out["inventory_trend"] == -1


def test_empty_text_low_confidence():
    out = parse_glass_fiber_news("")
    assert out["cloth_trend"] == 0
    assert out["inventory_trend"] == 0
    assert out["new_capacity_flag"] == 0
    assert out["cloth_7628_mid"] is None
    assert out["confidence"] == 0.3


def test_no_new_kiln_ignition():
    out = parse_glass_fiber_news("本周暂无新窑点火，市场观望。")
    assert out["new_capacity_flag"] == 0


def test_conflicting_price_phrases_reduced_confidence():
    out = parse_glass_fiber_news("电子布价格上调，部分规格下跌。")
    assert out["cloth_trend"] == 0
    assert out["confidence"] == 0.3

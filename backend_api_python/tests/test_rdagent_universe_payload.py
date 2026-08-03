from app.services.rdagent_bridge.universe_payload import (
    ALL_MARKET_CODE,
    build_universe_job_payload,
    list_rdagent_universes,
)


class _FakeUniverseService:
    def list_universes(self, user_id):
        return [
            {
                "id": 1,
                "code": "csi300",
                "name": "沪深300",
                "market": "CNStock",
                "universe_type": "index",
                "source_ref": "000300.SH",
                "is_system": True,
                "member_count": 300,
            },
            {
                "id": 2,
                "code": "my-pool",
                "name": "自选池",
                "market": "CNStock",
                "universe_type": "manual",
                "source_ref": "",
                "is_system": False,
                "member_count": 12,
            },
            {
                "id": 3,
                "code": "sp500",
                "name": "S&P500",
                "market": "USStock",
                "universe_type": "index",
                "is_system": True,
                "member_count": 500,
            },
        ]

    def resolve_members(self, user_id, universe_id, *, as_of=None):
        if int(universe_id) == 1:
            return [{"symbol": f"{600000 + i}.SH"} for i in range(15)]
        if int(universe_id) == 2:
            return [{"symbol": f"{i:06d}.SZ"} for i in range(1, 13)]
        return []


def test_list_rdagent_universes_filters_cn_and_adds_all():
    items = list_rdagent_universes(_FakeUniverseService(), 1)
    codes = [i["code"] for i in items]
    assert codes[0] == ALL_MARKET_CODE
    assert "csi300" in codes
    assert "my-pool" in codes
    assert "sp500" not in codes


def test_build_all_market_payload():
    out = build_universe_job_payload(_FakeUniverseService(), 1, ALL_MARKET_CODE)
    assert out["market_id"] == "all"
    assert out["benchmark"] == "SZ000985"


def test_build_csi300_payload():
    out = build_universe_job_payload(_FakeUniverseService(), 1, "csi300", end_date="2024-06-01")
    assert out["code"] == "csi300"
    assert out["benchmark"] == "SH000300"
    assert len(out["members"]) == 15


def test_build_manual_payload():
    out = build_universe_job_payload(_FakeUniverseService(), 1, "my-pool")
    assert out["universe_type"] == "manual"
    assert "benchmark" not in out  # bridge infers equal-weight
    assert len(out["members"]) == 12

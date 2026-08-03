"""Build RD-Agent job universe payloads from QuantDinger pools."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

ALL_MARKET_CODE = "__all_market__"
MIN_MEMBERS = 10

_INDEX_BENCH_BY_CODE = {
    "csi300": "SH000300",
    "csi500": "SH000905",
    "csi1000": "SH000852",
}

_SOURCE_REF_BENCH = {
    "000300.SH": "SH000300",
    "000905.SH": "SH000905",
    "000852.SH": "SH000852",
    "000985.SH": "SZ000985",
    "000985.SZ": "SZ000985",
}


def list_rdagent_universes(universe_service, user_id: int) -> list[dict[str, Any]]:
    """CNStock pools + special all-market entry for the research factory."""
    items = [
        {
            "code": ALL_MARKET_CODE,
            "name": "全市场",
            "market": "CNStock",
            "universe_type": "all_market",
            "benchmark_hint": "SZ000985",
            "is_system": True,
            "member_count": None,
        }
    ]
    for u in universe_service.list_universes(int(user_id)):
        if str(u.get("market") or "") != "CNStock":
            continue
        code = str(u.get("code") or "")
        utype = str(u.get("universe_type") or "")
        hint = _INDEX_BENCH_BY_CODE.get(code)
        if not hint:
            ref = str(u.get("source_ref") or "").strip().upper()
            hint = _SOURCE_REF_BENCH.get(ref)
        if not hint and utype in {"manual", "watchlist"}:
            hint = "equal_weight"
        items.append(
            {
                "code": code,
                "name": u.get("name") or code,
                "market": "CNStock",
                "universe_type": utype,
                "source_ref": u.get("source_ref") or "",
                "benchmark_hint": hint or "equal_weight",
                "is_system": bool(u.get("is_system")),
                "member_count": u.get("member_count"),
                "id": u.get("id"),
            }
        )
    return items


def _as_of_date(end_date: str | None) -> date:
    if end_date:
        return date.fromisoformat(str(end_date).strip()[:10])
    return datetime.utcnow().date()


def build_universe_job_payload(
    universe_service,
    user_id: int,
    universe_code: str | None,
    *,
    end_date: str | None = None,
) -> dict[str, Any] | None:
    """Resolve QD members into a bridge ``universe`` object.

    Returns None when ``universe_code`` is empty (keep RD template defaults).
    """
    code = str(universe_code or "").strip()
    if not code:
        return None
    if code == ALL_MARKET_CODE:
        return {
            "code": ALL_MARKET_CODE,
            "universe_type": "all_market",
            "market_id": "all",
            "members": [],
            "benchmark": "SZ000985",
        }

    as_of = _as_of_date(end_date)
    target = None
    for u in universe_service.list_universes(int(user_id)):
        if str(u.get("code") or "") == code and str(u.get("market") or "") == "CNStock":
            target = u
            break
    if target is None:
        raise ValueError(f"unknown CNStock universe_code: {code!r}")

    uid = int(target["id"])
    members = universe_service.resolve_members(int(user_id), uid, as_of=as_of)
    symbols = [str(m.get("symbol") or "") for m in members if m.get("symbol")]
    if len(symbols) < MIN_MEMBERS and code not in _INDEX_BENCH_BY_CODE:
        # System index pools may still be large; only enforce for sparse manual pools.
        pass
    if len(symbols) < MIN_MEMBERS:
        raise ValueError(
            f"universe {code!r} has {len(symbols)} members at {as_of.isoformat()}, "
            f"need at least {MIN_MEMBERS}"
        )

    utype = str(target.get("universe_type") or "")
    source_ref = str(target.get("source_ref") or "")
    payload: dict[str, Any] = {
        "code": code,
        "universe_type": utype,
        "source_ref": source_ref,
        "market_id": "qd_universe",
        "members": symbols,
    }
    # Let bridge infer benchmark; optionally pre-set for clarity
    if code in _INDEX_BENCH_BY_CODE:
        payload["benchmark"] = _INDEX_BENCH_BY_CODE[code]
    elif source_ref.upper() in _SOURCE_REF_BENCH:
        payload["benchmark"] = _SOURCE_REF_BENCH[source_ref.upper()]
    return payload

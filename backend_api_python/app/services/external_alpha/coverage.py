"""Preflight: External Alpha score panels must cover the backtest rebalance window."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.external_alpha.store import (
    list_external_alpha_as_ofs,
    load_external_alpha_scores_as_of,
)
from app.services.quant_models.schedule import (
    expand_rebalance_dates,
    infer_schedule_from_strategy,
    to_score_as_ofs,
)


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def assert_external_alpha_score_coverage(
    *,
    code: str,
    params: dict[str, Any] | None,
    start_date: date | str,
    end_date: date | str,
    min_names: int | None = None,
) -> dict[str, Any] | None:
    """Raise ``ValueError`` when no rebalance day can read a usable score panel.

    Returns a small coverage summary when the strategy is not External Alpha
    (``None``) or when coverage is sufficient.
    """
    params = dict(params or {})
    source = str(params.get("source") or "").strip()
    version = str(params.get("version") or "").strip()
    if not source or not version:
        return None

    try:
        need_names = int(params.get("min_names") if min_names is None else min_names)
    except (TypeError, ValueError):
        need_names = 10
    need_names = max(1, need_names)

    try:
        lag = int(params.get("score_lag_days") or 1)
    except (TypeError, ValueError):
        lag = 1

    start_d = _to_date(start_date)
    end_d = _to_date(end_date)
    schedule = infer_schedule_from_strategy(code or "", params)
    rebalances = expand_rebalance_dates(schedule, start_d, end_d)
    if not rebalances:
        return {"source": source, "version": version, "rebalances": 0, "covered": 0}

    as_ofs = to_score_as_ofs(rebalances, lag)
    panel = list_external_alpha_as_ofs(source=source, version=version)
    covered = 0
    for as_of in as_ofs:
        series = load_external_alpha_scores_as_of(as_of, source=source, version=version)
        if series is not None and len(series) >= need_names:
            covered += 1

    summary = {
        "source": source,
        "version": version,
        "rebalances": len(rebalances),
        "as_ofs": len(as_ofs),
        "covered": covered,
        "panel_days": len(panel),
        "panel_min": panel[0] if panel else None,
        "panel_max": panel[-1] if panel else None,
    }
    if covered > 0:
        return summary

    panel_span = (
        f"{summary['panel_min']}～{summary['panel_max']}（{summary['panel_days']} 个交易日）"
        if panel
        else "空（尚未导入/推理任何分数）"
    )
    raise ValueError(
        "回测区间内 External Alpha 无可读分数，因此不会产生调仓信号。"
        f" source={source} version={version}；"
        f"调仓约 {len(rebalances)} 次，分数面板={panel_span}；"
        f"回测区间={start_d.isoformat()}～{end_d.isoformat()}。"
        "请：① 把回测起止改到分数覆盖内；② 在「量化模型」下选择模型走自动补分回测；"
        "③ 或先到选股页按区间刷新分数。"
    )

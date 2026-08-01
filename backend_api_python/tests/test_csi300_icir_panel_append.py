import pandas as pd

from pathlib import Path
import importlib.util


def _load_example():
    path = Path(__file__).resolve().parents[2] / "docs/examples/strategy_v2_csi300_enhanced_v2_weekly.py"
    spec = importlib.util.spec_from_file_location("csi300_v2_example", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_append_icir_panel_bootstraps_empty_frame():
    mod = _load_example()
    scores = {
        "momentum": pd.Series({"CNStock:600519.SH": 1.0, "CNStock:000001.SZ": -0.5, "a": 0.2, "b": 0.1, "c": 0.0}),
    }
    panel = mod._append_icir_panel({}, scores, "2021-08-31")
    frame = panel["momentum"]
    assert list(frame.index) == [pd.Timestamp("2021-08-31")]
    assert "CNStock:600519.SH" in frame.columns

    panel2 = mod._append_icir_panel(panel, scores, "2021-09-01")
    assert len(panel2["momentum"].index) == 2

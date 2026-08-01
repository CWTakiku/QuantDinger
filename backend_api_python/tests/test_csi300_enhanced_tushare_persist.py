import pandas as pd

from app.services.csi300_enhanced import tushare_sync as sync


class _Cursor:
    def __init__(self):
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def close(self):
        pass


class _Connection:
    def __init__(self):
        self.cursor_obj = _Cursor()
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def test_persist_index_weights_upsert(monkeypatch):
    conn = _Connection()
    connections = []

    def fake_get_db_connection():
        connections.append(_Connection())
        return connections[-1]

    monkeypatch.setattr(sync, "get_db_connection", fake_get_db_connection)

    frame = pd.DataFrame([
        {"trade_date": "20260731", "con_code": "600519.SH", "weight": 4.2},
        {"trade_date": "20260731", "con_code": "000001.SZ", "weight": 1.1},
    ])
    n = sync.persist_index_weights(frame)
    assert n == 2
    assert connections[0].committed is True
    assert len(connections[0].cursor_obj.executions) == 2
    sql, params = connections[0].cursor_obj.executions[0]
    assert "qd_csi300_index_weights" in sql
    assert "ON CONFLICT" in sql
    assert params == ("20260731", "600519.SH", 4.2)

    # second call idempotent
    assert sync.persist_index_weights(frame) == 2
    assert len(connections[1].cursor_obj.executions) == 2


def test_persist_index_weights_empty_returns_zero(monkeypatch):
    conn = _Connection()
    monkeypatch.setattr(sync, "get_db_connection", lambda: conn)
    assert sync.persist_index_weights(pd.DataFrame()) == 0
    assert conn.committed is False


def test_persist_flow_daily_upsert(monkeypatch):
    connections = []

    def fake_get_db_connection():
        connections.append(_Connection())
        return connections[-1]

    monkeypatch.setattr(sync, "get_db_connection", fake_get_db_connection)

    frame = pd.DataFrame([
        {
            "trade_date": "20260731",
            "ts_code": "600519.SH",
            "north_net_buy": 1200.0,
            "margin_balance": 3.5e8,
            "metadata_json": {"hk_vol": 100.0, "margin_src": "margin_detail"},
        },
        {
            "trade_date": "20260731",
            "ts_code": "000001.SZ",
            "north_net_buy": -500.0,
            "margin_balance": None,
            "metadata_json": {"hk_vol": 50.0},
        },
    ])
    n = sync.persist_flow_daily(frame)
    assert n == 2
    assert connections[0].committed is True
    sql, params = connections[0].cursor_obj.executions[0]
    assert "qd_ashare_flow_daily" in sql
    assert "ON CONFLICT" in sql
    assert params[0] == "20260731"
    assert params[1] == "600519.SH"
    assert params[2] == 1200.0
    assert params[3] == 3.5e8
    assert sync.persist_flow_daily(frame) == 2


def test_persist_flow_daily_empty_returns_zero(monkeypatch):
    conn = _Connection()
    monkeypatch.setattr(sync, "get_db_connection", lambda: conn)
    assert sync.persist_flow_daily(pd.DataFrame()) == 0
    assert conn.committed is False


def test_persist_consensus_daily_upsert(monkeypatch):
    connections = []

    def fake_get_db_connection():
        connections.append(_Connection())
        return connections[-1]

    monkeypatch.setattr(sync, "get_db_connection", fake_get_db_connection)

    frame = pd.DataFrame([
        {
            "trade_date": "20260731",
            "ts_code": "600519.SH",
            "eps_fy1": 58.2,
            "pe_fy1": 28.5,
            "rating_mean": 4.5,
            "metadata_json": {"consensus_src": "report_rc", "report_count": 3},
        },
    ])
    n = sync.persist_consensus_daily(frame)
    assert n == 1
    assert connections[0].committed is True
    sql, params = connections[0].cursor_obj.executions[0]
    assert "qd_ashare_consensus_daily" in sql
    assert params == ("20260731", "600519.SH", 58.2, 28.5, 4.5, '{"consensus_src": "report_rc", "report_count": 3}')


def test_fetch_flow_daily_permission_failure_returns_empty(monkeypatch):
    class _Pro:
        def hk_hold(self, **_kwargs):
            raise PermissionError("no permission for hk_hold")

        def margin_detail(self, **_kwargs):
            raise PermissionError("no permission for margin_detail")

    monkeypatch.setattr(sync, "is_tushare_configured", lambda: True)
    monkeypatch.setattr(sync, "_build_pro", lambda: _Pro())
    out = sync.fetch_flow_daily(trade_date="20260731")
    assert out.empty


def test_fetch_and_persist_flow_daily_returns_zero_on_failure(monkeypatch):
    monkeypatch.setattr(sync, "fetch_flow_daily", lambda **_: pd.DataFrame())
    assert sync.fetch_and_persist_flow_daily(trade_date="20260731") == 0


def test_fetch_consensus_daily_permission_failure_returns_empty(monkeypatch):
    class _Pro:
        def report_rc(self, **_kwargs):
            raise PermissionError("no permission for report_rc")

        def forecast_vip(self, **_kwargs):
            raise PermissionError("no permission for forecast_vip")

    monkeypatch.setattr(sync, "is_tushare_configured", lambda: True)
    monkeypatch.setattr(sync, "_build_pro", lambda: _Pro())
    out = sync.fetch_consensus_daily(trade_date="20260731")
    assert out.empty
    assert sync.fetch_and_persist_consensus_daily(trade_date="20260731") == 0

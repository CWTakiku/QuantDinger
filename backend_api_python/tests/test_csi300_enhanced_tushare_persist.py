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

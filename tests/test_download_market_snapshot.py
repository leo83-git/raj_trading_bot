import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "download_market_snapshot.py"


spec = importlib.util.spec_from_file_location("download_market_snapshot", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_resolve_batch_size_warns_and_caps_at_max():
    effective_size, warned = module.resolve_batch_size(3000)

    assert effective_size == module.MAX_TOKENS_PER_CONNECTION
    assert warned is True


def test_resolve_credentials_uses_zerodha_config_values(monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("ZERODHA_API_KEY", raising=False)
    monkeypatch.delenv("ZERODHA_ACCESS_TOKEN", raising=False)

    monkeypatch.setattr(
        module,
        "load_zerodha_credentials_from_config",
        lambda: ("cfg-key", "cfg-token"),
    )

    api_key, access_token = module.resolve_credentials()

    assert api_key == "cfg-key"
    assert access_token == "cfg-token"


def test_wait_for_connection_waits_until_socket_is_connected(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.is_connected = False
            self.ws = object()

    ws = FakeSocket()
    clock = {"now": 0.0}

    def fake_time():
        return clock["now"]

    def fake_sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(module.time, "sleep", fake_sleep)
    monkeypatch.setattr(module.time, "time", fake_time)

    assert module.wait_for_connection(ws, timeout_seconds=0.1) is False


def test_collect_chunk_falls_back_to_rest_quotes_when_websocket_cache_is_empty(monkeypatch):
    class FakeSocket:
        def __init__(self, *args, **kwargs):
            self.is_connected = True
            self.price_cache = {}

        def connect(self):
            return True

        def set_mode(self, *args, **kwargs):
            return None

        def subscribe(self, *args, **kwargs):
            return None

        def disconnect(self):
            return None

    class FakeKiteClient:
        def __init__(self, *args, **kwargs):
            self.access_token = None

        def set_access_token(self, token):
            self.access_token = token

        def quote(self, *tokens):
            return {str(tokens[0]): {"last_price": 123.45, "depth": {"buy": [{"price": 123.0}], "sell": [{"price": 124.0}]}}}

    monkeypatch.setattr(module, "ZerodhaWebSocket", lambda *args, **kwargs: FakeSocket())
    monkeypatch.setattr(module, "KiteConnect", lambda *args, **kwargs: FakeKiteClient())

    rows = module.collect_chunk(
        [{"instrument_token": 12345, "tradingsymbol": "TEST", "exchange": "NSE"}],
        "api-key",
        "access-token",
        0.0,
    )

    assert len(rows) == 1
    assert rows[0]["instrument_token"] == 12345
    assert rows[0]["last_price"] == 123.45


def test_ensure_table_uses_canonical_snapshot_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'snapshot.db'}", future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE market_snapshot (
                instrument_token INTEGER PRIMARY KEY,
                symbol TEXT,
                exchange TEXT,
                last_price TEXT,
                quote_json TEXT,
                depth_json TEXT,
                timestamp TEXT
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO market_snapshot VALUES (
                1, 'TEST', 'NSE', '123.45', '{"last_price":123.45}', '{"buy":[{"price":123}],"sell":[{"price":124}]}', '2026-08-06T09:15:00'
            )
            """
        )
    table = module.ensure_table(engine)
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("market_snapshot")}

    assert {"instrument_token", "symbol", "exchange", "last_price", "bids_json", "asks_json", "timestamp"}.issubset(columns)
    assert "quote_json" not in columns
    assert "depth_json" not in columns
    with engine.begin() as conn:
        rows = list(conn.execute(table.select()))
    assert rows[0].last_price == 123.45

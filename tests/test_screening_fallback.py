import importlib.util
import sys
import types
from pathlib import Path

# Provide lightweight module shims for unrelated optional dependencies so the
# test can import the main trading module in isolation.
config_module = types.ModuleType("config")
config_module.LOG_DIR = "/tmp"
config_module.LOG_FILE = "/tmp/trading.log"
sys.modules.setdefault("config", config_module)

repo_root = Path(__file__).resolve().parents[1]
module_path = repo_root / "main.py"
module_spec = importlib.util.spec_from_file_location("main_under_test", module_path)
main_module = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = main_module
module_spec.loader.exec_module(main_module)
RajTradingBot = main_module.RajTradingBot


def test_normalize_screening_candidate_handles_tuple_and_dict_inputs():
    system = RajTradingBot.__new__(RajTradingBot)

    dict_result = system._normalize_screening_candidate(
        {
            "symbol": "RELIANCE",
            "category": "intraday",
            "close": 2500.0,
            "volume": 150000,
            "score": 0.8,
        }
    )
    tuple_result = system._normalize_screening_candidate(
        ("TCS", "fno", 3400.0, 120000, 0.7)
    )

    assert dict_result["symbol"] == "RELIANCE"
    assert dict_result["category"] == "intraday"
    assert dict_result["close"] == 2500.0
    assert dict_result["volume"] == 150000
    assert dict_result["screener_score"] == 0.8

    assert tuple_result["symbol"] == "TCS"
    assert tuple_result["category"] == "fno"
    assert tuple_result["close"] == 3400.0
    assert tuple_result["volume"] == 120000
    assert tuple_result["screener_score"] == 0.0


def test_build_screening_fallback_candidates_preserves_category_and_metrics():
    system = RajTradingBot.__new__(RajTradingBot)
    candidates = [
        {
            "symbol": "RELIANCE",
            "category": "intraday",
            "close": 2500.0,
            "volume": 150000,
            "score": 0.8,
        },
        ("TCS", "fno", 3400.0, 120000, 0.7),
    ]

    fallback = system._build_screening_fallback_candidates(candidates, "intraday")

    assert len(fallback) == 1
    assert fallback[0]["symbol"] == "RELIANCE"
    assert fallback[0]["category"] == "intraday"
    assert fallback[0]["close"] == 2500.0
    assert fallback[0]["volume"] == 150000
    assert fallback[0]["screener_score"] == 0.8

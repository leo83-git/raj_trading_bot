"""Test F&O batch processing with asyncio and semaphore."""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from core.database import DatabaseManager


@pytest.mark.asyncio
async def test_batch_processing_with_semaphore():
    """Verify batch processing groups symbols and respects concurrent limit."""

    # Mock config
    config = {
        "fno_batch_size": 2,
        "fno_max_concurrent": 2,
        "fno_task_timeout_seconds": 5,
    }

    # Track processing order and concurrency
    active_tasks = 0
    max_concurrent = 0
    processing_log = []

    async def mock_process_stock(stock_item: dict) -> dict:
        """Simulate stock processing with tracking."""
        nonlocal active_tasks, max_concurrent

        active_tasks += 1
        max_concurrent = max(max_concurrent, active_tasks)
        processing_log.append(f"START_{stock_item['symbol']}")

        await asyncio.sleep(0.1)  # Simulate processing

        processing_log.append(f"END_{stock_item['symbol']}")
        active_tasks -= 1

        return {
            "processed": True,
            "symbol": stock_item["symbol"],
            "result": {"status": "ok"},
        }

    # Create test stocks
    stocks_list = [
        {"symbol": "STOCK1", "category": "fno"},
        {"symbol": "STOCK2", "category": "fno"},
        {"symbol": "STOCK3", "category": "fno"},
        {"symbol": "STOCK4", "category": "fno"},
        {"symbol": "STOCK5", "category": "fno"},
    ]

    # Set up batch processing
    batch_size = config.get("fno_batch_size", 8)
    max_concurrent_batches = config.get("fno_max_concurrent", 3)
    task_timeout_per_stock = config.get("fno_task_timeout_seconds", 120)

    results = []
    semaphore = asyncio.Semaphore(max_concurrent_batches)

    async def _run_one_stock(stock_item: dict) -> dict:
        try:
            result = await asyncio.wait_for(
                mock_process_stock(stock_item), timeout=task_timeout_per_stock
            )
            return result
        except asyncio.TimeoutError:
            return {
                "processed": False,
                "symbol": stock_item.get("symbol", "unknown"),
                "result": {"error": "timeout"},
            }

    async def _process_batch_async(batch: list[dict]) -> list[dict]:
        async with semaphore:
            return await asyncio.gather(*[_run_one_stock(s) for s in batch])

    # Create batches
    batches = [
        stocks_list[i : i + batch_size] for i in range(0, len(stocks_list), batch_size)
    ]

    # Verify batching
    assert len(batches) == 3  # 5 stocks / 2 per batch = 3 batches
    assert len(batches[0]) == 2
    assert len(batches[1]) == 2
    assert len(batches[2]) == 1

    # Run batches
    all_batch_tasks = [asyncio.create_task(_process_batch_async(b)) for b in batches]
    completed_batches = await asyncio.gather(*all_batch_tasks)

    for batch_result in completed_batches:
        results.extend(batch_result)

    # Verify results
    assert len(results) == 5
    assert all(r["processed"] for r in results)

    # Verify concurrency was limited
    assert max_concurrent <= max_concurrent_batches * batch_size
    print(
        f"Max concurrent tasks: {max_concurrent}, Expected limit: {max_concurrent_batches * batch_size}"
    )


@pytest.mark.asyncio
async def test_batch_processing_timeout_handling():
    """Verify timeout handling for individual stocks."""

    config = {
        "fno_batch_size": 2,
        "fno_max_concurrent": 2,
        "fno_task_timeout_seconds": 0.05,  # Very short timeout to trigger timeout
    }

    async def mock_slow_process_stock(stock_item: dict) -> dict:
        """Simulate slow stock processing that will timeout."""
        await asyncio.sleep(1)  # Will exceed the 0.05s timeout
        return {"processed": True, "symbol": stock_item["symbol"]}

    stocks_list = [
        {"symbol": "SLOW1", "category": "fno"},
        {"symbol": "SLOW2", "category": "fno"},
    ]

    batch_size = config.get("fno_batch_size", 8)
    max_concurrent_batches = config.get("fno_max_concurrent", 3)
    task_timeout_per_stock = config.get("fno_task_timeout_seconds", 120)

    results = []
    semaphore = asyncio.Semaphore(max_concurrent_batches)

    async def _run_one_stock(stock_item: dict) -> dict:
        try:
            result = await asyncio.wait_for(
                mock_slow_process_stock(stock_item), timeout=task_timeout_per_stock
            )
            return result
        except asyncio.TimeoutError:
            return {
                "processed": False,
                "symbol": stock_item.get("symbol", "unknown"),
                "result": {"error": "processing_timeout_individual_stock"},
            }

    async def _process_batch_async(batch: list[dict]) -> list[dict]:
        async with semaphore:
            return await asyncio.gather(*[_run_one_stock(s) for s in batch])

    batches = [
        stocks_list[i : i + batch_size] for i in range(0, len(stocks_list), batch_size)
    ]
    all_batch_tasks = [asyncio.create_task(_process_batch_async(b)) for b in batches]
    completed_batches = await asyncio.gather(*all_batch_tasks)

    for batch_result in completed_batches:
        results.extend(batch_result)

    # Verify timeouts were caught
    assert len(results) == 2
    assert all(not r["processed"] for r in results)
    assert all(
        r["result"]["error"] == "processing_timeout_individual_stock" for r in results
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_replace_fno_contract_cache_deduplicates_symbols(tmp_path: Path):
    """A refresh payload with duplicate symbols should not violate uniqueness."""

    db_path = tmp_path / "fno_cache.db"
    manager = DatabaseManager(database_url=f"sqlite:///{db_path}")

    contracts = [
        {
            "symbol": "NIFTY26AUGFUT",
            "instrument_token": "1001",
            "exchange": "NFO",
            "segment": "NFO-FUT",
            "expiry": "2026-08-27",
            "strike": 0.0,
            "option_type": "FUT",
        },
        {
            "symbol": "NIFTY26AUGFUT",
            "instrument_token": "2002",
            "exchange": "NFO",
            "segment": "NFO-FUT",
            "expiry": "2026-08-27",
            "strike": 0.0,
            "option_type": "FUT",
        },
    ]

    saved = manager.replace_fno_contract_cache(
        contracts, last_refresh=datetime(2026, 8, 4, 12, 8, 45)
    )

    loaded_contracts, last_refresh = manager.load_fno_contract_cache()

    assert saved == 1
    assert len(loaded_contracts) == 1
    assert loaded_contracts[0]["symbol"] == "NIFTY26AUGFUT"
    assert loaded_contracts[0]["instrument_token"] == "2002"
    assert last_refresh == datetime(2026, 8, 4, 12, 8, 45)


def test_fno_contract_loader_deduplicates_by_symbol():
    """The loader should collapse duplicate symbols before persistence."""

    from screener.fno_contract_loader import FnoContractLoader

    loader = FnoContractLoader.__new__(FnoContractLoader)
    contracts = [
        {"symbol": "BANKNIFTY26AUGFUT", "instrument_token": "1001"},
        {"symbol": "BANKNIFTY26AUGFUT", "instrument_token": "2002"},
        {"symbol": "NIFTY26AUGFUT", "instrument_token": "3003"},
    ]

    deduped = loader._dedupe_contracts(contracts)

    assert len(deduped) == 2
    assert deduped[0]["symbol"] == "BANKNIFTY26AUGFUT"
    assert deduped[0]["instrument_token"] == "2002"
    assert deduped[1]["symbol"] == "NIFTY26AUGFUT"


def test_fno_contract_loader_init_does_not_force_sync_refresh(monkeypatch):
    """Loader init should rely on cached data and background refresh only."""

    from screener import fno_contract_loader as module
    from utils import cache as cache_module

    class FakeDB:
        def load_fno_contract_cache(self):
            return [], None

    cache_module.clear_caches()
    monkeypatch.setattr(module, "DatabaseManager", lambda: FakeDB())
    monkeypatch.setattr(module.FnoContractLoader, "_start_background_refresh", lambda self: None)
    called = {"sync": 0}

    def fake_refresh(self):
        called["sync"] += 1

    monkeypatch.setattr(module.FnoContractLoader, "_refresh_contracts_sync", fake_refresh)

    loader = module.FnoContractLoader()

    assert called["sync"] == 0
    assert loader.contracts == []

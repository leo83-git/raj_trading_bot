"""Utility module for a minimal SQLite database used by the new pipelines.

The original codebase stores a lot of data in in‑memory structures.  For the
instrument master list, market snapshots and market‑depth we now persist the
information in a small SQLite database located under ``data/``.  This module
exposes a SQLAlchemy ``engine`` and a ``Session`` factory as well as the ORM
models required by the plan.

Only the fields needed by the pipelines are modelled – additional columns can be
added later without breaking existing code.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Engine / Session setup
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("QUANT_TRADING_DB", "data/quant_trading.db")
# Ensure the directory exists – ``os.makedirs`` is safe when the folder already
# exists.
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------
class Instrument(Base):
    """Master record for a Zerodha instrument.

    Only the columns required by the new pipelines are stored.  The ``segment``
    column contains values such as ``EQ`` (equity) or ``NFO``/``NFO-OPT`` for
    futures and options.
    """

    __tablename__ = "instrument"

    instrument_token = Column(Integer, primary_key=True, index=True)
    tradingsymbol = Column(String, nullable=False, index=True)
    exchange = Column(String, nullable=False)
    segment = Column(String, nullable=False)
    instrument_type = Column(String, nullable=False)  # e.g. "EQ", "CE", "PE"
    expiry = Column(String, nullable=True)
    strike = Column(Float, nullable=True)
    lot_size = Column(Integer, nullable=True)
    tick_size = Column(Float, nullable=True)
    name = Column(String, nullable=True)


class MarketSnapshot(Base):
    """A price snapshot for a given instrument at a specific time.

    The snapshot is stored after the daily batch download (pre‑market) and is
    later compared with live WebSocket quotes.
    """

    __tablename__ = "market_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_token = Column(Integer, nullable=False, index=True)
    ltp = Column(Float, nullable=False)
    snapshot_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MarketDepth(Base):
    """Order‑book depth for an instrument.

    ``bid_prices`` and ``ask_prices`` are stored as JSON arrays of price‑size
    pairs.  The exact structure mirrors what the Kite WebSocket depth payload
    provides.
    """

    __tablename__ = "market_depth"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instrument_token = Column(Integer, nullable=False, index=True)
    bid_prices = Column(JSON, nullable=True)
    ask_prices = Column(JSON, nullable=True)
    depth_at = Column(DateTime, default=datetime.utcnow, nullable=False)


def init_db() -> None:
    """Create all tables if they do not exist.

    This function is idempotent – calling it multiple times is safe because
    ``Base.metadata.create_all`` only creates missing tables.
    """
    Base.metadata.create_all(bind=engine)


def get_session() -> Any:
    """Convenient helper returning a new SQLAlchemy session.

    Usage::

        from utils.db import get_session
        with get_session() as db:
            db.add(...)
    """
    return SessionLocal()


# Initialise the DB on import so that the tables are ready for the first run.
init_db()

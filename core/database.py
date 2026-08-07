"""Database infrastructure for the trading bot.

Provides a SQLAlchemy 2.x ORM layer for trades, positions, and critical log
events. The database backend is configured via environment variables and is
intended to work with PostgreSQL in production.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Trade(Base):
    """Executed order / fill record."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=datetime.utcnow, index=True
    )
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class Position(Base):
    """Open position snapshot."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    action: Mapped[str] = mapped_column(String(16), nullable=False, default="HOLD")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class LogEvent(Base):
    """Critical system event record."""

    __tablename__ = "log_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="bot")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=datetime.utcnow, index=True
    )


class RuntimeCheckpoint(Base):
    """Minimal restart state; high-volume telemetry is intentionally not stored."""

    __tablename__ = "runtime_checkpoints"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=datetime.utcnow
    )


class InstrumentCache(Base):
    """Cached market instrument metadata."""

    __tablename__ = "instrument_cache"

    instrument_token: Mapped[str] = mapped_column(String(32), primary_key=True)
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    tradingsymbol: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expiry: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    tick_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instrument_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    segment: Mapped[str | None] = mapped_column(String(32), nullable=True)


class FnoContractCache(Base):
    """Cached F&O contract metadata."""

    __tablename__ = "fno_contracts"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    instrument_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    segment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expiry: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    last_updated: Mapped[str | None] = mapped_column(String(32), nullable=True)


class CacheMetadata(Base):
    """Generic key/value cache metadata store."""

    __tablename__ = "cache_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(256), nullable=True)


class MarketSnapshot(Base):
    """Persisted market snapshot rows."""

    __tablename__ = "market_snapshot"

    instrument_token: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    bids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    asks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=datetime.utcnow
    )


class DatabaseManager:
    """SQLAlchemy-backed database manager."""

    def __init__(self, database_url: str | None = None, echo: bool = False) -> None:
        self.database_url = database_url or self._build_database_url()
        self.engine = self._create_engine(self.database_url, echo=echo)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(self.engine)

    def _create_engine(self, database_url: str, echo: bool = False) -> Engine:
        """Create a SQLAlchemy engine, falling back to SQLite if needed."""
        try:
            return create_engine(database_url, echo=echo, pool_pre_ping=True)
        except Exception:
            fallback_url = os.getenv("SQLITE_FALLBACK_URL", "sqlite:///:memory:")
            os.makedirs("data", exist_ok=True)
            return create_engine(fallback_url, echo=echo, future=True)

    def _build_database_url(self) -> str:
        """Build a PostgreSQL connection string from environment variables."""
        url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
        if url:
            return url

        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5433")
        db_name = os.getenv("POSTGRES_DB", "trading_bot")
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"

    @contextmanager
    def get_session(self) -> Iterator[Session]:
        """Yield a SQLAlchemy session and ensure it is cleaned up."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def save_trade(self, trade_data: dict[str, Any]) -> Trade:
        """Persist an executed trade."""
        timestamp = trade_data.get("timestamp") or datetime.now(UTC)
        trade = Trade(
            symbol=str(trade_data["symbol"]),
            quantity=int(trade_data["quantity"]),
            price=float(trade_data["price"]),
            action=str(trade_data["action"]),
            timestamp=(
                timestamp
                if isinstance(timestamp, datetime)
                else datetime.fromisoformat(str(timestamp))
            ),
            pnl=float(trade_data.get("pnl", 0.0)),
        )
        with self.get_session() as session:
            session.add(trade)
            session.flush()
            session.refresh(trade)
            return trade

    def update_position(self, position_data: dict[str, Any]) -> Position:
        """Insert or update an open position."""
        symbol = str(position_data["symbol"])
        with self.get_session() as session:
            position = session.scalar(select(Position).where(Position.symbol == symbol))
            if position is None:
                position = Position(
                    symbol=symbol,
                    quantity=int(position_data.get("quantity", 0)),
                    avg_price=float(
                        position_data.get("avg_price", position_data.get("price", 0.0))
                    ),
                    action=str(position_data.get("action", "HOLD")),
                )
                session.add(position)
            else:
                position.quantity = int(
                    position_data.get("quantity", position.quantity)
                )
                position.avg_price = float(
                    position_data.get("avg_price", position.avg_price)
                )
                position.action = str(position_data.get("action", position.action))
                position.updated_at = datetime.now(UTC)

            session.flush()
            session.refresh(position)
            return position

    def get_active_positions(self) -> list[Position]:
        """Return all currently tracked positions."""
        with self.get_session() as session:
            return list(session.scalars(select(Position)).all())

    def save_log_event(self, level: str, message: str, source: str = "bot") -> LogEvent:
        """Persist a critical event to the database."""
        event = LogEvent(level=level.upper(), message=message, source=source)
        with self.get_session() as session:
            session.add(event)
            session.flush()
            session.refresh(event)
            return event

    def save_runtime_checkpoint(
        self, key: str, payload: dict[str, Any]
    ) -> RuntimeCheckpoint | None:
        """Best-effort upsert of only the state required for safe recovery."""
        try:
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            )
            with self.get_session() as session:
                checkpoint = session.get(RuntimeCheckpoint, key)
                if checkpoint is None:
                    checkpoint = RuntimeCheckpoint(key=key, payload_json=encoded)
                    session.add(checkpoint)
                else:
                    checkpoint.payload_json = encoded
                    checkpoint.updated_at = datetime.now(UTC)
                session.flush()
                session.refresh(checkpoint)
                return checkpoint
        except SQLAlchemyError:
            return None

    def load_runtime_checkpoint(self, key: str) -> dict[str, Any] | None:
        """Load a restart checkpoint, returning ``None`` for missing/corrupt state."""
        try:
            with self.get_session() as session:
                checkpoint = session.get(RuntimeCheckpoint, key)
                if checkpoint is None:
                    return None
                value = json.loads(checkpoint.payload_json)
                return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, SQLAlchemyError, TypeError):
            return None

    def delete_runtime_checkpoint(self, key: str) -> bool:
        """Clear recovery state after broker reconciliation completes."""
        try:
            with self.get_session() as session:
                checkpoint = session.get(RuntimeCheckpoint, key)
                if checkpoint is None:
                    return False
                session.delete(checkpoint)
                return True
        except SQLAlchemyError:
            return False

    def replace_instrument_cache(self, instruments: list[dict[str, Any]]) -> int:
        """Replace the instrument cache with a fresh dump."""
        with self.get_session() as session:
            session.query(InstrumentCache).delete()
            count = 0
            for instrument in instruments:
                token = str(instrument.get("instrument_token", ""))
                if not token:
                    continue
                session.add(
                    InstrumentCache(
                        instrument_token=token,
                        exchange=instrument.get("exchange"),
                        tradingsymbol=instrument.get("tradingsymbol"),
                        name=instrument.get("name"),
                        expiry=instrument.get("expiry"),
                        strike=(
                            float(instrument["strike"])
                            if instrument.get("strike") is not None
                            else None
                        ),
                        tick_size=(
                            float(instrument["tick_size"])
                            if instrument.get("tick_size") is not None
                            else None
                        ),
                        lot_size=(
                            int(instrument["lot_size"])
                            if instrument.get("lot_size") is not None
                            else None
                        ),
                        instrument_type=instrument.get("instrument_type"),
                        segment=instrument.get("segment"),
                    )
                )
                count += 1
            return count

    def get_instrument_cache(self) -> list[dict[str, Any]]:
        """Return cached instruments in legacy dict form."""
        with self.get_session() as session:
            rows = session.scalars(select(InstrumentCache)).all()
            return [
                {
                    "instrument_token": row.instrument_token,
                    "exchange": row.exchange,
                    "tradingsymbol": row.tradingsymbol,
                    "name": row.name,
                    "expiry": row.expiry,
                    "strike": row.strike,
                    "tick_size": row.tick_size,
                    "lot_size": row.lot_size,
                    "instrument_type": row.instrument_type,
                    "segment": row.segment,
                }
                for row in rows
            ]

    def replace_fno_contract_cache(
        self, contracts: list[dict[str, Any]], last_refresh: datetime | None = None
    ) -> int:
        """Replace the F&O contract cache and metadata."""
        refresh_time = last_refresh or datetime.now(UTC)
        # Keep the last occurrence for each symbol so a noisy source payload
        # cannot violate the primary-key constraint during refresh.
        deduped_contracts: dict[str, dict[str, Any]] = {}
        for contract in contracts:
            symbol = str(contract.get("symbol", "")).strip()
            if not symbol:
                continue
            deduped_contracts[symbol] = contract

        with self.get_session() as session:
            session.query(FnoContractCache).delete()
            session.merge(
                CacheMetadata(key="last_refresh", value=refresh_time.isoformat())
            )
            count = 0
            for symbol, contract in deduped_contracts.items():
                session.add(
                    FnoContractCache(
                        symbol=symbol,
                        instrument_token=contract.get("instrument_token"),
                        exchange=contract.get("exchange"),
                        segment=contract.get("segment"),
                        expiry=contract.get("expiry"),
                        strike=(
                            float(contract["strike"])
                            if contract.get("strike") is not None
                            else None
                        ),
                        option_type=contract.get("option_type"),
                        last_updated=refresh_time.isoformat(),
                    )
                )
                count += 1
            return count

    def load_fno_contract_cache(self) -> tuple[list[dict[str, Any]], datetime | None]:
        """Load cached F&O contracts and the last refresh timestamp."""
        with self.get_session() as session:
            metadata = session.get(CacheMetadata, "last_refresh")
            last_refresh = None
            if metadata and metadata.value:
                try:
                    last_refresh = datetime.fromisoformat(metadata.value)
                except ValueError:
                    last_refresh = None

            rows = session.scalars(select(FnoContractCache)).all()
            contracts = [
                {
                    "symbol": row.symbol,
                    "instrument_token": row.instrument_token,
                    "exchange": row.exchange,
                    "segment": row.segment,
                    "expiry": row.expiry,
                    "strike": row.strike,
                    "option_type": row.option_type,
                }
                for row in rows
            ]
            return contracts, last_refresh

    def replace_market_snapshot(self, rows: list[dict[str, Any]]) -> int:
        """Replace the stored market snapshot."""
        with self.get_session() as session:
            session.query(MarketSnapshot).delete()
            count = 0
            for row in rows:
                token = row.get("instrument_token")
                if token is None:
                    continue
                session.add(
                    MarketSnapshot(
                        instrument_token=int(token),
                        symbol=row.get("symbol"),
                        exchange=row.get("exchange"),
                        last_price=(
                            float(row["last_price"])
                            if row.get("last_price") is not None
                            else None
                        ),
                        bids_json=row.get("bids_json"),
                        asks_json=row.get("asks_json"),
                        timestamp=row.get("timestamp") or datetime.now(UTC),
                    )
                )
                count += 1
            return count

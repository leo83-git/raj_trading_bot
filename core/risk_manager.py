"""Compatibility import for the authoritative risk manager implementation."""

from risk.risk_manager import CircuitBreakerTriggered, RiskManager

__all__ = ["CircuitBreakerTriggered", "RiskManager"]

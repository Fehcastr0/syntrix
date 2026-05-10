"""
Broker adapter — conexão IQ Option via iqbroker.
Simples e direto.
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass
from typing import Any, List, Optional

from scanner.indicators import Candle

logger = logging.getLogger("syntrix-lite")

# Try to import broker API
_IQ: Any = None
try:
    from iqbroker.stable_api import IQ_Option
    _IQ = IQ_Option
except Exception:
    try:
        from iqoptionapi.stable_api import IQ_Option
        _IQ = IQ_Option
    except Exception:
        pass


@dataclass
class TradeResult:
    success: bool = False
    trade_id: str = ""
    broker_id: int = 0
    result: str = ""  # "win", "loss", "tie"
    profit: float = 0.0
    error: str = ""
    latency_ms: float = 0.0


class BrokerAdapter:
    """IQ Option broker connection."""

    def __init__(self, email: str, password: str, practice: bool = True) -> None:
        self._email = email
        self._password = password
        self._practice = practice
        self._api: Any = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        if _IQ is None:
            logger.error("No broker API available (install iqbroker)")
            return False
        try:
            self._api = _IQ(self._email, self._password)
            check, reason = self._api.connect()
            if check:
                account_type = "PRACTICE" if self._practice else "REAL"
                self._api.change_balance(account_type)
                self._connected = True
                logger.info("Broker connected (%s)", account_type)
                return True
            else:
                logger.error("Broker connect failed: %s", reason)
                return False
        except Exception as e:
            logger.error("Broker error: %s", e)
            return False

    def disconnect(self) -> None:
        if self._api:
            try:
                self._api.disconnect()
            except Exception:
                pass
        self._connected = False

    def get_balance(self) -> float:
        if not self._api:
            return 0.0
        try:
            return float(self._api.get_balance())
        except Exception:
            logger.warning("get_balance error: %s", traceback.format_exc())
            return 0.0

    def get_candles(self, asset: str, timeframe: int = 60, count: int = 50) -> List[Candle]:
        if not self._api:
            return []
        try:
            raw = self._api.get_candles(asset, timeframe, count, time.time())
            candles = []
            for c in raw:
                candles.append(Candle(
                    open=float(c.get("open", 0)),
                    high=float(c.get("max", 0)),
                    low=float(c.get("min", 0)),
                    close=float(c.get("close", 0)),
                    timestamp=float(c.get("from", 0)),
                ))
            return candles
        except Exception as e:
            logger.warning("Candles error %s: %s", asset, e)
            return []

    def get_payout(self, asset: str) -> float:
        if not self._api:
            return 0.0
        try:
            data = self._api.get_all_profit()
            if asset in data:
                return float(data[asset].get("turbo", 0)) / 100.0
            return 0.0
        except Exception:
            logger.warning("Payout error %s: %s", asset, traceback.format_exc())
            return 0.0

    def buy(self, asset: str, amount: float, direction: str, duration: int) -> TradeResult:
        """Execute a binary options trade."""
        if not self._api:
            return TradeResult(error="Not connected")

        start = time.time()
        try:
            action = "call" if direction == "call" else "put"
            success, trade_id = self._api.buy(amount, asset, action, duration)
            latency = (time.time() - start) * 1000

            if success:
                return TradeResult(
                    success=True,
                    broker_id=trade_id,
                    trade_id=str(trade_id),
                    latency_ms=latency,
                )
            else:
                return TradeResult(error=f"Buy failed: {trade_id}", latency_ms=latency)
        except Exception as e:
            return TradeResult(error=str(e))

    def check_result(self, trade_id: int) -> TradeResult:
        """Wait for and check trade result."""
        if not self._api:
            return TradeResult(error="Not connected")
        try:
            result = self._api.check_win_v4(trade_id)
            if isinstance(result, (int, float)):
                profit = float(result)
                return TradeResult(
                    success=True,
                    broker_id=trade_id,
                    trade_id=str(trade_id),
                    result="win" if profit > 0 else ("tie" if profit == 0 else "loss"),
                    profit=profit,
                )
            return TradeResult(error="Unknown result format")
        except Exception as e:
            return TradeResult(error=str(e))

    def get_open_assets(self) -> List[str]:
        """Get list of currently open assets."""
        if not self._api:
            return []
        try:
            self._api.update_ACTIVES_OPCODE()
            open_assets = []
            turbo = self._api.get_all_open_time().get("turbo", {})
            for asset_name, info in turbo.items():
                if info.get("open"):
                    open_assets.append(asset_name)
            return open_assets
        except Exception:
            logger.warning("get_open_assets error: %s", traceback.format_exc())
            return []

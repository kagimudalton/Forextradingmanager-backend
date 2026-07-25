"""
mt5_connector.py

Wraps the official `MetaTrader5` Python package.

IMPORTANT PLATFORM NOTE:
The `MetaTrader5` package only works on Windows, because it talks to a
locally-installed MT5 terminal over a native IPC channel. There is no
official Linux/Mac build. Because of that, this module:

  1. Tries to `import MetaTrader5` for real use on a Windows host that has
     the MT5 terminal installed.
  2. Falls back to a deterministic MOCK data provider if the package or
     terminal isn't available (e.g. during development on Linux/Mac, or in
     CI). This keeps the rest of the platform (API, frontend, DB) fully
     testable without a live broker connection.

Swap MOCK_MODE off by installing `MetaTrader5` on Windows and setting
MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in your environment (see README).
"""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    import MetaTrader5 as mt5  # type: ignore
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False


class MT5Connector:
    def __init__(self):
        self.connected = False
        self.login = os.getenv("MT5_LOGIN")
        self.password = os.getenv("MT5_PASSWORD")
        self.server = os.getenv("MT5_SERVER")
        self.mock_mode = not MT5_AVAILABLE or not self.login

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def initialize(self) -> bool:
        if self.mock_mode:
            self.connected = True
            return True

        ok = mt5.initialize()
        if not ok:
            return False

        if self.login and self.password and self.server:
            ok = mt5.login(int(self.login), password=self.password, server=self.server)

        self.connected = bool(ok)
        return self.connected

    def shutdown(self):
        if not self.mock_mode and MT5_AVAILABLE:
            mt5.shutdown()
        self.connected = False

    # ------------------------------------------------------------------
    # Account / positions
    # ------------------------------------------------------------------
    def get_account(self) -> dict:
        if self.mock_mode:
            return {
                "balance": 10432.55,
                "equity": 10611.20,
                "margin": 210.00,
                "free_margin": 10401.20,
                "margin_level": 5053.9,
                "currency": "USD",
                "leverage": 100,
                "server": "MockBroker-Demo",
                "mock": True,
            }

        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 account_info() returned None - not connected")
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "margin_level": info.margin_level,
            "currency": info.currency,
            "leverage": info.leverage,
            "server": info.server,
            "mock": False,
        }

    def get_positions(self) -> list[dict]:
        if self.mock_mode:
            return [
                {
                    "ticket": 1001,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "volume": 0.10,
                    "open_price": 2385.20,
                    "current_price": 2391.75,
                    "profit": 65.50,
                },
                {
                    "ticket": 1002,
                    "symbol": "EURUSD",
                    "type": "SELL",
                    "volume": 0.25,
                    "open_price": 1.0862,
                    "current_price": 1.0849,
                    "profit": 32.50,
                },
            ]

        positions = mt5.positions_get()
        if positions is None:
            return []
        out = []
        for p in positions:
            out.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "BUY" if p.type == 0 else "SELL",
                "volume": p.volume,
                "open_price": p.price_open,
                "current_price": p.price_current,
                "profit": p.profit,
            })
        return out

    def get_history(self, days: int = 30) -> list[dict]:
        if self.mock_mode:
            base = datetime.utcnow()
            history = []
            for i in range(10):
                history.append({
                    "ticket": 900 + i,
                    "symbol": random.choice(["XAUUSD", "EURUSD", "NAS100"]),
                    "type": random.choice(["BUY", "SELL"]),
                    "volume": round(random.uniform(0.05, 0.5), 2),
                    "profit": round(random.uniform(-40, 90), 2),
                    "closed_at": (base - timedelta(days=i)).isoformat(),
                })
            return history

        date_from = datetime.now() - timedelta(days=days)
        deals = mt5.history_deals_get(date_from, datetime.now())
        if deals is None:
            return []
        return [{
            "ticket": d.ticket,
            "symbol": d.symbol,
            "type": "BUY" if d.type == 0 else "SELL",
            "volume": d.volume,
            "profit": d.profit,
            "closed_at": datetime.fromtimestamp(d.time).isoformat(),
        } for d in deals]

    # ------------------------------------------------------------------
    # Price data
    # ------------------------------------------------------------------
    def get_rates(self, symbol: str, timeframe: str = "M15", count: int = 100) -> list[dict]:
        """Return OHLC candles, newest last."""
        if self.mock_mode:
            return self._mock_rates(symbol, count)

        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        rates = mt5.copy_rates_from_pos(symbol, tf_map.get(timeframe, mt5.TIMEFRAME_M15), 0, count)
        if rates is None:
            return []
        return [{
            "time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]), "volume": int(r["tick_volume"]),
        } for r in rates]

    def _mock_rates(self, symbol: str, count: int) -> list[dict]:
        seed = sum(ord(c) for c in symbol)
        rng = random.Random(seed + int(time.time() // 3600))  # stable within the hour
        base_prices = {"XAUUSD": 2390.0, "EURUSD": 1.086, "NAS100": 19800.0, "GBPUSD": 1.271, "BTCUSD": 63500.0}
        price = base_prices.get(symbol, 100.0)
        candles = []
        now = int(time.time())
        for i in range(count):
            change = rng.uniform(-0.4, 0.45) * (price * 0.0015)
            open_p = price
            close_p = price + change
            high_p = max(open_p, close_p) + abs(change) * rng.uniform(0.1, 0.5)
            low_p = min(open_p, close_p) - abs(change) * rng.uniform(0.1, 0.5)
            candles.append({
                "time": now - (count - i) * 900,
                "open": round(open_p, 4), "high": round(high_p, 4),
                "low": round(low_p, 4), "close": round(close_p, 4),
                "volume": rng.randint(50, 500),
            })
            price = close_p
        return candles

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def open_trade(self, symbol: str, direction: str, volume: float,
                    sl: Optional[float] = None, tp: Optional[float] = None) -> dict:
        if self.mock_mode:
            price = self._mock_rates(symbol, 1)[0]["close"]
            return {
                "success": True, "ticket": random.randint(100000, 999999),
                "symbol": symbol, "type": direction, "volume": volume,
                "price": price, "sl": sl, "tp": tp, "mock": True,
            }

        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if direction == "BUY" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl or 0.0,
            "tp": tp or 0.0,
            "deviation": 20,
            "magic": 20260101,
            "comment": "AI dashboard order",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return {
            "success": result.retcode == mt5.TRADE_RETCODE_DONE,
            "ticket": result.order, "symbol": symbol, "type": direction,
            "volume": volume, "price": price, "sl": sl, "tp": tp,
            "retcode": result.retcode, "mock": False,
        }

    def close_trade(self, ticket: int) -> dict:
        if self.mock_mode:
            return {"success": True, "ticket": ticket, "mock": True}

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return {"success": False, "error": "Position not found"}
        pos = positions[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if pos.type == 0 else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 20260101,
            "comment": "AI dashboard close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        return {"success": result.retcode == mt5.TRADE_RETCODE_DONE, "ticket": ticket, "retcode": result.retcode}


# Singleton instance used across the app
connector = MT5Connector()

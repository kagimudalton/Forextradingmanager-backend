"""
mt5_connector.py

Wraps the MetaApi cloud SDK (https://metaapi.cloud) to get real-time MT5
account data, positions, prices, and trade execution — from a plain Linux
host like Render. The old `MetaTrader5` Python package only runs on
Windows next to a live MT5 terminal, which Render can't do; MetaApi runs
the terminal in their cloud and exposes it over an async API instead.

Falls back to deterministic MOCK data if METAAPI_TOKEN / METAAPI_ACCOUNT_ID
aren't set, so local development and the rest of the platform keep working
without a live broker connection.

Every method here is now `async` — callers (routers, bot_engine, etc.) need
`await connector.get_account()` instead of `connector.get_account()`.
"""
from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta
from typing import Optional

try:
    from metaapi_cloud_sdk import MetaApi  # type: ignore
    METAAPI_AVAILABLE = True
except ImportError:
    MetaApi = None
    METAAPI_AVAILABLE = False


class MT5Connector:
    def __init__(self):
        self.token = os.getenv("METAAPI_TOKEN")
        self.account_id = os.getenv("METAAPI_ACCOUNT_ID")
        self.mock_mode = not METAAPI_AVAILABLE or not self.token or not self.account_id

        self.api: Optional["MetaApi"] = None
        self.account = None
        self.connection = None
        self.connected = False

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    async def initialize(self) -> bool:
        if self.mock_mode:
            self.connected = True
            return True

        self.api = MetaApi(token=self.token)
        self.account = await self.api.metatrader_account_api.get_account(self.account_id)

        # Make sure the MetaApi-hosted terminal is actually deployed and running
        if self.account.state not in ("DEPLOYED",):
            await self.account.deploy()
        await self.account.wait_connected()

        self.connection = self.account.get_rpc_connection()
        await self.connection.connect()
        await self.connection.wait_synchronized()

        self.connected = True
        return True

    async def shutdown(self):
        if not self.mock_mode and self.connection:
            await self.connection.close()
        self.connected = False

    # ------------------------------------------------------------------
    # Account / positions
    # ------------------------------------------------------------------
    async def get_account(self) -> dict:
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

        info = await self.connection.get_account_information()
        return {
            "balance": info["balance"],
            "equity": info["equity"],
            "margin": info["margin"],
            "free_margin": info["freeMargin"],
            "margin_level": info.get("marginLevel", 0),
            "currency": info["currency"],
            "leverage": info["leverage"],
            "server": info.get("server", ""),
            "mock": False,
        }

    async def get_positions(self) -> list[dict]:
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

        positions = await self.connection.get_positions()
        return [{
            "ticket": p["id"],
            "symbol": p["symbol"],
            "type": p["type"].replace("POSITION_TYPE_", ""),  # e.g. POSITION_TYPE_BUY -> BUY
            "volume": p["volume"],
            "open_price": p["openPrice"],
            "current_price": p["currentPrice"],
            "profit": p["profit"],
        } for p in positions]

    async def get_history(self, days: int = 30) -> list[dict]:
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

        start_time = datetime.now() - timedelta(days=days)
        deals = await self.connection.get_deals_by_time_range(start_time, datetime.now())
        return [{
            "ticket": d["id"],
            "symbol": d.get("symbol", ""),
            "type": d.get("type", "").replace("DEAL_TYPE_", ""),
            "volume": d.get("volume", 0),
            "profit": d.get("profit", 0),
            "closed_at": d["time"].isoformat() if hasattr(d["time"], "isoformat") else d["time"],
        } for d in deals]

    # ------------------------------------------------------------------
    # Price data
    # ------------------------------------------------------------------
    async def get_rates(self, symbol: str, timeframe: str = "M15", count: int = 100) -> list[dict]:
        """Return OHLC candles, newest last."""
        if self.mock_mode:
            return self._mock_rates(symbol, count)

        # MetaApi's historical candles endpoint is only guaranteed on G1
        # infrastructure; if your account is G2 this may raise — in that
        # case fall back to building candles from get_symbol_price polling,
        # or ask MetaApi support which tier your account is on.
        tf_map = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h", "D1": "1d"}
        candles = await self.account.get_historical_candles(
            symbol=symbol, timeframe=tf_map.get(timeframe, "15m"),
            start_time=datetime.now(), limit=count,
        )
        return [{
            "time": int(c["time"].timestamp()) if hasattr(c["time"], "timestamp") else c["time"],
            "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
            "volume": c.get("tickVolume", 0),
        } for c in candles]

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
    async def open_trade(self, symbol: str, direction: str, volume: float,
                          sl: Optional[float] = None, tp: Optional[float] = None) -> dict:
        if self.mock_mode:
            price = self._mock_rates(symbol, 1)[0]["close"]
            return {
                "success": True, "ticket": random.randint(100000, 999999),
                "symbol": symbol, "type": direction, "volume": volume,
                "price": price, "sl": sl, "tp": tp, "mock": True,
            }

        if direction == "BUY":
            result = await self.connection.create_market_buy_order(
                symbol=symbol, volume=volume, stop_loss=sl, take_profit=tp,
                options={"comment": "AI dashboard order"},
            )
        else:
            result = await self.connection.create_market_sell_order(
                symbol=symbol, volume=volume, stop_loss=sl, take_profit=tp,
                options={"comment": "AI dashboard order"},
            )

        return {
            "success": result.get("numericCode") == 0,
            "ticket": result.get("orderId") or result.get("positionId"),
            "symbol": symbol, "type": direction, "volume": volume,
            "sl": sl, "tp": tp,
            "retcode": result.get("stringCode"), "mock": False,
        }

    async def close_trade(self, ticket: int) -> dict:
        if self.mock_mode:
            return {"success": True, "ticket": ticket, "mock": True}

        result = await self.connection.close_position(position_id=str(ticket))
        return {
            "success": result.get("numericCode") == 0,
            "ticket": ticket,
            "retcode": result.get("stringCode"),
        }


# Singleton instance used across the app
connector = MT5Connector()

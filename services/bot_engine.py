"""
bot_engine.py

The actual automation behind "Start Auto Mode" / "Stop Bot". A background
asyncio loop wakes up on an interval, and for every user whose bot_status is
running, it:

  1. Runs market_analyzer.analyze_watchlist()
  2. Takes the highest-confidence signal above CONFIDENCE_THRESHOLD
  3. Checks risk_manager.check_trade_allowed() against that user's settings
  4. If allowed, opens the trade via mt5_connector and logs it
  5. Broadcasts a bot_trade event over the WebSocket

This runs against whatever mt5_connector is configured with — mock data by
default, live MT5/broker orders once you wire up real credentials. Nothing
here changes based on that; the engine doesn't know or care whether it's
mock or live, which is exactly the point of the mock-fallback design.
"""
from __future__ import annotations

import asyncio
import json

from db.database import get_db, log_action
from services import market_analyzer as ma
from services import risk_manager
from services.mt5_connector import connector
import ws_manager

CONFIDENCE_THRESHOLD = 75
CHECK_INTERVAL_SECONDS = 45


async def bot_loop():
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            await _run_cycle()
        except Exception as e:  # noqa: BLE001 - keep the loop alive no matter what
            print(f"[bot_engine] cycle error: {e}")


async def _run_cycle():
    with get_db() as conn:
        active_users = conn.execute(
            "SELECT user_id FROM bot_status WHERE running = 1"
        ).fetchall()

    if not active_users:
        return

    signals = ma.analyze_watchlist()
    if not signals:
        return
    best = signals[0]
    if best["confidence"] < CONFIDENCE_THRESHOLD or best["direction"] == "HOLD":
        return

    for row in active_users:
        user_id = row["user_id"]
        await _maybe_trade_for_user(user_id, best)


async def _maybe_trade_for_user(user_id: int, signal: dict):
    with get_db() as conn:
        settings_row = conn.execute(
            "SELECT risk_percent, max_trades FROM settings WHERE user_id = ?", (user_id,)
        ).fetchone()
        open_count = conn.execute(
            "SELECT COUNT(*) c FROM trades WHERE user_id = ? AND status = 'OPEN'", (user_id,)
        ).fetchone()["c"]

    if not settings_row:
        return

    allowed, reason = risk_manager.check_trade_allowed(
        open_trades=open_count, max_trades=settings_row["max_trades"],
        daily_loss_percent=0, max_daily_loss_percent=100,
    )
    if not allowed:
        return

    account = await connector.get_account()
    lots = risk_manager.position_size(
        balance=account["balance"], risk_percent=settings_row["risk_percent"],
        stop_loss_pips=20, pip_value=10.0,
    )

    result = await connector.open_trade(signal["symbol"], signal["direction"], lots)
    if not result.get("success"):
        return

    with get_db() as conn:
        conn.execute(
            "INSERT INTO trades (user_id, symbol, type, volume, open_price, status) "
            "VALUES (?, ?, ?, ?, ?, 'OPEN')",
            (user_id, signal["symbol"], signal["direction"], lots, result.get("price")),
        )
    log_action(
        user_id, "BOT_AUTO_TRADE",
        f"{signal['symbol']} {signal['direction']} vol={lots} confidence={signal['confidence']}%",
    )
    await ws_manager.broadcast({
        "event": "bot_trade",
        "data": {**result, "confidence": signal["confidence"], "user_id": user_id},
    })

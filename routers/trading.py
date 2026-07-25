from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel

from services.security import get_current_user
from services.mt5_connector import connector
from services import risk_manager
from db.database import get_db, log_action
from ws_manager import broadcast

router = APIRouter(tags=["trading"])


def _auth(session_token: str | None = Cookie(default=None)):
    user = get_current_user(session_token)
    if user["role"] not in ("ADMIN", "TRADER"):
        raise HTTPException(403, "TRADER or ADMIN role required")
    return user


class TradeRequest(BaseModel):
    symbol: str
    volume: float = 0.10
    sl: float | None = None
    tp: float | None = None


@router.post("/trade/buy")
async def trade_buy(payload: TradeRequest, user=Depends(_auth)):
    return await _execute_trade(payload, "BUY", user)


@router.post("/trade/sell")
async def trade_sell(payload: TradeRequest, user=Depends(_auth)):
    return await _execute_trade(payload, "SELL", user)


async def _execute_trade(payload: TradeRequest, direction: str, user: dict):
    with get_db() as conn:
        settings_row = conn.execute(
            "SELECT max_trades FROM settings WHERE user_id = ?", (user["id"],)
        ).fetchone()
        open_count = conn.execute(
            "SELECT COUNT(*) c FROM trades WHERE user_id = ? AND status = 'OPEN'", (user["id"],)
        ).fetchone()["c"]

    max_trades = settings_row["max_trades"] if settings_row else 5
    allowed, reason = risk_manager.check_trade_allowed(
        open_trades=open_count, max_trades=max_trades,
        daily_loss_percent=0, max_daily_loss_percent=100,
    )
    if not allowed:
        raise HTTPException(400, reason)

    result = await connector.open_trade(payload.symbol, direction, payload.volume, payload.sl, payload.tp)
    if not result.get("success"):
        raise HTTPException(400, f"Order failed: {result}")

    with get_db() as conn:
        conn.execute(
            "INSERT INTO trades (user_id, symbol, type, volume, open_price, status) "
            "VALUES (?, ?, ?, ?, ?, 'OPEN')",
            (user["id"], payload.symbol, direction, payload.volume, result.get("price")),
        )
    log_action(user["id"], f"TRADE_{direction}", f"{payload.symbol} vol={payload.volume}")
    await broadcast({"event": "trade_opened", "data": result})
    return result


class CloseRequest(BaseModel):
    ticket: int


@router.post("/trade/close")
async def trade_close(payload: CloseRequest, user=Depends(_auth)):
    result = await connector.close_trade(payload.ticket)
    if not result.get("success"):
        raise HTTPException(400, f"Close failed: {result}")

    with get_db() as conn:
        conn.execute(
            "UPDATE trades SET status = 'CLOSED', closed_at = datetime('now') "
            "WHERE user_id = ? AND id = (SELECT id FROM trades WHERE user_id = ? "
            "AND status = 'OPEN' ORDER BY id DESC LIMIT 1)",
            (user["id"], user["id"]),
        )
    log_action(user["id"], "TRADE_CLOSE", f"ticket={payload.ticket}")
    await broadcast({"event": "trade_closed", "data": result})
    return result


# --- Bot control -----------------------------------------------------------

@router.post("/bot/start")
async def bot_start(user=Depends(_auth)):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO bot_status (user_id, running, updated_at) VALUES (?, 1, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET running = 1, updated_at = datetime('now')",
            (user["id"],),
        )
    log_action(user["id"], "BOT_START")
    await broadcast({"event": "bot_status", "data": {"running": True}})
    return {"running": True}


@router.post("/bot/stop")
async def bot_stop(user=Depends(_auth)):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO bot_status (user_id, running, updated_at) VALUES (?, 0, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET running = 0, updated_at = datetime('now')",
            (user["id"],),
        )
    log_action(user["id"], "BOT_STOP")
    await broadcast({"event": "bot_status", "data": {"running": False}})
    return {"running": False}


@router.get("/bot/status")
def bot_status(user=Depends(_auth)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT running FROM bot_status WHERE user_id = ?", (user["id"],)
        ).fetchone()
    return {"running": bool(row["running"]) if row else False}

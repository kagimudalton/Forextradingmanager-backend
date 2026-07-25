from fastapi import APIRouter, Cookie, Depends
from services.security import get_current_user
from services.mt5_connector import connector
from services import risk_manager
from db.database import get_db

router = APIRouter(tags=["dashboard"])


def _auth(session_token: str | None = Cookie(default=None)):
    return get_current_user(session_token)


@router.get("/account")
async def account(user=Depends(_auth)):
    return await connector.get_account()


@router.get("/positions")
async def positions(user=Depends(_auth)):
    return await connector.get_positions()


@router.get("/history")
async def history(days: int = 30, user=Depends(_auth)):
    return await connector.get_history(days=days)


@router.get("/risk-status")
async def risk_status(user=Depends(_auth)):
    acct = await connector.get_account()
    positions_list = await connector.get_positions()
    with get_db() as conn:
        row = conn.execute(
            "SELECT risk_percent, max_trades, max_daily_loss_percent FROM settings WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
    risk_percent = row["risk_percent"] if row else 1.0
    max_trades = row["max_trades"] if row else 5
    max_daily_loss = row["max_daily_loss_percent"] if row else 5.0
    today_loss = sum(p["profit"] for p in positions_list if p["profit"] < 0)
    return risk_manager.risk_status(
        balance=acct["balance"], equity=acct["equity"],
        open_trades=len(positions_list), max_trades=max_trades,
        risk_percent=risk_percent, today_loss=today_loss,
        max_daily_loss_percent=max_daily_loss,
    )


@router.get("/settings")
def get_settings(user=Depends(_auth)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT risk_percent, max_trades, max_daily_loss_percent FROM settings WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
    if not row:
        return {"risk_percent": 1.0, "max_trades": 5, "max_daily_loss_percent": 5.0}
    return dict(row)


class SettingsPayload(dict):
    pass


from pydantic import BaseModel


class SettingsUpdate(BaseModel):
    risk_percent: float
    max_trades: int
    max_daily_loss_percent: float = 5.0


@router.post("/settings")
def update_settings(payload: SettingsUpdate, user=Depends(_auth)):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO settings (user_id, risk_percent, max_trades, max_daily_loss_percent)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 risk_percent = excluded.risk_percent,
                 max_trades = excluded.max_trades,
                 max_daily_loss_percent = excluded.max_daily_loss_percent""",
            (user["id"], payload.risk_percent, payload.max_trades, payload.max_daily_loss_percent),
        )
    return {"ok": True}

from fastapi import APIRouter, Cookie, Depends

from services.security import get_current_user
from db.database import get_db

router = APIRouter(tags=["reports"])


def _auth(session_token: str | None = Cookie(default=None)):
    return get_current_user(session_token)


@router.get("/reports/summary")
def reports_summary(user=Depends(_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT symbol, type, volume, profit, status, opened_at, closed_at "
            "FROM trades WHERE user_id = ? ORDER BY opened_at DESC",
            (user["id"],),
        ).fetchall()

    trades = [dict(r) for r in rows]
    closed = [t for t in trades if t["status"] == "CLOSED" and t["profit"] is not None]

    total_trades = len(trades)
    closed_count = len(closed)
    wins = [t for t in closed if t["profit"] > 0]
    losses = [t for t in closed if t["profit"] <= 0]
    total_pnl = round(sum(t["profit"] for t in closed), 2) if closed else 0.0
    win_rate = round(len(wins) / closed_count * 100, 1) if closed_count else 0.0
    avg_win = round(sum(t["profit"] for t in wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(t["profit"] for t in losses) / len(losses), 2) if losses else 0.0
    best_trade = max((t["profit"] for t in closed), default=0.0)
    worst_trade = min((t["profit"] for t in closed), default=0.0)

    # Per-symbol breakdown
    by_symbol: dict[str, dict] = {}
    for t in closed:
        s = by_symbol.setdefault(t["symbol"], {"symbol": t["symbol"], "trades": 0, "pnl": 0.0})
        s["trades"] += 1
        s["pnl"] += t["profit"]
    for s in by_symbol.values():
        s["pnl"] = round(s["pnl"], 2)

    return {
        "total_trades": total_trades,
        "closed_trades": closed_count,
        "open_trades": total_trades - closed_count,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "by_symbol": sorted(by_symbol.values(), key=lambda x: x["pnl"], reverse=True),
    }


@router.get("/reports/trades")
def reports_trades(limit: int = 50, user=Depends(_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, symbol, type, volume, open_price, close_price, profit, status, "
            "opened_at, closed_at FROM trades WHERE user_id = ? "
            "ORDER BY opened_at DESC LIMIT ?",
            (user["id"], limit),
        ).fetchall()
    return [dict(r) for r in rows]

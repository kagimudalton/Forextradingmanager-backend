import json
from fastapi import APIRouter, Cookie, Depends

from services.security import get_current_user
from services import market_analyzer
from db.database import get_db, log_action
from ws_manager import broadcast

router = APIRouter(tags=["analysis"])


def _auth(session_token: str | None = Cookie(default=None)):
    return get_current_user(session_token)


@router.post("/analyze")
async def analyze(user=Depends(_auth)):
    results = market_analyzer.analyze_watchlist()

    with get_db() as conn:
        for r in results:
            conn.execute(
                "INSERT INTO signals (symbol, direction, confidence, risk, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (r["symbol"], r["direction"], r["confidence"], r["risk"], json.dumps(r["reason"])),
            )
    log_action(user["id"], "ANALYZE_MARKETS", f"{len(results)} symbols analyzed")

    await broadcast({"event": "analysis_complete", "data": results})
    return results


@router.get("/signals")
def signals(limit: int = 20, user=Depends(_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT symbol, direction, confidence, risk, reason, created_at "
            "FROM signals ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["reason"] = json.loads(d["reason"]) if d["reason"] else []
        out.append(d)
    return out

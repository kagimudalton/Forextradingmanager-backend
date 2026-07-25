from fastapi import APIRouter, Cookie, Depends

from services.security import get_current_user
from services.mt5_connector import connector
from services import market_analyzer as ma

router = APIRouter(tags=["scanner"])

# Broader universe than the AI Signals watchlist — Market Scanner is meant
# for browsing, AI Signals (/api/signals) is the curated, persisted list.
SCANNER_UNIVERSE = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "NZDUSD", "USDCAD", "NAS100", "US30", "SPX500", "BTCUSD", "ETHUSD",
]


def _auth(session_token: str | None = Cookie(default=None)):
    return get_current_user(session_token)


@router.get("/scanner")
async def scan_market(user=Depends(_auth)):
    results = []
    for symbol in SCANNER_UNIVERSE:
        analysis = ma.analyze_symbol(symbol)
        candles = await connector.get_rates(symbol, count=2)
        change_pct = 0.0
        if len(candles) == 2 and candles[0]["close"]:
            change_pct = round((candles[1]["close"] - candles[0]["close"]) / candles[0]["close"] * 100, 3)
        results.append({
            **analysis,
            "change_pct": change_pct,
        })
    results.sort(key=lambda r: r["confidence"], reverse=True)
    return results

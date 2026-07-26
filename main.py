import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from db.database import init_db, get_db
from services.security import hash_password
from services.mt5_connector import connector
import ws_manager

from routers import auth, dashboard, analysis, trading, admin, reports, scanner
from services import bot_engine

app = FastAPI(title="Private AI MT5 Trading Intelligence Dashboard — API")

# This backend is API-only. The frontend is deployed separately on GitHub
# Pages, so CORS must explicitly allow that origin (and cookies must be
# sent as "credentials: include" from the frontend, which api.js already does).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://kagimudalton.github.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(trading.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(scanner.router, prefix="/api")
app.include_router(emergency_reactivate.router, prefix="/api")


@app.get("/")
def health_check():
    """Simple root route so hitting the bare Render URL doesn't 404 —
    useful for confirming the service is alive."""
    return {"status": "ok", "service": "Forex Trading Manager API"}


@app.on_event("startup")
async def startup():
    init_db()
    # connector.initialize() is now async — it connects to MetaApi's cloud
    # terminal (or drops into mock mode if METAAPI_TOKEN/ACCOUNT_ID aren't
    # set). This can take a few seconds on first boot while MetaApi deploys
    # the cloud terminal, so don't be surprised if startup takes a bit longer
    # than before.
    await connector.initialize()
    _seed_admin()


def _seed_admin():
    """Create a default admin account on first run only (dev convenience)."""
    admin_user = os.getenv("SEED_ADMIN_USER", "admin")
    admin_pass = os.getenv("SEED_ADMIN_PASS", "ChangeMe123!")
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users").fetchone()
        if existing:
            return
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'ADMIN')",
            (admin_user, hash_password(admin_pass)),
        )
        conn.execute(
            "INSERT INTO settings (user_id, risk_percent, max_trades) VALUES (?, 1.0, 5)",
            (cur.lastrowid,),
        )
    print(f"[seed] Created default admin '{admin_user}' — change the password after first login.")


# ---------------------------------------------------------------------------
# WebSocket: real-time events (prices, signals, trades, bot status)
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive / ignore client pings
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


async def _price_ticker():
    """Background task pushing lightweight price ticks to connected clients."""
    symbols = ["XAUUSD", "EURUSD", "NAS100"]
    while True:
        await asyncio.sleep(5)
        if not ws_manager.active_connections:
            continue
        for s in symbols:
            candle = await connector.get_rates(s, count=1)
            if candle:
                await ws_manager.broadcast({
                    "event": "price_update",
                    "data": {"symbol": s, "price": candle[-1]["close"]},
                })


@app.on_event("startup")
async def start_background_tasks():
    asyncio.create_task(_price_ticker())
    asyncio.create_task(bot_engine.bot_loop())

"""
Lightweight WebSocket broadcast manager for real-time dashboard events:
price updates, signal generation, trade opened/closed, bot status.
"""
import json
from fastapi import WebSocket

active_connections: list[WebSocket] = []


async def connect(ws: WebSocket):
    await ws.accept()
    active_connections.append(ws)


def disconnect(ws: WebSocket):
    if ws in active_connections:
        active_connections.remove(ws)


async def broadcast(message: dict):
    payload = json.dumps(message)
    dead = []
    for conn in active_connections:
        try:
            await conn.send_text(payload)
        except Exception:
            dead.append(conn)
    for d in dead:
        disconnect(d)

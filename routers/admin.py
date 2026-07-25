from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel

from services.security import get_current_user, hash_password
from services.mt5_connector import connector
from db.database import get_db, log_action

router = APIRouter(tags=["admin"])


def _admin_only(session_token: str | None = Cookie(default=None)):
    user = get_current_user(session_token)
    if user["role"] != "ADMIN":
        raise HTTPException(403, "Admin role required")
    return user


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "TRADER"  # ADMIN | TRADER | VIEWER


@router.post("/users/create")
def create_user(payload: CreateUserRequest, admin=Depends(_admin_only)):
    if payload.role not in ("ADMIN", "TRADER", "VIEWER"):
        raise HTTPException(400, "Invalid role")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (payload.username,)
        ).fetchone()
        if existing:
            raise HTTPException(400, "Username already exists")
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (payload.username, hash_password(payload.password), payload.role),
        )
        new_id = cur.lastrowid
        conn.execute(
            "INSERT INTO settings (user_id, risk_percent, max_trades) VALUES (?, 1.0, 5)",
            (new_id,),
        )
    log_action(admin["id"], "USER_CREATE", f"created '{payload.username}' as {payload.role}")
    return {"id": new_id, "username": payload.username, "role": payload.role}


@router.get("/users")
def list_users(admin=Depends(_admin_only)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin=Depends(_admin_only)):
    if user_id == admin["id"]:
        raise HTTPException(400, "Cannot delete your own account")
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    log_action(admin["id"], "USER_DELETE", f"deleted user_id={user_id}")
    return {"ok": True}


@router.post("/users/{user_id}/disable")
def disable_user(user_id: int, admin=Depends(_admin_only)):
    with get_db() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
    log_action(admin["id"], "USER_DISABLE", f"disabled user_id={user_id}")
    return {"ok": True}


@router.post("/users/{user_id}/enable")
def enable_user(user_id: int, admin=Depends(_admin_only)):
    with get_db() as conn:
        conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user_id,))
    log_action(admin["id"], "USER_ENABLE", f"enabled user_id={user_id}")
    return {"ok": True}


@router.get("/logs")
def logs(limit: int = 100, admin=Depends(_admin_only)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT l.id, l.action, l.detail, l.timestamp, u.username "
            "FROM logs l LEFT JOIN users u ON u.id = l.user_id "
            "ORDER BY l.timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/mt5-status")
async def mt5_status(admin=Depends(_admin_only)):
    # The new connector no longer exposes a bare .server attribute — server
    # name only comes back as part of the account info dict, and fetching
    # that now means a real (awaited) call to MetaApi.
    server = "N/A"
    if connector.connected and not connector.mock_mode:
        try:
            acct = await connector.get_account()
            server = acct.get("server", "N/A")
        except Exception:
            server = "N/A"
    return {
        "connected": connector.connected,
        "mock_mode": connector.mock_mode,
        "server": server,
    }

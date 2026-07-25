"""
Password hashing and session-token authentication.
Uses PBKDF2-HMAC-SHA256 (stdlib only, no extra native deps required).
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

from fastapi import Cookie, HTTPException, status

from db.database import get_db

PBKDF2_ITERATIONS = 260_000
SESSION_TTL_HOURS = 12


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, hash_hex = stored_hash.split("$")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk, expected)


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
    return token


def destroy_session(token: str):
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def get_current_user(session_token: str | None = Cookie(default=None)):
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    with get_db() as conn:
        row = conn.execute(
            "SELECT s.user_id, s.expires_at, u.username, u.role, u.is_active "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (session_token,),
        ).fetchone()

    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")

    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        destroy_session(session_token)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    if not row["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    return {"id": row["user_id"], "username": row["username"], "role": row["role"]}


def require_role(*allowed_roles):
    def dependency(user=None):
        pass

    def checker(user=None, session_token: str | None = Cookie(default=None)):
        current = get_current_user(session_token)
        if current["role"] not in allowed_roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return current

    return checker

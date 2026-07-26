"""
emergency_reactivate.py

TEMPORARY FILE — delete this and its import in main.py right after you've
used it once. Do not leave this in your deployed app.

Re-enables a user account (e.g. an accidentally-disabled admin) via a
single unauthenticated request, protected by a one-time secret you set
yourself. This exists purely to recover from a locked-out state where
nobody can sign in to use the normal /users/{id}/enable endpoint.
"""
import os

from fastapi import APIRouter, HTTPException

from db.database import get_db

router = APIRouter(tags=["emergency"])

# Change this to your own random string before deploying, then use that
# same value in the URL. Treat it like a password — don't reuse anything
# else, and remove this file once you're back in.
EMERGENCY_SECRET = os.getenv("EMERGENCY_SECRET", "devdalton")


@router.get("/emergency-reactivate")
def emergency_reactivate(username: str, secret: str):
    if secret != EMERGENCY_SECRET:
        raise HTTPException(403, "Invalid secret")
    with get_db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise HTTPException(404, f"No user found with username '{username}'")
        conn.execute("UPDATE users SET is_active = 1 WHERE username = ?", (username,))
    return {"ok": True, "reactivated": username}

from fastapi import APIRouter, HTTPException, Response, Cookie, status
from pydantic import BaseModel

from db.database import get_db, log_action
from services.security import verify_password, create_session, destroy_session, get_current_user

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(payload: LoginRequest, response: Response):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role, is_active FROM users WHERE username = ?",
            (payload.username,),
        ).fetchone()

    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    if not row["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been disabled")

    token = create_session(row["id"])
    log_action(row["id"], "LOGIN", f"user '{row['username']}' logged in")

    # NOTE: samesite="none" + secure=True is required for cross-site cookies
    # when the frontend (GitHub Pages) and backend (Render/Railway/etc.) live
    # on different domains. This only works over HTTPS on both sides, which
    # is what those hosts give you by default. If you ever serve the frontend
    # from the same FastAPI app (single domain), switch back to "lax".
    response.set_cookie(
        key="session_token", value=token, httponly=True,
        samesite="none", secure=True, max_age=12 * 3600, path="/",
    )
    return {"username": row["username"], "role": row["role"]}


@router.post("/logout")
def logout(response: Response, session_token: str | None = Cookie(default=None)):
    if session_token:
        destroy_session(session_token)
    response.delete_cookie("session_token", path="/")
    return {"ok": True}


@router.get("/me")
def me(session_token: str | None = Cookie(default=None)):
    user = get_current_user(session_token)
    return user

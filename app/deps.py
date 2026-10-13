"""Shared request helpers: who is calling, and are they allowed."""
from fastapi import Depends, HTTPException, Request, Response

from backend import admin, auth, config

COOKIE = "session"


def client_ip(request: Request) -> str | None:
    # Behind a reverse proxy this is the proxy's address; run uvicorn with --proxy-headers there.
    return request.client.host if request.client else None


def session_token(request: Request) -> str | None:
    return request.cookies.get(COOKIE)


def current_user(request: Request) -> dict:
    user = auth.validate_session(session_token(request))
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


def current_admin(user: dict = Depends(current_user)) -> dict:
    admin.require_admin(user["id"])  # PermissionError -> 403 (see main.py)
    return user


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE, token, max_age=config.SESSION_HOURS * 3600, httponly=True,
        samesite="lax", secure=config.COOKIE_SECURE, path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")

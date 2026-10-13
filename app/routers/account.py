"""Login / signup / password reset, settings, notifications and app metadata."""
import logging

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.deps import (clear_session_cookie, client_ip, current_user, session_token, set_session_cookie)
from backend import auth, billing, config, notifications, usage
from backend.llm import is_demo_ai

router = APIRouter(prefix="/api")
log = logging.getLogger("sqlllm")


class SignupIn(BaseModel):
    username: str = Field(max_length=30)
    email: str = Field(max_length=120)
    password: str = Field(max_length=200)
    consent: bool


class LoginIn(BaseModel):
    username: str = Field(max_length=120)
    password: str = Field(max_length=200)


class ForgotIn(BaseModel):
    email: str = Field(max_length=120)


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class DeleteAccountIn(BaseModel):
    password: str = Field(max_length=200)


class SettingsIn(BaseModel):
    default_chart_type: str
    theme: str
    rows_per_page: int = 50


@router.get("/meta")
def meta():
    return {
        "app_name": config.APP_NAME,
        "demo_ai": is_demo_ai(),
        "demo_payments": billing.is_demo_mode(),
        "currency": config.CURRENCY,
    }


@router.get("/health")
def health():
    from backend.db import get_conn
    with get_conn() as c:
        c.one("SELECT 1 AS ok")
    return {"ok": True}


# ---------------------------------------------------------------- sign in/out
def _login(body_user: str, password: str, request: Request, response: Response) -> dict:
    res = auth.login(body_user, password, ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    set_session_cookie(response, res["token"])
    return res["user"]


@router.post("/auth/signup", status_code=201)
def signup(body: SignupIn, request: Request, response: Response):
    auth.signup(body.username, body.email, body.password, body.consent)
    return {"user": _login(body.username, body.password, request, response)}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    return {"user": _login(body.username, body.password, request, response)}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = session_token(request)
    if token:
        auth.logout(token)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    """One call that gives every page what the shared sidebar needs."""
    return {
        "user": user,
        "usage": usage.get_today_usage(user["id"]),
        "unread_notifications": notifications.unread_count(user["id"]),
        "settings": auth.get_settings(user["id"]),
    }


# ----------------------------------------------------------- password flows
@router.post("/auth/forgot")
def forgot(body: ForgotIn, request: Request):
    token = auth.request_password_reset(body.email)
    result = {"ok": True, "message": "If that email is registered, a reset link has been sent."}
    if token:
        link = f"{str(request.base_url).rstrip('/')}/reset?token={token}"
        if config.RESET_LINK_MODE == "response":  # local development only
            result["dev_reset_link"] = link
        else:
            log.warning("PASSWORD RESET LINK for %s: %s", body.email.strip().lower(), link)
    return result


@router.post("/auth/reset")
def reset(body: ResetIn):
    auth.reset_password(body.token, body.new_password)
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(body: ChangePasswordIn, request: Request, user: dict = Depends(current_user)):
    auth.change_password(user["id"], body.old_password, body.new_password, keep_token=session_token(request))
    return {"ok": True}


@router.post("/auth/delete-account")
def delete_account(body: DeleteAccountIn, response: Response, user: dict = Depends(current_user)):
    result = auth.delete_account(user["id"], body.password)
    clear_session_cookie(response)
    return {"ok": True, "result": result}


# ------------------------------------------------------------------ settings
@router.get("/settings")
def get_settings(user: dict = Depends(current_user)):
    return auth.get_settings(user["id"])


@router.put("/settings")
def put_settings(body: SettingsIn, user: dict = Depends(current_user)):
    auth.update_settings(user["id"], body.default_chart_type, body.theme, body.rows_per_page)
    return auth.get_settings(user["id"])


# ------------------------------------------------------------- notifications
@router.get("/notifications")
def list_notifications(unread_only: bool = False, user: dict = Depends(current_user)):
    return {"items": notifications.list_notifications(user["id"], unread_only),
            "unread": notifications.unread_count(user["id"])}


@router.post("/notifications/read-all")
def read_all(user: dict = Depends(current_user)):
    notifications.mark_all_read(user["id"])
    return {"ok": True}


@router.post("/notifications/{notification_id}/read")
def read_one(notification_id: int, user: dict = Depends(current_user)):
    notifications.mark_read(user["id"], notification_id)
    return {"ok": True}

"""Chat, history, saved queries and usage."""
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.deps import current_user
from backend import history, service, usage
from backend.llm import get_provider

router = APIRouter(prefix="/api")


class AskIn(BaseModel):
    question: str = Field(max_length=2000)  # real limit (500) is enforced by the service with a friendly message
    chat_session_id: int | None = None


class FeedbackIn(BaseModel):
    response_id: int
    rating: int
    reason: str | None = None
    comment: str | None = Field(default=None, max_length=2000)


class SaveIn(BaseModel):
    title: str | None = Field(default=None, max_length=150)


class SavedIn(BaseModel):
    title: str = Field(max_length=150)
    question_text: str = Field(max_length=2000)
    sql_text: str | None = Field(default=None, max_length=10000)


# ---------------------------------------------------------------------- chat
@router.post("/chat/ask")
def ask(body: AskIn, user: dict = Depends(current_user)):
    result = service.ask(user["id"], body.question, get_provider(), body.chat_session_id)
    result["usage"] = usage.get_today_usage(user["id"])
    return result


@router.post("/chat/feedback")
def feedback(body: FeedbackIn, user: dict = Depends(current_user)):
    service.submit_feedback(user["id"], body.response_id, body.rating, body.reason, body.comment)
    return {"ok": True}


@router.get("/chat/export/{response_id}.csv")
def export_csv(response_id: int, user: dict = Depends(current_user)):
    out = history.rerun_response(user["id"], response_id)
    text = service.export_csv(user["id"], out["columns"], out["rows"])
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="result-{response_id}.csv"'})


# ------------------------------------------------------------------- history
@router.get("/history")
def list_history(q: str | None = None, status: str | None = None, session_id: int | None = None,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 user: dict = Depends(current_user)):
    return {"items": history.list_history(user["id"], limit, offset, q, status, session_id)}


@router.get("/history/sessions")
def list_sessions(user: dict = Depends(current_user)):
    return {"items": history.list_sessions(user["id"])}


@router.get("/history/session/{session_id}")
def session_messages(session_id: int, user: dict = Depends(current_user)):
    return {"items": history.get_session_messages(user["id"], session_id)}


@router.post("/history/prompt/{prompt_id}/rerun")
def rerun(prompt_id: int, user: dict = Depends(current_user)):
    return history.rerun(user["id"], prompt_id)


@router.post("/history/prompt/{prompt_id}/save")
def save_from_prompt(prompt_id: int, body: SaveIn, user: dict = Depends(current_user)):
    return {"id": history.save_from_prompt(user["id"], prompt_id, body.title)}


@router.delete("/history/prompt/{prompt_id}")
def delete_prompt(prompt_id: int, user: dict = Depends(current_user)):
    history.delete_prompt(user["id"], prompt_id)
    return {"ok": True}


@router.delete("/history/session/{session_id}")
def delete_session(session_id: int, user: dict = Depends(current_user)):
    history.delete_session(user["id"], session_id)
    return {"ok": True}


@router.delete("/history")
def clear_history(user: dict = Depends(current_user)):
    return {"deleted_chats": history.clear_history(user["id"])}


# --------------------------------------------------------------------- saved
@router.get("/saved")
def list_saved(user: dict = Depends(current_user)):
    return {"items": history.list_saved(user["id"])}


@router.post("/saved", status_code=201)
def create_saved(body: SavedIn, user: dict = Depends(current_user)):
    return {"id": history.save_query(user["id"], body.title, body.question_text, body.sql_text)}


@router.post("/saved/{saved_id}/rerun")
def rerun_saved(saved_id: int, user: dict = Depends(current_user)):
    return history.rerun_saved(user["id"], saved_id)


@router.delete("/saved/{saved_id}")
def delete_saved(saved_id: int, user: dict = Depends(current_user)):
    history.delete_saved(user["id"], saved_id)
    return {"ok": True}


# --------------------------------------------------------------------- usage
@router.get("/usage")
def get_usage(days: int = Query(14, ge=1, le=90), user: dict = Depends(current_user)):
    return {"today": usage.get_today_usage(user["id"]), "daily": usage.get_daily_history(user["id"], days)}

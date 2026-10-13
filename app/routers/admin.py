"""Admin dashboard and Evaluation/Research endpoints. Every endpoint needs an admin session, and the
backend functions re-check the role themselves as a second layer."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.deps import current_admin
from backend import admin, evaluation
from backend.llm import get_provider

router = APIRouter(prefix="/api/admin")


class PlanIn(BaseModel):
    plan: str = Field(max_length=30)


class ActiveIn(BaseModel):
    active: bool


class RoleIn(BaseModel):
    role: str = Field(max_length=10)


class TokensIn(BaseModel):
    tokens: int = Field(gt=0, le=1_000_000_000)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    strategy: str
    template_text: str = Field(min_length=1, max_length=20000)


class ExampleIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    sql_text: str = Field(min_length=1, max_length=10000)
    difficulty: str = "easy"


class TestQuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    expected_sql: str = Field(min_length=1, max_length=10000)
    category: str | None = Field(default=None, max_length=40)
    difficulty: str = "easy"


class RunIn(BaseModel):
    strategy: str
    template_id: int | None = None
    few_shot_k: int = Field(default=3, ge=1, le=10)


# ----------------------------------------------------------------- dashboard
@router.get("/overview")
def overview(a: dict = Depends(current_admin)):
    i = a["id"]
    return {
        "overview": admin.overview(i), "top_users": admin.most_active_users(i),
        "status": admin.status_breakdown(i), "daily": admin.daily_totals(i),
        "feedback_reasons": admin.feedback_reasons(i),
    }


@router.get("/plans")
def plans(a: dict = Depends(current_admin)):
    return {"items": admin.list_plans(a["id"])}


@router.get("/users")
def users(q: str | None = None, a: dict = Depends(current_admin)):
    return {"items": admin.list_users(a["id"], q)}


@router.post("/users/{user_id}/plan")
def set_plan(user_id: int, body: PlanIn, a: dict = Depends(current_admin)):
    admin.set_user_plan(a["id"], user_id, body.plan)
    return {"ok": True}


@router.post("/users/{user_id}/active")
def set_active(user_id: int, body: ActiveIn, a: dict = Depends(current_admin)):
    admin.set_user_active(a["id"], user_id, body.active)
    return {"ok": True}


@router.post("/users/{user_id}/role")
def set_role(user_id: int, body: RoleIn, a: dict = Depends(current_admin)):
    admin.set_user_role(a["id"], user_id, body.role)
    return {"ok": True}


@router.post("/users/{user_id}/grant-tokens")
def grant(user_id: int, body: TokensIn, a: dict = Depends(current_admin)):
    admin.grant_tokens(a["id"], user_id, body.tokens)
    return {"ok": True}


@router.get("/payments")
def payments(a: dict = Depends(current_admin)):
    return {"items": admin.recent_payments(a["id"])}


@router.post("/payments/{payment_id}/refund")
def refund(payment_id: int, a: dict = Depends(current_admin)):
    admin.refund_payment(a["id"], payment_id)
    return {"ok": True}


@router.get("/errors")
def errors(source: str | None = None, a: dict = Depends(current_admin)):
    return {"items": admin.recent_errors(a["id"], source=source)}


@router.get("/blocked")
def blocked(a: dict = Depends(current_admin)):
    return {"items": admin.recent_blocked(a["id"])}


@router.get("/audit")
def audit(a: dict = Depends(current_admin)):
    return {"items": admin.recent_audit_logs(a["id"])}


# ------------------------------------------------- prompts and few-shot data
@router.get("/templates")
def templates(a: dict = Depends(current_admin)):
    return {"items": admin.list_templates(a["id"])}


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, a: dict = Depends(current_admin)):
    return {"id": admin.create_template(a["id"], body.name, body.strategy, body.template_text)}


@router.post("/templates/{template_id}/activate")
def activate(template_id: int, a: dict = Depends(current_admin)):
    admin.activate_template(a["id"], template_id)
    return {"ok": True}


@router.get("/examples")
def examples(a: dict = Depends(current_admin)):
    return {"items": admin.list_few_shot_examples(a["id"])}


@router.post("/examples", status_code=201)
def add_example(body: ExampleIn, a: dict = Depends(current_admin)):
    return {"id": admin.add_few_shot_example(a["id"], body.question, body.sql_text, body.difficulty)}


@router.post("/examples/{example_id}/active")
def example_active(example_id: int, body: ActiveIn, a: dict = Depends(current_admin)):
    admin.set_few_shot_active(a["id"], example_id, body.active)
    return {"ok": True}


# ---------------------------------------------------------------- evaluation
@router.get("/eval/questions")
def eval_questions(a: dict = Depends(current_admin)):
    return {"items": evaluation.list_test_questions(a["id"])}


@router.post("/eval/questions", status_code=201)
def add_eval_question(body: TestQuestionIn, a: dict = Depends(current_admin)):
    return {"id": evaluation.add_test_question(a["id"], body.question, body.expected_sql, body.category, body.difficulty)}


@router.delete("/eval/questions/{test_id}")
def delete_eval_question(test_id: int, a: dict = Depends(current_admin)):
    evaluation.delete_test_question(a["id"], test_id)
    return {"ok": True}


@router.post("/eval/run")
def run_eval(body: RunIn, a: dict = Depends(current_admin)):
    run_id = evaluation.run_experiment(a["id"], get_provider(), body.strategy, body.template_id, body.few_shot_k)
    return {"run_id": run_id}


@router.get("/eval/runs")
def eval_runs(a: dict = Depends(current_admin)):
    return {"items": evaluation.accuracy_table(a["id"])}


@router.get("/eval/runs/{run_id}")
def eval_run(run_id: int, a: dict = Depends(current_admin)):
    i = a["id"]
    return {
        "run": next((r for r in evaluation.accuracy_table(i) if r["id"] == run_id), None),
        "details": evaluation.run_details(i, run_id),
        "failures": evaluation.failure_breakdown(i, run_id),
        "by_difficulty": evaluation.accuracy_by_difficulty(i, run_id),
    }

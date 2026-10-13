from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.deps import current_user
from backend import billing

router = APIRouter(prefix="/api/billing")


class OrderIn(BaseModel):
    plan: str | None = Field(default=None, max_length=30)
    pack: str | None = Field(default=None, max_length=30)


class ConfirmIn(BaseModel):
    gateway_order_id: str = Field(max_length=100)
    gateway_payment_id: str = Field(max_length=100)
    signature: str = Field(max_length=300)


@router.get("/plans")
def plans(user: dict = Depends(current_user)):
    return {"plans": billing.list_plans(), "packs": billing.list_token_packs(),
            "demo": billing.is_demo_mode(), "current_plan": user["plan"]}


@router.post("/order")
def order(body: OrderIn, user: dict = Depends(current_user)):
    return billing.create_order(user["id"], plan_name=body.plan, pack=body.pack)


@router.post("/confirm")
def confirm(body: ConfirmIn, user: dict = Depends(current_user)):
    return billing.confirm_payment(user["id"], body.gateway_order_id, body.gateway_payment_id, body.signature)


@router.get("/payments")
def payments(user: dict = Depends(current_user)):
    return {"items": billing.list_payments(user["id"])}


@router.get("/invoice/{payment_id}")
def invoice(payment_id: int, user: dict = Depends(current_user)):
    inv = billing.get_invoice(user["id"], payment_id)
    if not inv:
        from fastapi import HTTPException
        raise HTTPException(404, "Invoice not found.")
    return inv

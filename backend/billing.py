"""Pricing, checkout, payment history and invoices.

Gateway modes
- Demo (no RAZORPAY_KEY_SECRET set): orders are created locally and confirmed with signature "demo".
  Good for the project demo; refused automatically once real keys are configured.
- Razorpay: order creation calls the Razorpay Orders API, and confirm_payment verifies the
  checkout signature (HMAC-SHA256 of "order_id|payment_id" with your key secret).
  NOTE: the Razorpay HTTP call could not be exercised against the real service here. Test it with
  your Razorpay *test* keys before relying on it. Card details never touch this app.

Limitation: the schema has no plan expiry column, so a purchased plan stays active until an admin
(or a future renewal job) changes it.
"""
import base64
import hashlib
import hmac
import json
import urllib.request
import uuid
from decimal import ROUND_HALF_UP, Decimal

from . import config, notifications, usage
from .db import get_conn, to_json

ADMIN_PLAN = "admin"


def is_demo_mode() -> bool:
    return not config.RAZORPAY_KEY_SECRET


def list_plans() -> list[dict]:
    """For the Pricing page. The internal 'admin' plan is never shown."""
    with get_conn() as conn:
        return conn.all(
            """SELECT id, name, price_monthly, daily_query_limit, daily_token_limit, can_export_csv, can_use_charts
               FROM plans WHERE is_active=1 AND name <> %s ORDER BY price_monthly, id""",
            (ADMIN_PLAN,),
        )


def list_token_packs() -> list[dict]:
    return [{"key": k, "tokens": t, "price": p} for k, (t, p) in config.TOKEN_PACKS.items()]


def _money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _razorpay_create_order(amount: Decimal, receipt: str) -> str:
    body = json.dumps({"amount": int(amount * 100), "currency": config.CURRENCY, "receipt": receipt}).encode()
    auth = base64.b64encode(f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}".encode()).decode()
    req = urllib.request.Request(
        "https://api.razorpay.com/v1/orders", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["id"]


def create_order(user_id: int, plan_name: str | None = None, pack: str | None = None) -> dict:
    """Start a checkout for a plan XOR a token pack. Returns what the checkout page needs."""
    if bool(plan_name) == bool(pack):
        raise ValueError("Choose either a plan or a token pack.")
    plan_id, tokens = None, 0
    with get_conn() as conn:
        if plan_name:
            plan = conn.one("SELECT id, price_monthly FROM plans WHERE name=%s AND is_active=1 AND name<>%s",
                            (plan_name, ADMIN_PLAN))
            if not plan:
                raise ValueError("Unknown plan.")
            amount, plan_id = _money(plan["price_monthly"]), plan["id"]
            if amount <= 0:
                raise ValueError("This plan is free. Nothing to pay.")
        else:
            if pack not in config.TOKEN_PACKS:
                raise ValueError("Unknown token pack.")
            tokens, price = config.TOKEN_PACKS[pack]
            amount = _money(price)

        receipt = "rcpt_" + uuid.uuid4().hex[:20]
        gateway = "demo" if is_demo_mode() else "razorpay"
        order_id = ("demo_order_" + uuid.uuid4().hex[:16]) if gateway == "demo" else None
    if gateway == "razorpay":
        order_id = _razorpay_create_order(amount, receipt)  # network call outside the DB transaction
    with get_conn() as conn:
        payment_id = conn.insert(
            """INSERT INTO payments (user_id, plan_id, tokens_purchased, amount, currency, gateway,
                                     gateway_order_id, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'created')""",
            (user_id, plan_id, tokens, amount, config.CURRENCY, gateway, order_id),
        )
    return {
        "payment_id": payment_id, "gateway": gateway, "gateway_order_id": order_id,
        "amount": amount, "currency": config.CURRENCY, "razorpay_key_id": config.RAZORPAY_KEY_ID or None,
    }


def _signature_ok(order_id: str, payment_ref: str, signature: str) -> bool:
    if is_demo_mode():
        return signature == "demo" and order_id.startswith("demo_order_")
    expected = hmac.new(config.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{payment_ref}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def confirm_payment(user_id: int, gateway_order_id: str, gateway_payment_id: str, signature: str) -> dict:
    """Verify the checkout result and grant the plan / tokens. Safe to call twice (idempotent)."""
    outcome = None
    with get_conn() as conn:
        pay = conn.one("SELECT * FROM payments WHERE gateway_order_id=%s AND user_id=%s FOR UPDATE",
                       (gateway_order_id, user_id))
        if not pay:
            outcome = "missing"
        elif pay["status"] == "success":
            outcome = "already"
        elif not _signature_ok(gateway_order_id, gateway_payment_id, signature):
            conn.execute("UPDATE payments SET status='failed' WHERE id=%s", (pay["id"],))
            notifications.notify(conn, user_id, "payment_failed", "Your payment could not be verified.")
            outcome = "bad_signature"
        else:
            conn.execute(
                "UPDATE payments SET status='success', gateway_payment_id=%s, paid_at=NOW() WHERE id=%s",
                (gateway_payment_id, pay["id"]),
            )
            total = _money(pay["amount"])
            subtotal = (total / (1 + Decimal(str(config.TAX_RATE)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            conn.insert(
                """INSERT INTO invoices (payment_id, invoice_number, subtotal, tax_amount, total)
                   VALUES (%s, %s, %s, %s, %s)""",
                (pay["id"], f"INV-{pay['id']:08d}", subtotal, total - subtotal, total),
            )
            if pay["plan_id"]:
                conn.execute("UPDATE users SET plan_id=%s WHERE id=%s", (pay["plan_id"], user_id))
                name = conn.one("SELECT name FROM plans WHERE id=%s", (pay["plan_id"],))["name"]
                notifications.notify(conn, user_id, "plan_changed", f"You are now on the {name} plan.")
            if pay["tokens_purchased"]:
                usage.add_token_balance(conn, user_id, int(pay["tokens_purchased"]))
            notifications.notify(conn, user_id, "payment_success",
                                 f"Payment of {config.CURRENCY} {total} received. Thank you!")
            outcome = "success"
    if outcome == "missing":
        raise ValueError("Order not found.")
    if outcome == "bad_signature":
        raise ValueError("Payment verification failed.")
    return {"status": "success", "already_processed": outcome == "already"}


def list_payments(user_id: int, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        return conn.all(
            """SELECT p.id, p.created_at, p.paid_at, p.amount, p.currency, p.status, p.tokens_purchased,
                      pl.name AS plan, i.invoice_number
               FROM payments p LEFT JOIN plans pl ON pl.id=p.plan_id LEFT JOIN invoices i ON i.payment_id=p.id
               WHERE p.user_id=%s ORDER BY p.id DESC LIMIT %s""",
            (user_id, limit),
        )


def get_invoice(user_id: int, payment_id: int) -> dict | None:
    with get_conn() as conn:
        return conn.one(
            """SELECT i.invoice_number, i.issued_at, i.subtotal, i.tax_amount, i.total, p.currency,
                      p.tokens_purchased, pl.name AS plan, u.username, u.email
               FROM invoices i JOIN payments p ON p.id=i.payment_id JOIN users u ON u.id=p.user_id
               LEFT JOIN plans pl ON pl.id=p.plan_id
               WHERE p.id=%s AND p.user_id=%s""",
            (payment_id, user_id),
        )

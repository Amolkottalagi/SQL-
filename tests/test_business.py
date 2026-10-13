from decimal import Decimal

import pytest

from backend import admin, auth, billing, config, evaluation, notifications, service, usage
from backend.db import get_conn
from backend.llm import FakeProvider, LLMError
from tests.conftest import sql_reply


def one(sql, args=()):
    with get_conn() as c:
        return c.one(sql, args)


# --------------------------------------------------------------- billing
def test_pricing_lists_hide_internal_admin_plan():
    names = [p["name"] for p in billing.list_plans()]
    assert names == ["free", "pro"]
    assert {p["key"] for p in billing.list_token_packs()} == set(config.TOKEN_PACKS)


def test_buy_plan_demo_flow(alice):
    assert billing.is_demo_mode()
    o = billing.create_order(alice, plan_name="pro")
    assert o["gateway"] == "demo" and o["amount"] == Decimal("199.00")
    assert one("SELECT status FROM payments WHERE id=%s", (o["payment_id"],))["status"] == "created"

    res = billing.confirm_payment(alice, o["gateway_order_id"], "pay_1", "demo")
    assert res == {"status": "success", "already_processed": False}
    assert auth.get_user(alice)["plan"] == "pro"
    assert usage.get_today_usage(alice)["queries_limit"] == 500

    inv = billing.get_invoice(alice, o["payment_id"])
    assert inv["total"] == Decimal("199.00") and inv["subtotal"] + inv["tax_amount"] == inv["total"]
    assert inv["tax_amount"] == Decimal("30.36") and inv["invoice_number"].startswith("INV-")
    assert {n["type"] for n in notifications.list_notifications(alice)} == {"payment_success", "plan_changed"}
    assert billing.list_payments(alice)[0]["status"] == "success"


def test_confirm_is_idempotent(alice):
    o = billing.create_order(alice, pack="small")
    billing.confirm_payment(alice, o["gateway_order_id"], "pay_1", "demo")
    again = billing.confirm_payment(alice, o["gateway_order_id"], "pay_1", "demo")
    assert again["already_processed"] and usage.get_today_usage(alice)["token_balance"] == 100_000
    assert one("SELECT COUNT(*) AS n FROM invoices")["n"] == 1


def test_bad_signature_grants_nothing(alice):
    o = billing.create_order(alice, plan_name="pro")
    with pytest.raises(ValueError, match="verification"):
        billing.confirm_payment(alice, o["gateway_order_id"], "pay_1", "forged")
    assert auth.get_user(alice)["plan"] == "free"
    assert one("SELECT status FROM payments")["status"] == "failed"
    assert [n["type"] for n in notifications.list_notifications(alice)] == ["payment_failed"]


def test_cannot_confirm_someone_elses_order(alice):
    bob = auth.signup("bob", "bob@example.com", "password123", True)
    o = billing.create_order(alice, pack="small")
    with pytest.raises(ValueError, match="not found"):
        billing.confirm_payment(bob, o["gateway_order_id"], "pay_x", "demo")
    assert usage.get_today_usage(alice)["token_balance"] == 0


def test_demo_gateway_is_refused_once_real_keys_are_configured(alice, monkeypatch):
    o = billing.create_order(alice, plan_name="pro")
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", "real_secret")
    with pytest.raises(ValueError):
        billing.confirm_payment(alice, o["gateway_order_id"], "pay_1", "demo")
    assert auth.get_user(alice)["plan"] == "free"


def test_razorpay_signature_verification(alice, monkeypatch):
    import hashlib, hmac
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", "s3cret")
    with get_conn() as c:
        pid = c.insert("INSERT INTO payments (user_id, plan_id, amount, gateway, gateway_order_id, status) "
                       "SELECT %s, id, 199, 'razorpay', 'order_ABC', 'created' FROM plans WHERE name='pro'", (alice,))
    sig = hmac.new(b"s3cret", b"order_ABC|pay_XYZ", hashlib.sha256).hexdigest()
    assert billing.confirm_payment(alice, "order_ABC", "pay_XYZ", sig)["status"] == "success"
    assert auth.get_user(alice)["plan"] == "pro"


def test_order_validation(alice):
    for kwargs in [{}, {"plan_name": "pro", "pack": "small"}, {"plan_name": "free"}, {"plan_name": "admin"},
                   {"plan_name": "nope"}, {"pack": "huge"}]:
        with pytest.raises(ValueError):
            billing.create_order(alice, **kwargs)


def test_user_with_payments_is_anonymised_not_deleted(alice):
    o = billing.create_order(alice, plan_name="pro")
    billing.confirm_payment(alice, o["gateway_order_id"], "pay_1", "demo")
    assert auth.delete_account(alice, "password123") == "anonymised"
    row = one("SELECT username, email, is_active FROM users WHERE id=%s", (alice,))
    assert row["username"].startswith("deleted_") and row["is_active"] == 0 and "alice" not in row["email"]
    assert one("SELECT COUNT(*) AS n FROM payments")["n"] == 1 and one("SELECT COUNT(*) AS n FROM invoices")["n"] == 1
    with pytest.raises(auth.AuthError):
        auth.login("alice", "password123")


def test_refund_reverts_grants(alice, admin_id):
    o = billing.create_order(alice, pack="small")
    billing.confirm_payment(alice, o["gateway_order_id"], "pay_1", "demo")
    o2 = billing.create_order(alice, plan_name="pro")
    billing.confirm_payment(alice, o2["gateway_order_id"], "pay_2", "demo")
    admin.refund_payment(admin_id, o["payment_id"])
    admin.refund_payment(admin_id, o2["payment_id"])
    assert usage.get_today_usage(alice)["token_balance"] == 0 and auth.get_user(alice)["plan"] == "free"
    with pytest.raises(ValueError):
        admin.refund_payment(admin_id, o["payment_id"])  # already refunded


# ----------------------------------------------------------------- admin
def test_admin_functions_require_admin(alice, admin_id):
    calls = [
        lambda: admin.overview(alice), lambda: admin.list_users(alice), lambda: admin.set_user_plan(alice, alice, "pro"),
        lambda: admin.recent_errors(alice), lambda: admin.recent_audit_logs(alice), lambda: admin.grant_tokens(alice, alice, 5),
        lambda: admin.create_template(alice, "x", "zero_shot", "{schema}{question}"),
        lambda: evaluation.run_experiment(alice, FakeProvider(), "zero_shot"),
        lambda: evaluation.accuracy_table(alice),
    ]
    for call in calls:
        with pytest.raises(PermissionError):
            call()
    with get_conn() as c:  # a deactivated admin loses access immediately
        c.execute("UPDATE users SET is_active=0 WHERE id=%s", (admin_id,))
    with pytest.raises(PermissionError):
        admin.overview(admin_id)


def test_dashboard_numbers(alice, admin_id):
    bob = auth.signup("bob", "bob@example.com", "password123", True)
    ok = FakeProvider(default=sql_reply("SELECT 1"))
    service.ask(alice, "a", ok)
    service.ask(alice, "b", ok)
    service.ask(bob, "c", FakeProvider(default=sql_reply("DROP TABLE orders")))
    service.ask(bob, "d", FakeProvider(default=sql_reply("SELECT 1")))
    r = service.ask(alice, "e", ok)
    service.submit_feedback(alice, r["response_id"], -1, "slow")

    ov = admin.overview(admin_id)
    assert ov["total_users"] == 3 and ov["total_questions"] == 5
    assert ov["error_rate"] == pytest.approx(1 / 5) and ov["thumbs_down"] == 1
    assert ov["total_tokens"] == ov["input_tokens"] + ov["output_tokens"] > 0
    assert ov["queries_today"] == 5
    top = admin.most_active_users(admin_id)
    assert top[0]["username"] == "alice" and top[0]["questions"] == 3
    assert {s["status"]: s["n"] for s in admin.status_breakdown(admin_id)} == {"success": 4, "blocked": 1}
    assert admin.feedback_reasons(admin_id) == [{"reason": "slow", "n": 1}]
    assert admin.daily_totals(admin_id)[-1]["active_users"] == 2
    assert admin.recent_blocked(admin_id)[0]["username"] == "bob"
    assert [u["username"] for u in admin.list_users(admin_id, search="ali")] == ["alice"]


def test_admin_actions_are_audited_and_effective(alice, admin_id):
    admin.set_user_plan(admin_id, alice, "pro")
    assert auth.get_user(alice)["plan"] == "pro"
    admin.set_user_role(admin_id, alice, "admin")
    admin.set_user_role(admin_id, alice, "user")
    session = auth.login("alice", "password123")["token"]
    admin.set_user_active(admin_id, alice, False)
    assert auth.validate_session(session) is None  # sessions revoked on deactivation
    with pytest.raises(ValueError):
        admin.set_user_active(admin_id, admin_id, False)
    with pytest.raises(ValueError):
        admin.set_user_role(admin_id, admin_id, "user")
    with pytest.raises(ValueError):
        admin.set_user_plan(admin_id, alice, "platinum")
    with pytest.raises(ValueError):
        admin.set_user_plan(admin_id, 99999, "pro")
    actions = [a["action"] for a in admin.recent_audit_logs(admin_id)]
    assert {"change_plan", "change_role", "deactivate_user"} <= set(actions)
    assert notifications.list_notifications(alice)[-1]["type"] == "plan_changed"


def test_template_management(admin_id):
    with pytest.raises(ValueError):
        admin.create_template(admin_id, "bad", "few_shot", "no placeholders")
    t1 = admin.create_template(admin_id, "v", "zero_shot", "{schema}\nQuestion: {question}")
    t2 = admin.create_template(admin_id, "v", "few_shot", "{schema}{examples}\nQuestion: {question}")
    assert [t["version"] for t in admin.list_templates(admin_id) if t["name"] == "v"] == [2, 1]
    admin.activate_template(admin_id, t1)
    admin.activate_template(admin_id, t2)
    assert [t["id"] for t in admin.list_templates(admin_id) if t["is_active"]] == [t2]


def test_few_shot_example_management(admin_id):
    with pytest.raises(Exception):
        admin.add_few_shot_example(admin_id, "bad", "DROP TABLE x")
    eid = admin.add_few_shot_example(admin_id, "Count products", "SELECT COUNT(*) FROM products", "easy")
    admin.set_few_shot_active(admin_id, eid, False)
    assert [e for e in admin.list_few_shot_examples(admin_id) if e["id"] == eid][0]["is_active"] == 0


# ------------------------------------------------------------ evaluation
SCRIPTED = {
    "how many orders": sql_reply("SELECT COUNT(*) FROM orders"),                 # correct
    "list all cities": sql_reply("SELECT city FROM customers"),                  # duplicates -> wrong_result
    "most expensive": sql_reply("SELECT nme FROM products"),                     # sql_error
    "total revenue": sql_reply("DROP TABLE orders"),                             # blocked
    "number of customers in each city": sql_reply("SELECT city, COUNT(*) FROM customers GROUP BY city"),  # correct
    "bought a laptop": sql_reply(                                                # correct (order of rows irrelevant)
        "SELECT DISTINCT c.name FROM orders o JOIN customers c ON c.id=o.customer_id "
        "JOIN products p ON p.id=o.product_id WHERE p.name='Laptop' ORDER BY c.name DESC"),
}


def test_experiment_scoring_and_breakdowns(admin_id):
    p = FakeProvider(SCRIPTED)
    run = evaluation.run_experiment(admin_id, p, "zero_shot")
    row = evaluation.accuracy_table(admin_id)[0]
    assert (row["total"], row["correct"], float(row["accuracy_pct"])) == (6, 3, 50.0)
    assert {f["failure_type"]: f["n"] for f in evaluation.failure_breakdown(admin_id, run)} == \
        {"wrong_result": 1, "sql_error": 1, "blocked": 1}
    assert {d["difficulty"]: float(d["accuracy_pct"]) for d in evaluation.accuracy_by_difficulty(admin_id, run)} == \
        {"easy": 33.3, "medium": 50.0, "hard": 100.0}
    details = evaluation.run_details(admin_id, run)
    assert len(details) == 6 and sum(d["is_correct"] for d in details) == 3
    assert all("Examples:" not in pr for pr in p.prompts)


def test_few_shot_prompts_use_examples_but_never_the_test_question(admin_id):
    p = FakeProvider(SCRIPTED)
    evaluation.run_experiment(admin_id, p, "few_shot", few_shot_k=2)
    tests = [t["question_text"] for t in evaluation.list_test_questions(admin_id)]
    for pr in p.prompts:
        assert "Examples:" in pr
        shown = pr.split("Examples:")[1].rsplit("Question:", 1)[0]
        assert not any(t in shown for t in tests)  # no leakage
    assert [r["strategy"] for r in evaluation.accuracy_table(admin_id)] == ["few_shot"]


def test_experiment_handles_llm_errors_and_ordering(admin_id):
    p = FakeProvider()
    p.fail_with = LLMError("boom")
    run = evaluation.run_experiment(admin_id, p, "zero_shot")
    assert [f["failure_type"] for f in evaluation.failure_breakdown(admin_id, run)] == ["llm_error"]
    assert evaluation.accuracy_table(admin_id)[0]["correct"] == 0


def test_result_comparison_rules():
    mk = lambda cols, rows: {"columns": cols, "rows": rows}
    exp = mk(["a"], [[1], [2]])
    assert evaluation.same_result(mk(["x"], [[2], [1]]), exp, "SELECT a FROM t")                # order ignored
    assert not evaluation.same_result(mk(["x"], [[2], [1]]), exp, "SELECT a FROM t ORDER BY a")  # unless ORDER BY
    assert evaluation.same_result(mk(["x"], [[1.00001], [2]]), exp, "SELECT a")                 # float tolerance
    assert not evaluation.same_result(mk(["x", "y"], [[1, 1], [2, 2]]), exp, "SELECT a")         # column count
    assert not evaluation.same_result(mk(["x"], [[1]]), exp, "SELECT a")                         # row count


def test_test_question_management(admin_id):
    with pytest.raises(Exception):
        evaluation.add_test_question(admin_id, "bad", "SELECT * FROM missing_table")  # gold SQL must run
    with pytest.raises(Exception):
        evaluation.add_test_question(admin_id, "evil", "DROP TABLE orders")
    tid = evaluation.add_test_question(admin_id, "How many products?", "SELECT COUNT(*) FROM products")
    evaluation.delete_test_question(admin_id, tid)
    evaluation.run_experiment(admin_id, FakeProvider(SCRIPTED), "zero_shot")
    first = evaluation.list_test_questions(admin_id)[0]["id"]
    with pytest.raises(ValueError):
        evaluation.delete_test_question(admin_id, first)  # referenced by results
    with get_conn() as c:
        c.execute("DELETE FROM experiment_results")
        c.execute("DELETE FROM experiment_runs")
        c.execute("DELETE FROM test_questions")
    with pytest.raises(ValueError):
        evaluation.run_experiment(admin_id, FakeProvider(), "zero_shot")
    with pytest.raises(ValueError):
        evaluation.run_experiment(admin_id, FakeProvider(), "chain")

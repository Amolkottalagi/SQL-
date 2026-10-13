"""Seed required reference data (idempotent, safe to re-run).

    python -m backend.seed                               # plans, prompt template, examples, test questions
    python -m backend.seed --admin-user admin --admin-email you@example.com --admin-password '...'

Run the .sql files in sql/ first (see README).
"""
import argparse

from . import auth
from .db import get_conn
from .llm import DEFAULT_TEMPLATE

# name, daily_query_limit, daily_token_limit, price_monthly, can_export_csv, can_use_charts
PLANS = [
    ("free", 20, 20_000, 0, False, True),     # must be inserted first: users.plan_id defaults to 1
    ("pro", 500, 500_000, 199, True, True),
    ("admin", None, None, 0, True, True),     # unlimited
]

FEW_SHOT = [
    ("How many customers do we have?", "SELECT COUNT(*) AS customer_count FROM customers", "easy"),
    ("List the names of all products in the Furniture category",
     "SELECT name FROM products WHERE category = 'Furniture'", "easy"),
    ("Total quantity ordered per product name, highest first",
     "SELECT p.name, SUM(o.quantity) AS total_qty FROM orders o JOIN products p ON p.id = o.product_id "
     "GROUP BY p.name ORDER BY total_qty DESC", "medium"),
    ("Which customers placed more than one order?",
     "SELECT c.name, COUNT(*) AS orders FROM orders o JOIN customers c ON c.id = o.customer_id "
     "GROUP BY c.id, c.name HAVING COUNT(*) > 1", "medium"),
]

# Kept separate from FEW_SHOT on purpose (no leakage into the few-shot experiment).
TEST_QUESTIONS = [
    ("How many orders are there?", "SELECT COUNT(*) FROM orders", "count", "easy"),
    ("List all cities where customers live", "SELECT DISTINCT city FROM customers", "select", "easy"),
    ("What is the most expensive product?",
     "SELECT name FROM products ORDER BY price DESC LIMIT 1", "order", "easy"),
    ("Total revenue from all orders",
     "SELECT SUM(o.quantity * p.price) FROM orders o JOIN products p ON p.id = o.product_id", "aggregate", "medium"),
    ("Number of customers in each city",
     "SELECT city, COUNT(*) FROM customers GROUP BY city", "group", "medium"),
    ("Names of customers who bought a Laptop",
     "SELECT DISTINCT c.name FROM orders o JOIN customers c ON c.id=o.customer_id "
     "JOIN products p ON p.id=o.product_id WHERE p.name='Laptop'", "join", "hard"),
]


def seed_reference_data() -> None:
    with get_conn() as conn:
        for name, ql, tl, price, exp, charts in PLANS:
            conn.execute(
                """INSERT INTO plans (name, daily_query_limit, daily_token_limit, price_monthly,
                                      can_export_csv, can_use_charts)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE daily_query_limit=VALUES(daily_query_limit),
                       daily_token_limit=VALUES(daily_token_limit), price_monthly=VALUES(price_monthly),
                       can_export_csv=VALUES(can_export_csv), can_use_charts=VALUES(can_use_charts)""",
                (name, ql, tl, price, int(exp), int(charts)),
            )
        if not conn.one("SELECT 1 FROM prompt_templates LIMIT 1"):
            conn.execute(
                "INSERT INTO prompt_templates (name, version, strategy, template_text, is_active) VALUES ('default',1,'few_shot',%s,1)",
                (DEFAULT_TEMPLATE,),
            )
        if not conn.one("SELECT 1 FROM few_shot_examples LIMIT 1"):
            for q, s, d in FEW_SHOT:
                conn.execute("INSERT INTO few_shot_examples (question_text, sql_text, difficulty) VALUES (%s,%s,%s)", (q, s, d))
        if not conn.one("SELECT 1 FROM test_questions LIMIT 1"):
            for q, s, c, d in TEST_QUESTIONS:
                conn.execute("INSERT INTO test_questions (question_text, expected_sql, category, difficulty) VALUES (%s,%s,%s,%s)", (q, s, c, d))


def create_admin(username: str, email: str, password: str) -> int:
    uid = auth.signup(username, email, password, consent=True)
    with get_conn() as conn:
        conn.execute("UPDATE users SET role='admin' WHERE id=%s", (uid,))
        admin_plan = conn.one("SELECT id FROM plans WHERE name='admin'")
        conn.execute("UPDATE users SET plan_id=%s WHERE id=%s", (admin_plan["id"], uid))
    return uid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-user")
    ap.add_argument("--admin-email")
    ap.add_argument("--admin-password")
    a = ap.parse_args()
    seed_reference_data()
    print("Seeded plans, default prompt template, few-shot examples and test questions.")
    if a.admin_user:
        if not (a.admin_email and a.admin_password):
            ap.error("--admin-email and --admin-password are required with --admin-user")
        print("Admin user created, id =", create_admin(a.admin_user, a.admin_email, a.admin_password))


if __name__ == "__main__":
    main()

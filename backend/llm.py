"""LLM access and prompt building. Everything else depends only on the LLMProvider interface,
so tests use FakeProvider and switching vendor means editing this file only."""
import re
from dataclasses import dataclass
from typing import Protocol

from . import config
from .sql_guard import strip_code_fences


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    model: str


class LLMProvider(Protocol):
    model: str

    def generate(self, prompt: str) -> LLMResult: ...


class LLMError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code  # e.g. "429" for rate limit, stored in error_logs


# ---------------------------------------------------------------- providers
class GeminiProvider:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from google import genai  # lazy import: tests and non-LLM pages don't need the package
        from google.genai import types

        key = api_key or config.GEMINI_API_KEY
        if not key:
            raise LLMError("GEMINI_API_KEY is not set.", "config")
        self._client = genai.Client(api_key=key)
        self._cfg = types.GenerateContentConfig(temperature=0)  # deterministic SQL
        self.model = model or config.GEMINI_MODEL

    def generate(self, prompt: str) -> LLMResult:
        try:
            resp = self._client.models.generate_content(model=self.model, contents=prompt, config=self._cfg)
        except Exception as e:
            raise LLMError(str(e), str(getattr(e, "code", "") or "") or None)
        u = getattr(resp, "usage_metadata", None)
        inp = getattr(u, "prompt_token_count", 0) or 0
        # "thinking" tokens are billed as output but reported separately by Gemini 2.5.
        out = (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        total = getattr(u, "total_token_count", 0) or (inp + out)
        return LLMResult(resp.text or "", inp, out, total, self.model)


class FakeProvider:
    """Deterministic provider for tests/demos.

    `responses` maps a substring of the prompt to a reply, or to a list of replies used in order
    (handy for testing the self-correction retry). Replies use the normal "SQL: ... EXPLANATION: ..." format.
    """

    model = "fake"

    def __init__(self, responses: dict | None = None, default: str = "SELECT 1"):
        self.responses = {k: (list(v) if isinstance(v, list) else v) for k, v in (responses or {}).items()}
        self.default = default
        self.prompts: list[str] = []
        self.fail_with: LLMError | None = None

    @property
    def last_prompt(self) -> str:
        return self.prompts[-1] if self.prompts else ""

    def generate(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        if self.fail_with:
            raise self.fail_with
        # Only look at the question line so schema/example text can't trigger a match.
        m = re.findall(r"^Question: (.*)$", prompt, re.MULTILINE)
        q = (m[-1] if m else prompt).lower()
        text = self.default
        for key, val in self.responses.items():
            if key.lower() in q:
                if isinstance(val, list):
                    text = val.pop(0) if len(val) > 1 else val[0]  # last reply repeats
                else:
                    text = val
                break
        i, o = max(len(prompt) // 4, 1), max(len(text) // 4, 1)
        return LLMResult(text, i, o, i + o, self.model)


# ------------------------------------------------------------------ prompts
DEFAULT_TEMPLATE = """You are an expert MySQL data analyst. Convert the user's question into ONE MySQL SELECT query.

Rules:
- Use only the tables and columns listed in the schema.
- Write a single read-only SELECT (or WITH ... SELECT). Never modify data.
- Prefer explicit column names over SELECT *. Add LIMIT 100 unless the user asks for everything.
- If the question is ambiguous or cannot be answered from the schema, reply with exactly one line:
  CLARIFY: <short question for the user>

Reply in exactly this format (no markdown):
SQL: <the query>
EXPLANATION: <one short sentence describing what the query does>

Schema:
{schema}
{examples}
Question: {question}"""


def render_template(template: str, schema: str, question: str, examples: list[tuple[str, str]] | None) -> str:
    ex = ""
    if examples:
        ex = "\nExamples:\n" + "\n".join(f"Question: {q}\nSQL: {s}\n" for q, s in examples)
    # str.replace (not .format) because templates may contain literal braces.
    return template.replace("{schema}", schema).replace("{examples}", ex).replace("{question}", question)


def build_retry_prompt(prompt: str, previous_sql: str, error: str) -> str:
    return (
        f"{prompt}\n\nPrevious attempt (it failed):\nSQL: {previous_sql}\nMySQL error: {error}\n"
        "Fix the query. Reply in the same format (SQL: ... EXPLANATION: ...)."
    )


@dataclass
class Parsed:
    sql: str | None
    explanation: str | None
    clarification: str | None


def parse_response(text: str) -> Parsed:
    text = strip_code_fences(text or "")
    if not text:
        return Parsed(None, None, None)
    if re.match(r"^\s*CLARIFY\s*:", text, re.IGNORECASE):
        return Parsed(None, None, re.sub(r"^\s*CLARIFY\s*:\s*", "", text, flags=re.IGNORECASE).strip())
    explanation = None
    m = re.search(r"^\s*EXPLANATION\s*:\s*(.*)\Z", text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if m:
        explanation = m.group(1).strip() or None
        text = text[: m.start()]
    text = re.sub(r"^\s*SQL\s*:\s*", "", text.strip(), flags=re.IGNORECASE)
    return Parsed(strip_code_fences(text).strip() or None, explanation, None)


# ------------------------------------------------------------- demo provider
class DemoProvider:
    """Offline, rule-based stand-in for the LLM so the app works without an API key.

    It understands a fixed set of questions about the bundled demo database (customers, products,
    orders). It is NOT an AI: swap in GeminiProvider for real natural-language understanding.
    """

    model = "demo-rules"
    HELP = ("Demo mode (no Gemini API key) understands questions about customers, products and orders, for example: "
            "'How many customers do we have?', 'Total revenue per product', 'Which customers live in Pune?', "
            "'Most expensive product', 'Orders per month'.")

    CITIES = ("pune", "mumbai", "delhi")
    CATEGORIES = ("electronics", "furniture", "stationery")

    def generate(self, prompt: str) -> LLMResult:
        m = re.findall(r"^Question: (.*)$", prompt, re.MULTILINE)
        q = (m[-1] if m else prompt).lower().strip().rstrip("?.! ")
        sql, why = self._rules(q)
        text = f"CLARIFY: {self.HELP}" if sql is None else f"SQL: {sql}\nEXPLANATION: {why}"
        i, o = max(len(prompt) // 4, 1), max(len(text) // 4, 1)
        return LLMResult(text, i, o, i + o, self.model)

    def _rules(self, q: str):
        has = lambda *w: any(x in q for x in w)
        city = next((c for c in self.CITIES if c in q), None)
        cat = next((c for c in self.CATEGORIES if c in q), None)

        if has("per month", "by month", "each month", "monthly"):
            return ("SELECT DATE_FORMAT(order_date, '%Y-%m') AS month, COUNT(*) AS orders FROM orders "
                    "GROUP BY month ORDER BY month", "Counts orders for each month.")
        if has("revenue", "sales") and has("per product", "by product", "each product"):
            return ("SELECT p.name, SUM(o.quantity * p.price) AS revenue FROM orders o JOIN products p ON p.id = o.product_id "
                    "GROUP BY p.name ORDER BY revenue DESC", "Adds up quantity x price for every product.")
        if has("revenue", "sales", "spent", "spend") and has("per customer", "by customer", "each customer", "top customers"):
            return ("SELECT c.name, SUM(o.quantity * p.price) AS total_spent FROM orders o JOIN customers c ON c.id = o.customer_id "
                    "JOIN products p ON p.id = o.product_id GROUP BY c.name ORDER BY total_spent DESC",
                    "Total spent by each customer, highest first.")
        if has("total revenue", "total sales", "overall revenue", "how much revenue", "revenue"):
            return ("SELECT SUM(o.quantity * p.price) AS total_revenue FROM orders o JOIN products p ON p.id = o.product_id",
                    "Adds up quantity x price over all orders.")
        if has("best selling", "top selling", "most sold", "most popular", "bestseller"):
            return ("SELECT p.name, SUM(o.quantity) AS units_sold FROM orders o JOIN products p ON p.id = o.product_id "
                    "GROUP BY p.name ORDER BY units_sold DESC LIMIT 5", "Top products by units sold.")
        if has("orders per customer", "orders by customer", "orders for each customer", "orders did each customer"):
            return ("SELECT c.name, COUNT(*) AS orders FROM orders o JOIN customers c ON c.id = o.customer_id "
                    "GROUP BY c.name ORDER BY orders DESC", "Number of orders per customer.")
        if has("most expensive", "costliest", "highest price", "priciest"):
            return ("SELECT name, price FROM products ORDER BY price DESC LIMIT 1", "The product with the highest price.")
        if has("cheapest", "lowest price", "least expensive"):
            return ("SELECT name, price FROM products ORDER BY price ASC LIMIT 1", "The product with the lowest price.")
        if has("customer") and has("each city", "per city", "by city", "every city"):
            return ("SELECT city, COUNT(*) AS customers FROM customers GROUP BY city ORDER BY customers DESC",
                    "Customers counted for each city.")
        if has("customer") and city:
            return (f"SELECT name, city, signup_date FROM customers WHERE city = '{city.title()}' ORDER BY name",
                    f"Customers who live in {city.title()}.")
        if has("how many customers", "number of customers", "count of customers", "count customers", "customers do we have"):
            return ("SELECT COUNT(*) AS customer_count FROM customers", "Counts all customers.")
        if has("list customers", "all customers", "show customers", "customers"):
            return ("SELECT name, city, signup_date FROM customers ORDER BY name", "Lists every customer.")
        if has("product") and cat:
            return (f"SELECT name, price FROM products WHERE category = '{cat.title()}' ORDER BY price DESC",
                    f"Products in the {cat.title()} category.")
        if has("how many products", "number of products", "count products"):
            return ("SELECT COUNT(*) AS product_count FROM products", "Counts all products.")
        if has("product"):
            return ("SELECT name, category, price FROM products ORDER BY category, name", "Lists every product.")
        if has("how many orders", "number of orders", "count orders", "total orders"):
            return ("SELECT COUNT(*) AS order_count FROM orders", "Counts all orders.")
        if has("order"):
            return ("SELECT o.id, c.name AS customer, p.name AS product, o.quantity, o.order_date FROM orders o "
                    "JOIN customers c ON c.id = o.customer_id JOIN products p ON p.id = o.product_id "
                    "ORDER BY o.order_date DESC LIMIT 20", "The 20 most recent orders.")
        return None, None


_provider_cache: dict = {}


def get_provider() -> LLMProvider:
    """Provider chosen by LLM_PROVIDER / GEMINI_API_KEY (cached for the life of the process)."""
    mode = config.LLM_PROVIDER
    use_gemini = mode == "gemini" or (mode == "auto" and bool(config.GEMINI_API_KEY))
    key = "gemini" if use_gemini else "demo"
    if key not in _provider_cache:
        _provider_cache[key] = GeminiProvider() if use_gemini else DemoProvider()
    return _provider_cache[key]


def is_demo_ai() -> bool:
    return isinstance(get_provider(), DemoProvider)

"""All settings come from environment variables (or a .env file loaded by your shell/Streamlit)."""
import os
from pathlib import Path


def load_dotenv(path: Path | None = None) -> dict:
    """Read KEY=value lines from the project's .env into os.environ (real environment variables win).
    Returns the values found. Quotes around values are stripped; inline comments are not supported."""
    path = path or Path(__file__).resolve().parent.parent / ".env"
    found: dict = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[key] = value
        os.environ.setdefault(key, value)
    return found


load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


# App database (app_db): users, chats, usage, payments ...
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = _int("DB_PORT", 3306)
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "app_db")

# Target database the LLM writes queries for. Use a SELECT-only account (see sql/003_demo_target.sql).
TARGET_DB_HOST = os.getenv("TARGET_DB_HOST", DB_HOST)
TARGET_DB_PORT = _int("TARGET_DB_PORT", DB_PORT)
TARGET_DB_USER = os.getenv("TARGET_DB_USER", "llm_reader")
TARGET_DB_PASSWORD = os.getenv("TARGET_DB_PASSWORD", "reader_pw_change_me")
TARGET_DB_NAME = os.getenv("TARGET_DB_NAME", "shop_db")

# LLM. LLM_PROVIDER: "auto" (Gemini when GEMINI_API_KEY is set, else the offline demo), "gemini" or "demo".
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Auth
MAX_FAILED_LOGINS = _int("MAX_FAILED_LOGINS", 5)
LOCKOUT_MINUTES = _int("LOCKOUT_MINUTES", 15)
MIN_PASSWORD_LEN = 8
SESSION_HOURS = _int("SESSION_HOURS", 12)
RESET_TOKEN_MINUTES = 30

# Query execution
MAX_ROWS = _int("MAX_ROWS", 500)
QUERY_TIMEOUT_SEC = _int("QUERY_TIMEOUT_SEC", 5)
MAX_QUESTION_LEN = 500
MAX_SQL_RETRIES = _int("MAX_SQL_RETRIES", 1)  # self-correction attempts after the first try
SCHEMA_SAMPLE_ROWS = _int("SCHEMA_SAMPLE_ROWS", 3)  # rows per table sent to the LLM; 0 = none (privacy)

# Billing. With no RAZORPAY_KEY_SECRET the gateway runs in "demo" mode (no real payment).
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
CURRENCY = os.getenv("CURRENCY", "INR")
TAX_RATE = float(os.getenv("TAX_RATE", "0.18"))  # GST
# Token packs: key -> (tokens, price in INR)
TOKEN_PACKS = {
    "small": (100_000, 49.00),
    "medium": (500_000, 199.00),
    "large": (2_000_000, 699.00),
}

# Web app
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # set to 1 when serving over HTTPS
# How password-reset links are delivered. "console": printed to the server log (no email server needed);
# "response": returned in the API response (local development ONLY: anyone could take over accounts).
RESET_LINK_MODE = os.getenv("RESET_LINK_MODE", "console").lower()
APP_NAME = os.getenv("APP_NAME", "SQL LLM")

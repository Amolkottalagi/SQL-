"""FastAPI entry point: JSON API under /api, static HTML pages, security headers."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.deps import COOKIE
from app.routers import account, admin as admin_router, billing, chat
from backend import auth, config, executor, usage
from backend.llm import LLMError, get_provider

BASE = Path(__file__).resolve().parent.parent
STATIC = BASE / "static"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sqlllm")

@asynccontextmanager
async def lifespan(_app):
    from backend.db import get_conn
    try:
        with get_conn() as c:
            n = c.one("SELECT COUNT(*) AS n FROM plans")["n"]
        if n == 0:
            log.error("The plans table is empty. Run: python -m backend.setup_db")
        else:
            log.info("Connected to MySQL database %r. AI provider: %s", config.DB_NAME, type(get_provider()).__name__)
    except Exception as e:  # keep serving so /api/health can report it
        log.error("Cannot reach the app database: %s. Check your .env settings (or run: python -m backend.setup_db).", e)
    yield


app = FastAPI(title=config.APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)  # no public API docs
app.include_router(account.router)
app.include_router(chat.router)
app.include_router(billing.router)
app.include_router(admin_router.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

def _csp() -> str:
    script, frame, connect, img = "'self'", "", "'self'", "'self' data:"
    if config.RAZORPAY_KEY_ID:  # real payments: allow Razorpay's hosted checkout (and nothing else external)
        script += " https://checkout.razorpay.com"
        frame = "frame-src https://api.razorpay.com https://checkout.razorpay.com; "
        connect += " https://lumberjack.razorpay.com https://api.razorpay.com"
        img += " https://*.razorpay.com"
    return (f"default-src 'self'; script-src {script}; style-src 'self'; img-src {img}; connect-src {connect}; "
            f"{frame}base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


CSP = _csp()
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security(request: Request, call_next):
    # CSRF defence: browsers can't add this header to a cross-site form post, and a cross-site fetch()
    # that adds it needs a CORS preflight, which this app never grants.
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
        if request.headers.get("x-requested-with") != "XMLHttpRequest":
            return JSONResponse({"detail": "Missing X-Requested-With header."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ----------------------------------------------------------- error mapping
def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


@app.exception_handler(auth.AuthError)
async def _auth_error(_, e):          # message is written for end users
    return _err(400, str(e))


@app.exception_handler(ValueError)
async def _value_error(_, e):
    return _err(400, str(e))


@app.exception_handler(usage.QuotaExceeded)
async def _quota(_, e):
    return _err(429, str(e))


@app.exception_handler(PermissionError)
async def _perm(_, e):
    return _err(403, str(e))


@app.exception_handler(executor.ExecutionError)
async def _exec(_, e):
    return _err(400, str(e))


@app.exception_handler(LLMError)
async def _llm(_, e):
    log.error("LLM error: %s", e)
    return _err(502, "The AI service is unavailable right now. Please try again.")


@app.exception_handler(RequestValidationError)
async def _validation(_, e):
    first = e.errors()[0] if e.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    return _err(422, f"Invalid input{' for ' + field if field else ''}.")


@app.exception_handler(Exception)
async def _unexpected(request: Request, e: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _err(500, "Something went wrong on the server.")


# -------------------------------------------------------------------- pages
# path -> (html file, access) where access is "public" | "user" | "admin"
PAGES = {
    "/login": ("login.html", "public"), "/signup": ("login.html", "public"),
    "/forgot": ("forgot.html", "public"), "/reset": ("reset.html", "public"),
    "/about": ("about.html", "public"),
    "/chat": ("chat.html", "user"), "/history": ("history.html", "user"), "/saved": ("saved.html", "user"),
    "/usage": ("usage.html", "user"), "/billing": ("billing.html", "user"), "/settings": ("settings.html", "user"),
    "/admin": ("admin.html", "admin"), "/evaluation": ("evaluation.html", "admin"),
}


def _page(path: str, file: str, access: str):
    def handler(request: Request):
        user = auth.validate_session(request.cookies.get(COOKIE))
        if access != "public" and not user:
            return RedirectResponse(f"/login?next={path}", status_code=303)
        if access == "public" and user and path in ("/login", "/signup"):
            return RedirectResponse("/chat", status_code=303)
        if access == "admin" and user["role"] != "admin":
            return RedirectResponse("/chat", status_code=303)
        return FileResponse(STATIC / file)
    app.add_api_route(path, handler, methods=["GET"], include_in_schema=False)


for _path, (_file, _access) in PAGES.items():
    _page(_path, _file, _access)


@app.get("/", include_in_schema=False)
def root(request: Request):
    return RedirectResponse("/chat" if auth.validate_session(request.cookies.get(COOKIE)) else "/login", status_code=303)

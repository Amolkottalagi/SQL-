"""Browser end-to-end test (Playwright). Needs a server running against a FRESH database that already has an
admin user, and RESET_LINK_MODE=response (the password-reset step reads the link from the API response).

    pip install -r requirements-dev.txt && playwright install chromium
    E2E_ADMIN_USER=admin E2E_ADMIN_PASSWORD='Admin#12345' python scripts/e2e_browser.py

Use a throwaway database (e.g. DB_NAME=app_db_e2e), never your real one: it creates users and payments."""
import os, re, sys, time, json
from playwright.sync_api import sync_playwright, expect

BASE = os.getenv("E2E_BASE", "http://127.0.0.1:8000")
SHOTS = os.getenv("E2E_SHOTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e_shots"))
ADMIN_USER = os.getenv("E2E_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("E2E_ADMIN_PASSWORD", "Admin#12345")
os.makedirs(SHOTS, exist_ok=True)
results, problems, ALL = [], [], []

def step(name):
    def deco(fn):
        try:
            fn(); results.append((name, True, "")); print("PASS", name)
        except Exception as e:
            msg = (str(e).strip().splitlines() or [repr(e)])[0][:230]
            for i, pg in enumerate(ALL):
                try: pg.screenshot(path=f"{SHOTS}/FAIL_{re.sub('[^a-z0-9]+','_',name.lower())[:40]}_{i}.png")
                except Exception: pass
            results.append((name, False, msg)); print("FAIL", name, "->", msg)
    return deco

def attach_listeners(page):
    ALL.append(page)
    page.on("console", lambda m: problems.append(("console." + m.type, page.url, m.text)) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(("pageerror", page.url, str(e))))
    page.on("requestfailed", lambda r: problems.append(("requestfailed", page.url, r.url + " " + str(r.failure))))
    def resp(r):
        if r.status >= 400 and r.url.startswith(BASE):
            problems.append(("http%d" % r.status, page.url, r.request.method + " " + r.url.replace(BASE, "")))
    page.on("response", resp)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    page = ctx.new_page(); attach_listeners(page)
    page.set_default_timeout(8000)

    @step("unauthenticated / redirects to login")
    def _():
        page.goto(BASE + "/"); expect(page).to_have_url(re.compile(r"/login$"))
        expect(page.get_by_role("tab", name="Sign in")).to_be_visible()
    page.screenshot(path=f"{SHOTS}/01_login.png")

    @step("protected pages redirect with next=")
    def _():
        page.goto(BASE + "/history"); expect(page).to_have_url(re.compile(r"/login\?next=/history"))

    @step("signup validation: mismatch + missing consent")
    def _():
        page.goto(BASE + "/signup")
        page.fill("#su-user", "demo_user"); page.fill("#su-email", "demo@example.com")
        page.fill("#su-pass", "password123"); page.fill("#su-pass2", "different123")
        page.get_by_role("button", name="Create account").click()
        expect(page.get_by_role("alert")).to_contain_text("don't match")
        page.fill("#su-pass2", "password123"); page.get_by_role("button", name="Create account").click()
        expect(page.get_by_role("alert")).to_contain_text("accept")

    @step("signup succeeds and lands on chat with sidebar")
    def _():
        page.check("#su-consent"); page.get_by_role("button", name="Create account").click()
        expect(page).to_have_url(re.compile(r"/chat$"))
        expect(page.locator("#sidebar")).to_contain_text("demo_user")
        expect(page.locator("#sidebar")).to_contain_text("0 / 20")

    @step("duplicate signup shows server message")
    def _():
        c2 = b.new_context(); p2 = c2.new_page(); p2.goto(BASE + "/signup")
        p2.fill("#su-user", "demo_user"); p2.fill("#su-email", "x@example.com"); p2.fill("#su-pass", "password123"); p2.fill("#su-pass2", "password123")
        p2.check("#su-consent"); p2.get_by_role("button", name="Create account").click()
        expect(p2.get_by_role("alert")).to_contain_text("already registered"); c2.close()

    @step("demo-mode banner is shown")
    def _():
        expect(page.locator("#demo-banner")).to_be_visible()

    @step("ask via example chip: SQL + table result + usage meter")
    def _():
        page.get_by_role("button", name="How many customers do we have?").click()
        expect(page.locator(".a-card .code")).to_contain_text("SELECT COUNT(*)")
        expect(page.locator(".a-card table")).to_contain_text("5")
        expect(page.locator("#sidebar")).to_contain_text("1 / 20")

    @step("no stray null/undefined/NaN text in the page, sidebar usage box clean")
    def _():
        for bad in ("null", "undefined", "NaN"):
            expect(page.locator("body")).not_to_contain_text(bad)
        expect(page.locator("#usage-box")).to_contain_text("Upgrade plan")

    @step("multi-row result defaults to bar chart (CSP-safe SVG), toggles to table/pie/line")
    def _():
        page.fill("#question", "Total revenue per product"); page.press("#question", "Enter")
        card = page.locator(".a-card").nth(1)
        expect(card.locator(".chart-box svg")).to_be_visible()
        assert card.locator("svg rect.ch-bar").count() >= 4, "bars missing"
        h = card.locator("svg rect.ch-bar").first.evaluate("e => e.getBoundingClientRect().height")
        assert h > 5, f"bar has no height ({h})"
        page.wait_for_timeout(400)
        top = page.evaluate("[document.getElementById('topbar').getBoundingClientRect().top, document.getElementById('sidebar').getBoundingClientRect().top]")
        assert top == [0, 0], f"topbar/sidebar not pinned to top: {top}"
        assert page.locator(".chip").first.is_hidden(), "example chips should hide once a conversation exists"
        page.screenshot(path=f"{SHOTS}/02_chat_bar.png")
        card.get_by_role("button", name="Pie").click(); expect(card.locator("svg path.s0")).to_be_visible()
        card.get_by_role("button", name="Line").click(); expect(card.locator("svg polyline.ch-line")).to_be_visible()
        card.get_by_role("button", name="Table").click(); expect(card.locator("table")).to_contain_text("Laptop")

    @step("clarification for an unsupported question")
    def _():
        page.fill("#question", "what is the meaning of life"); page.press("#question", "Enter")
        expect(page.locator(".banner", has_text="more detail").last).to_be_visible()

    @step("feedback: thumbs up, then thumbs down with reason")
    def _():
        card = page.locator(".a-card").nth(1)
        card.get_by_role("button", name="Good answer").click(); expect(page.locator(".toast", has_text="Thanks")).to_be_visible()
        card.get_by_role("button", name="Bad answer").click()
        card.locator("select").select_option("wrong_result"); card.get_by_role("button", name="Send feedback").click()
        expect(card.get_by_role("button", name="Bad answer")).to_have_attribute("aria-pressed", "true")

    @step("save a query")
    def _():
        card = page.locator(".a-card").nth(1)
        card.get_by_role("button", name="Save").click(); expect(card.get_by_role("button", name="Saved")).to_be_visible()

    @step("CSV export is gated on the free plan")
    def _():
        page.locator(".a-card").nth(1).get_by_role("button", name="Export CSV").click()
        expect(page.locator(".toast.error", has_text="Pro plan")).to_be_visible()

    @step("chat list + reopen session from history, 'Show results' works")
    def _():
        expect(page.locator("#sessions a").first).to_be_visible()
        sid = re.search(r"session=(\d+)", page.url).group(1)
        page.goto(f"{BASE}/chat?session={sid}")
        expect(page.locator(".q-bubble")).to_have_count(3)
        expect(page.locator(".banner", has_text="more detail")).to_be_visible()   # clarification text survived
        page.get_by_role("button", name="Show results").first.click()
        expect(page.locator(".a-card table").first).to_be_visible()

    @step("new chat starts empty; XSS in a question is rendered as text")
    def _():
        page.get_by_role("button", name="+ New").click(); expect(page.locator("#messages h2")).to_contain_text("Ask your data")
        page.fill("#question", "<img src=x onerror=window.__xss=1> customers"); page.press("#question", "Enter")
        expect(page.locator(".q-bubble").last).to_contain_text("<img src=x")
        assert page.evaluate("window.__xss") is None, "XSS executed!"
        assert page.locator(".q-bubble img").count() == 0
        expect(page.locator("#ask-btn")).to_be_enabled()

    @step("history: list, filter, re-run modal, delete")
    def _():
        page.goto(BASE + "/history"); expect(page.locator(".item-card")).to_have_count(4)
        page.fill("#search", "revenue"); expect(page.locator(".item-card")).to_have_count(1)
        page.get_by_role("button", name="Re-run (free)").click()
        expect(page.locator(".modal")).to_contain_text("Laptop"); page.keyboard.press("Escape")
        expect(page.locator(".modal")).to_have_count(0)
        page.fill("#search", ""); page.select_option("#status", "clarification"); expect(page.locator(".item-card")).to_have_count(1)
        page.locator(".item-card").get_by_role("button", name="Delete").click(); page.get_by_role("button", name="Delete", exact=True).last.click()
        expect(page.locator(".empty")).to_be_visible()

    @step("saved queries: run + empty-state after remove")
    def _():
        page.goto(BASE + "/saved"); expect(page.locator(".item-card")).to_have_count(1)
        page.get_by_role("button", name="Run (free)").click(); expect(page.locator(".modal")).to_contain_text("Laptop")
        page.keyboard.press("Escape")

    @step("usage page: stats and charts")
    def _():
        page.goto(BASE + "/usage"); expect(page.locator("#stats")).to_contain_text("Queries today")
        expect(page.locator("#chart-tokens svg")).to_be_visible(); expect(page.locator("#chart-queries svg")).to_be_visible()
        page.screenshot(path=f"{SHOTS}/03_usage.png")

    @step("billing: demo upgrade to Pro, history + invoice")
    def _():
        page.goto(BASE + "/billing"); expect(page.locator("#plans")).to_contain_text("Upgrade for")
        page.get_by_role("button", name=re.compile("Upgrade for")).click()
        expect(page.locator(".modal")).to_contain_text("simulated payment")
        page.get_by_role("button", name=re.compile("Pay .* \\(demo\\)")).click()
        expect(page.locator(".toast", has_text="Payment successful")).to_be_visible()
        expect(page.locator("#sidebar")).to_contain_text("pro")
        page.get_by_role("tab", name="Payment history").click()
        expect(page.locator("#payments")).to_contain_text("success")
        page.get_by_role("button", name="Invoice").click(); expect(page.locator(".modal")).to_contain_text("INV-")
        page.keyboard.press("Escape")

    @step("buy a token pack")
    def _():
        page.get_by_role("tab", name="Plans & token packs").click()
        page.get_by_role("button", name="Buy").first.click(); page.get_by_role("button", name=re.compile("Pay .* \\(demo\\)")).click()
        expect(page.locator("#sidebar")).to_contain_text("Purchased tokens: 100,000")

    @step("notifications bell: unread badge, list, cleared after opening")
    def _():
        page.reload()
        expect(page.locator(".bell .count")).to_be_visible()
        page.get_by_role("button", name=re.compile("Notifications")).click()
        expect(page.locator(".dropdown")).to_contain_text("Payment of")
        expect(page.locator(".dropdown")).to_contain_text("now on the pro plan")
        expect(page.locator(".bell .count")).to_be_hidden()
        page.keyboard.press("Escape")

    @step("CSV export works on Pro (real download)")
    def _():
        page.goto(BASE + "/chat"); page.get_by_role("button", name="Total revenue per product").count() or None
        page.fill("#question", "Total revenue per product"); page.press("#question", "Enter")
        expect(page.locator(".a-card table, .a-card svg").first).to_be_visible()
        with page.expect_download() as d:
            page.get_by_role("button", name="Export CSV").last.click()
        text = open(d.value.path()).read()
        assert text.startswith("name,revenue") and "Laptop" in text, text[:80]

    @step("settings: theme + default chart persist across reload")
    def _():
        page.goto(BASE + "/settings")
        page.select_option("#theme", "dark"); page.select_option("#default-chart", "pie"); page.fill("#rows", "5")
        page.get_by_role("button", name="Save preferences").click(); expect(page.locator(".toast", has_text="saved")).to_be_visible()
        page.reload(); expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        expect(page.locator("#default-chart")).to_have_value("pie")
        page.screenshot(path=f"{SHOTS}/04_settings_dark.png")
        page.goto(BASE + "/chat"); page.fill("#question", "list products"); page.press("#question", "Enter")
        expect(page.locator(".a-card .chart-box svg path.s0").last).to_be_visible()   # pie default applies

    @step("settings: change password then forgot/reset flow")
    def _():
        page.goto(BASE + "/settings")
        page.fill("#pw-old", "wrongpass1"); page.fill("#pw-new", "newpassword1"); page.fill("#pw-new2", "newpassword1")
        page.get_by_role("button", name="Change password").click(); expect(page.locator("#pw-msg")).to_contain_text("incorrect")
        page.fill("#pw-old", "password123"); page.get_by_role("button", name="Change password").click()
        expect(page.locator("#pw-msg")).to_contain_text("Password changed")
        page.get_by_role("button", name="Log out").click(); expect(page).to_have_url(re.compile(r"/login"))
        page.goto(BASE + "/forgot"); page.fill("#fp-email", "demo@example.com"); page.get_by_role("button", name="Send reset link").click()
        link = page.locator("#auth-root .form-ok a").get_attribute("href")
        page.goto(link); page.fill("#rp-pass", "resetpassword1"); page.fill("#rp-pass2", "resetpassword1")
        page.get_by_role("button", name="Set new password").click(); expect(page.locator(".form-ok")).to_contain_text("changed")
        page.get_by_role("link", name="Sign in").first.click()
        page.fill("#li-user", "demo_user"); page.fill("#li-pass", "newpassword1"); page.get_by_role("button", name="Sign in").click()
        expect(page.locator(".form-error")).to_contain_text("Invalid")      # old password no longer works
        page.fill("#li-pass", "resetpassword1"); page.get_by_role("button", name="Sign in").click()
        expect(page).to_have_url(re.compile(r"/chat$"))

    @step("non-admin cannot open admin pages or admin API")
    def _():
        page.goto(BASE + "/admin"); expect(page).to_have_url(re.compile(r"/chat$"))
        r = page.evaluate("fetch('/api/admin/overview').then(r => r.status)"); assert r == 403, r
        expect(page.locator("#sidebar")).not_to_contain_text("Admin dashboard")

    @step("login lockout after repeated failures")
    def _():
        page.get_by_role("button", name="Log out").click()
        for _ in range(5):
            page.fill("#li-user", "demo_user"); page.fill("#li-pass", "bad-password"); page.get_by_role("button", name="Sign in").click()
            expect(page.locator(".form-error")).to_be_visible()
        page.fill("#li-pass", "resetpassword1"); page.get_by_role("button", name="Sign in").click()
        expect(page.locator(".form-error")).to_contain_text("Too many")

    @step("CSRF: state-changing request without header is rejected")
    def _():
        r = page.evaluate("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.status)")
        assert r == 403, r

    # ---------------- admin
    ctx2 = b.new_context(viewport={"width": 1280, "height": 900}); ap = ctx2.new_page(); attach_listeners(ap); ap.set_default_timeout(8000)

    @step("admin signs in and sees admin nav")
    def _():
        ap.goto(BASE + "/login"); ap.fill("#li-user", ADMIN_USER); ap.fill("#li-pass", ADMIN_PASSWORD); ap.get_by_role("button", name="Sign in").click()
        expect(ap).to_have_url(re.compile(r"/chat$")); expect(ap.locator("#sidebar")).to_contain_text("Admin dashboard")
        expect(ap.locator("#sidebar")).to_contain_text("unlimited")

    @step("admin overview: stats, charts, top users")
    def _():
        ap.goto(BASE + "/admin"); expect(ap.locator("#ov-stats")).to_contain_text("Users")
        expect(ap.locator("#ov-status svg")).to_be_visible(); expect(ap.locator("#ov-top")).to_contain_text("demo_user")
        w = ap.locator("#ov-status svg").evaluate("e => e.getBoundingClientRect().width"); assert w <= 232, f"pie too large: {w}"
        for bad in ("null", "undefined", "NaN"):
            expect(ap.locator("body")).not_to_contain_text(bad)
        lo = ap.get_by_role("button", name="Log out").bounding_box(); assert lo["height"] < 40, f"Log out wraps: {lo}"
        ap.screenshot(path=f"{SHOTS}/05_admin.png", full_page=True)

    @step("admin users: search, change plan, give tokens, self-protection")
    def _():
        ap.get_by_role("tab", name="Users").click()
        ap.fill("#user-search", "demo"); expect(ap.locator("#users tbody tr")).to_have_count(1)
        ap.locator("#users select").select_option("free"); expect(ap.locator(".toast", has_text="Plan updated")).to_be_visible()
        ap.get_by_role("button", name="Give tokens").click(); ap.get_by_role("button", name="Give tokens").last.click()
        expect(ap.locator(".toast", has_text="Tokens added")).to_be_visible()
        ap.fill("#user-search", "admin"); expect(ap.locator("#users tbody tr")).to_have_count(1)
        expect(ap.locator("#users").get_by_role("button", name="Disable")).to_be_disabled()

    @step("admin payments: refund")
    def _():
        ap.get_by_role("tab", name="Payments").click()
        ap.get_by_role("button", name="Refund").first.click(); ap.get_by_role("button", name="Refund", exact=True).last.click()
        expect(ap.locator(".toast", has_text="refunded")).to_be_visible()

    @step("admin prompts: create + activate template, add few-shot example")
    def _():
        ap.get_by_role("tab", name="Prompts & examples").click()
        ap.get_by_role("button", name="+ New template").click(); ap.fill("#t-name", "e2e"); ap.get_by_role("button", name="Create template").click()
        expect(ap.locator("#templates")).to_contain_text("e2e")
        ap.locator("#templates tr", has_text="e2e").get_by_role("button", name="Activate").click()
        expect(ap.locator("#templates tr", has_text="e2e")).to_contain_text("active")
        ap.get_by_role("button", name="+ New example").click(); ap.fill("#e-q", "How many products?"); ap.fill("#e-s", "SELECT COUNT(*) FROM products")
        ap.get_by_role("button", name="Add example").click(); expect(ap.locator("#examples")).to_contain_text("How many products?")
        ap.get_by_role("button", name="+ New template").click(); ap.fill("#t-name", "bad"); ap.fill("#t-text", "no placeholders"); ap.get_by_role("button", name="Create template").click()
        expect(ap.locator(".toast.error")).to_contain_text("placeholders"); ap.keyboard.press("Escape")

    @step("admin logs: errors / blocked / audit")
    def _():
        ap.get_by_role("tab", name="Logs").click(); ap.select_option("#log-kind", "audit")
        expect(ap.locator("#logs")).to_contain_text("change_plan"); ap.select_option("#log-kind", "blocked"); ap.select_option("#log-kind", "errors")

    @step("evaluation: run zero-shot and few-shot, see accuracy + details")
    def _():
        ap.goto(BASE + "/evaluation"); expect(ap.locator("#demo-note")).to_be_visible()
        ap.get_by_role("button", name="Run experiment").click()
        expect(ap.locator("#detail")).to_be_visible(timeout=30000); expect(ap.locator("#detail-stats")).to_contain_text("Accuracy")
        ap.select_option("#strategy", "few_shot"); ap.get_by_role("button", name="Run experiment").click()
        expect(ap.locator("#runs tbody tr")).to_have_count(2, timeout=30000)
        expect(ap.locator("#acc-chart svg")).to_be_visible()
        assert ap.locator("#details tbody tr").count() >= 6
        ap.screenshot(path=f"{SHOTS}/06_evaluation.png", full_page=True)

    @step("evaluation: add and delete a test question; bad SQL rejected")
    def _():
        ap.get_by_role("button", name="+ Add question").click(); ap.fill("#q-q", "Count products"); ap.fill("#q-s", "SELECT COUNT(*) FROM products")
        ap.get_by_role("button", name="Add test question").click(); expect(ap.locator("#questions")).to_contain_text("Count products")
        ap.get_by_role("button", name="+ Add question").click(); ap.fill("#q-q", "bad"); ap.fill("#q-s", "SELECT * FROM missing")
        ap.get_by_role("button", name="Add test question").click(); expect(ap.locator(".toast.error")).to_be_visible(); ap.keyboard.press("Escape")

    # ---------------- responsive + public about
    mctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2); mp = mctx.new_page(); attach_listeners(mp); mp.set_default_timeout(8000)

    @step("mobile: no horizontal scroll, menu opens, chart fits")
    def _():
        mp.goto(BASE + "/login"); mp.fill("#li-user", "admin"); mp.fill("#li-pass", "Admin#12345"); mp.get_by_role("button", name="Sign in").click()
        expect(mp).to_have_url(re.compile(r"/chat$"))
        mp.get_by_role("button", name="Total revenue per product").click()
        expect(mp.locator(".a-card svg, .a-card table").first).to_be_visible()
        sw = mp.evaluate("document.documentElement.scrollWidth"); assert sw <= 392, f"horizontal overflow: {sw}"
        ch = mp.evaluate("document.querySelector('.composer').getBoundingClientRect().height"); assert ch < 200, f"composer too tall on mobile: {ch}"
        mp.wait_for_timeout(300)
        mp.screenshot(path=f"{SHOTS}/07_mobile_chat.png")
        mp.get_by_role("button", name="Open menu").click(); expect(mp.locator("#sidebar")).to_have_class(re.compile("open"))
        mp.screenshot(path=f"{SHOTS}/08_mobile_menu.png")

    @step("about page is public (no redirect) and shows sign-in link")
    def _():
        c4 = b.new_context(); p4 = c4.new_page(); attach_listeners(p4); p4.goto(BASE + "/about")
        expect(p4.locator("main")).to_contain_text("Privacy"); expect(p4.get_by_role("link", name="Sign in")).to_be_visible(); c4.close()

    b.close()

print("\n==== SUMMARY ====")
fails = [r for r in results if not r[1]]
print(f"{len(results)-len(fails)}/{len(results)} steps passed")
for n, ok, m in fails: print("  FAILED:", n, "|", m)
# expected noise: 401 from public pages that probe the session, 400/403/429 produced deliberately by tests
expected = ("http401", "http400", "http403", "http429", "http422")
real = [x for x in problems if x[0] not in expected]
print(f"\nbrowser problems (excluding deliberate 4xx): {len(real)}")
seen = set()
for kind, url, text in real:
    key = (kind, text[:120])
    if key in seen: continue
    seen.add(key); print(" ", kind, "|", url.replace(BASE, ""), "|", text[:240])
sys.exit(1 if fails else 0)

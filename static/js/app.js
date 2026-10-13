/* Shared client library: DOM helper, API client, layout, charts, result tables.
   Everything is built with textContent / createElement, so data from the server can never inject HTML. */
"use strict";
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const bodyEl = document.body;
  const ACCESS = bodyEl.dataset.access || "user";
  const PUBLIC = ACCESS === "public";

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  // ------------------------------------------------------------------ DOM
  function append(parent, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      parent.append(k instanceof Node ? k : document.createTextNode(String(k)));
    }
  }
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "value") e.value = v;
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    append(e, kids);
    return e;
  }
  /** el.replaceChildren(...) but ignoring null/false (the native method turns null into the text "null"). */
  function setKids(el, ...kids) {
    el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
  }
  function svg(tag, attrs, ...kids) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    append(e, kids);
    return e;
  }

  // ------------------------------------------------------------------ API
  async function api(method, url, body) {
    const opts = { method, credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try { res = await fetch(url, opts); }
    catch (e) { throw new ApiError("Cannot reach the server. Check your connection.", 0); }
    let data = null;
    if ((res.headers.get("content-type") || "").includes("json")) data = await res.json().catch(() => null);
    if (!res.ok) {
      if (res.status === 401 && !PUBLIC) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      }
      throw new ApiError((data && data.detail) || `Request failed (${res.status}).`, res.status);
    }
    return data;
  }

  // -------------------------------------------------------------- helpers
  const num = (n) => (n == null ? "–" : Number(n).toLocaleString());
  const date = (s) => (s ? String(s).replace("T", " ").slice(0, 16) : "");
  const money = (n, cur) => {
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "INR" }).format(Number(n)); }
    catch (e) { return `${cur || ""} ${Number(n).toFixed(2)}`; }
  };
  const compact = (n) => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
  const short = (s, n) => (String(s).length > n ? String(s).slice(0, n - 1) + "…" : String(s));

  function toast(message, type) {
    let box = document.querySelector(".toasts");
    if (!box) {
      box = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
      bodyEl.append(box);
    }
    const t = h("div", { class: "toast " + (type || "") }, message);
    box.append(t);
    setTimeout(() => t.remove(), type === "error" ? 6500 : 3500);
  }

  /** Disable a button while an async action runs; show errors as toasts. */
  async function run(btn, fn) {
    if (btn) btn.disabled = true;
    try { return await fn(); }
    catch (e) { toast(e.message || "Something went wrong.", "error"); }
    finally { if (btn) btn.disabled = false; }
  }

  function modal(title, content, opts = {}) {
    const prev = document.activeElement;
    let backdrop;
    const onKey = (e) => { if (e.key === "Escape") close(); };
    function close() {
      if (!backdrop.isConnected) return;
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      if (prev && prev.focus) prev.focus();
      if (opts.onClose) opts.onClose();
    }
    const box = h("div", { class: "modal" + (opts.wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "row between mb" },
        h("h2", { text: title }),
        h("button", { class: "btn btn-ghost btn-sm", "aria-label": "Close", onclick: close, text: "✕" })),
      content);
    backdrop = h("div", { class: "modal-backdrop", onmousedown: (e) => { if (e.target === backdrop) close(); } }, box);
    document.addEventListener("keydown", onKey);
    bodyEl.append(backdrop);
    const focusable = box.querySelector("input, select, textarea, .btn-primary") || box.querySelector("button");
    if (focusable) focusable.focus();
    return { close, box };
  }

  function confirmDialog(message, opts = {}) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (done) return; done = true; m.close(); resolve(v); };
      const content = h("div", {},
        h("p", { text: message }),
        h("div", { class: "row" },
          h("button", { class: "btn " + (opts.danger ? "btn-danger" : "btn-primary"), onclick: () => finish(true), text: opts.confirmText || "Confirm" }),
          h("button", { class: "btn", onclick: () => finish(false), text: "Cancel" })));
      const m = modal(opts.title || "Please confirm", content, { onClose: () => finish(false) });
    });
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied to clipboard", "success"); return; } catch (e) { /* fall through */ }
    const ta = h("textarea", { "aria-hidden": "true" });
    ta.value = text; bodyEl.append(ta); ta.select();
    try { document.execCommand("copy"); toast("Copied to clipboard", "success"); }
    catch (e) { toast("Could not copy. Select the text and copy it manually.", "error"); }
    ta.remove();
  }

  const badge = (status, label) => h("span", { class: "badge " + status, text: label || status });
  const codeBlock = (text) => {
    const pre = h("div", { class: "code" }, h("button", { class: "btn btn-sm copy", onclick: () => copyText(text), text: "Copy" }), h("code", { text }));
    return pre;
  };
  const statCard = (label, value, sub) =>
    h("div", { class: "card stat" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), sub ? h("div", { class: "sub", text: sub }) : null);

  function table(headers, rows, opts = {}) {
    if (!rows.length) return h("div", { class: "empty", text: opts.empty || "Nothing here yet." });
    const numeric = new Set(opts.numeric || []);
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, headers.map((t, i) => h("th", { class: numeric.has(i) ? "right" : "", scope: "col", text: t })))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: numeric.has(i) ? "num" : "" }, c)))))));
  }

  // --------------------------------------------------------------- charts
  const niceMax = (v) => {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  };
  const CH = { W: 520, H: 240, l: 46, r: 12, t: 16, b: 40 };

  function chartFrame(container, items, opts) {
    container.replaceChildren();
    const vals = items.map((i) => Number(i.value) || 0);
    if (!items.length || vals.every((v) => v === 0)) {
      container.append(h("div", { class: "empty", text: opts.empty || "No data yet." }));
      return null;
    }
    const max = niceMax(Math.max(...vals));
    const iw = CH.W - CH.l - CH.r, ih = CH.H - CH.t - CH.b;
    const root = svg("svg", { viewBox: `0 0 ${CH.W} ${CH.H}`, role: "img", "aria-label": opts.title || "Chart" });
    for (let i = 0; i <= 4; i++) {
      const y = CH.t + ih - (ih * i) / 4;
      root.append(svg("line", { class: i ? "ch-grid" : "ch-axis", x1: CH.l, x2: CH.W - CH.r, y1: y, y2: y }),
        svg("text", { class: "ch-label", x: CH.l - 7, y: y + 4, "text-anchor": "end" }, compact((max * i) / 4)));
    }
    return { root, vals, max, iw, ih };
  }

  function barChart(container, items, opts = {}) {
    const f = chartFrame(container, items, opts);
    if (!f) return;
    const band = f.iw / items.length, bw = Math.min(band * 0.7, 56), every = Math.ceil(items.length / 10);
    items.forEach((it, i) => {
      const bh = (f.ih * f.vals[i]) / f.max, x = CH.l + band * i + (band - bw) / 2, y = CH.t + f.ih - bh;
      f.root.append(svg("rect", { class: "ch-bar", x, y, width: bw, height: Math.max(bh, 0), rx: 3 },
        svg("title", {}, `${it.label}: ${(opts.format || num)(f.vals[i])}`)));
      if (items.length <= 14) f.root.append(svg("text", { class: "ch-value", x: x + bw / 2, y: y - 5, "text-anchor": "middle" }, (opts.format || compact)(f.vals[i])));
      if (i % every === 0) f.root.append(svg("text", { class: "ch-label", x: x + bw / 2, y: CH.H - CH.b + 16, "text-anchor": "middle" }, short(it.label, 12)));
    });
    container.append(f.root);
  }

  function lineChart(container, items, opts = {}) {
    const f = chartFrame(container, items, opts);
    if (!f) return;
    const step = items.length > 1 ? f.iw / (items.length - 1) : 0, every = Math.ceil(items.length / 10);
    const pts = items.map((it, i) => [CH.l + (items.length > 1 ? step * i : f.iw / 2), CH.t + f.ih - (f.ih * f.vals[i]) / f.max]);
    const base = CH.t + f.ih;
    f.root.append(svg("polygon", { class: "ch-area", points: [[pts[0][0], base], ...pts, [pts[pts.length - 1][0], base]].map((p) => p.join(",")).join(" ") }));
    f.root.append(svg("polyline", { class: "ch-line", points: pts.map((p) => p.join(",")).join(" ") }));
    pts.forEach((p, i) => {
      if (items.length <= 31) f.root.append(svg("circle", { class: "ch-dot", cx: p[0], cy: p[1], r: 3.5 }, svg("title", {}, `${items[i].label}: ${(opts.format || num)(f.vals[i])}`)));
      if (i % every === 0) f.root.append(svg("text", { class: "ch-label", x: p[0], y: CH.H - CH.b + 16, "text-anchor": "middle" }, short(items[i].label, 12)));
    });
    container.append(f.root);
  }

  function pieChart(container, items, opts = {}) {
    container.replaceChildren();
    let data = items.map((i) => ({ label: String(i.label), value: Number(i.value) || 0 })).filter((i) => i.value > 0);
    if (!data.length) { container.append(h("div", { class: "empty", text: opts.empty || "No data yet." })); return; }
    data.sort((a, b) => b.value - a.value);
    if (data.length > 8) {
      const rest = data.slice(7).reduce((s, d) => s + d.value, 0);
      data = [...data.slice(0, 7), { label: "Other", value: rest }];
    }
    const total = data.reduce((s, d) => s + d.value, 0), R = 100, cx = 130, cy = 120;
    const root = svg("svg", { class: "pie-svg", viewBox: "0 0 260 240", role: "img", "aria-label": opts.title || "Pie chart" });
    let angle = -Math.PI / 2;
    data.forEach((d, i) => {
      const frac = d.value / total, tip = svg("title", {}, `${d.label}: ${num(d.value)} (${(frac * 100).toFixed(1)}%)`);
      if (frac >= 0.9999) { root.append(svg("circle", { class: "s" + i, cx, cy, r: R }, tip)); return; }
      const a2 = angle + frac * 2 * Math.PI, large = frac > 0.5 ? 1 : 0;
      const p = (a) => [cx + R * Math.cos(a), cy + R * Math.sin(a)];
      const [x1, y1] = p(angle), [x2, y2] = p(a2);
      root.append(svg("path", { class: "s" + i, d: `M${cx},${cy} L${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2} Z`, stroke: "var(--surface)", "stroke-width": 1.5 }, tip));
      angle = a2;
    });
    container.append(root, h("div", { class: "legend" }, data.map((d, i) =>
      h("span", {}, h("i", { class: "l" + i }), `${short(d.label, 22)} (${((d.value / total) * 100).toFixed(0)}%)`))));
  }

  // ---------------------------------------------------------- result view
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);

  /** Pick a label column and a value column so a result can be charted, or null if it can't. */
  function chartSpec(cols, rows) {
    if (cols.length < 2 || rows.length < 2 || rows.length > 60) return null;
    const numeric = cols.map((_, c) => rows.every((r) => r[c] == null || isNum(r[c])) && rows.some((r) => isNum(r[c])));
    const vi = numeric.lastIndexOf(true);
    if (vi < 0) return null;
    let li = numeric.findIndex((n) => !n);
    if (li < 0) li = vi === 0 ? 1 : 0;
    return { li, vi, items: rows.map((r) => ({ label: r[li] == null ? "NULL" : String(r[li]), value: isNum(r[vi]) ? r[vi] : 0 })), vLabel: cols[vi] };
  }

  function cellNode(v) {
    if (v == null) return h("td", { class: "muted" }, h("em", { text: "NULL" }));
    if (isNum(v)) return h("td", { class: "num", text: Number.isInteger(v) ? String(v) : String(Number(v.toFixed(4))) });
    return h("td", { text: String(v) });
  }

  function tableView(cols, rows, perPage) {
    let page = 0;
    const pages = Math.max(1, Math.ceil(rows.length / perPage));
    const tbody = h("tbody");
    const info = h("span", { class: "muted small" });
    const prev = h("button", { class: "btn btn-sm", text: "‹ Prev", onclick: () => { page--; draw(); } });
    const next = h("button", { class: "btn btn-sm", text: "Next ›", onclick: () => { page++; draw(); } });
    const pager = h("div", { class: "pager" }, info, prev, next);
    function draw() {
      tbody.replaceChildren(...rows.slice(page * perPage, (page + 1) * perPage).map((r) => h("tr", {}, r.map(cellNode))));
      info.textContent = `Rows ${page * perPage + 1}–${Math.min(rows.length, (page + 1) * perPage)} of ${rows.length}`;
      prev.disabled = page === 0; next.disabled = page >= pages - 1;
    }
    draw();
    if (pages === 1) pager.classList.add("hidden");
    return h("div", {},
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, cols.map((c) => h("th", { scope: "col", text: c })))), tbody)),
      pager);
  }

  /** Table plus (when it makes sense and the plan allows) bar/line/pie views with a toggle. */
  function resultView(res, ctx = {}) {
    const cols = res.columns || [], rows = res.rows || [];
    if (!cols.length) return h("div", { class: "empty", text: "The query returned no columns." });
    if (!rows.length) return h("div", { class: "empty", text: "No rows matched." });
    const spec = ctx.canChart === false ? null : chartSpec(cols, rows);
    const wrap = h("div", {});
    const body = h("div", {});
    const views = ["table", ...(spec ? ["bar", "line", "pie"] : [])];
    const labels = { table: "Table", bar: "Bar", line: "Line", pie: "Pie" };
    const initial = spec && ["bar", "line", "pie"].includes(ctx.chartType) ? ctx.chartType : "table";
    const buttons = {};
    function show(v) {
      for (const [k, b] of Object.entries(buttons)) { b.classList.toggle("active", k === v); b.setAttribute("aria-pressed", String(k === v)); }
      body.replaceChildren();
      if (v === "table") body.append(tableView(cols, rows, ctx.perPage || 50));
      else {
        const box = h("div", { class: "chart-box" });
        body.append(box, h("div", { class: "muted small", text: `${spec.vLabel} by ${cols[spec.li]}` }));
        ({ bar: barChart, line: lineChart, pie: pieChart }[v])(box, spec.items, { title: `${spec.vLabel} by ${cols[spec.li]}` });
      }
    }
    if (views.length > 1) {
      const toggle = h("div", { class: "view-toggle", role: "group", "aria-label": "Result view" });
      views.forEach((v) => { buttons[v] = h("button", { text: labels[v], onclick: () => show(v) }); toggle.append(buttons[v]); });
      wrap.append(h("div", { class: "mb" }, toggle));
    } else buttons.table = h("button");
    wrap.append(body);
    show(initial);
    return wrap;
  }

  // --------------------------------------------------------------- layout
  const NAV = [
    { key: "chat", href: "/chat", icon: "💬", label: "Chat" },
    { key: "history", href: "/history", icon: "🕘", label: "History" },
    { key: "saved", href: "/saved", icon: "⭐", label: "Saved queries" },
    { key: "usage", href: "/usage", icon: "📊", label: "Usage" },
    { key: "billing", href: "/billing", icon: "💳", label: "Plans & billing" },
    { key: "settings", href: "/settings", icon: "⚙️", label: "Settings" },
  ];
  const ADMIN_NAV = [
    { key: "admin", href: "/admin", icon: "🛡️", label: "Admin dashboard" },
    { key: "evaluation", href: "/evaluation", icon: "🧪", label: "Evaluation" },
  ];
  const ABOUT = { key: "about", href: "/about", icon: "ℹ️", label: "About & help" };

  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("theme", t); } catch (e) { /* ignore */ }
  }

  function meterRow(label, used, limit) {
    const pct = limit == null ? 0 : Math.min(100, (used / limit) * 100);
    const bar = h("span");
    bar.style.width = pct + "%";
    return h("div", {},
      h("div", { class: "row between small" }, h("span", { text: label }),
        h("span", { class: "muted", text: limit == null ? `${num(used)} · unlimited` : `${num(used)} / ${num(limit)}` })),
      h("div", { class: "meter" + (pct >= 100 ? " full" : pct >= 80 ? " warn" : ""), role: "progressbar", "aria-label": label + " used today",
        "aria-valuemin": "0", "aria-valuemax": String(limit == null ? 0 : limit), "aria-valuenow": String(used) }, bar));
  }

  function buildSidebar(ctx, meta) {
    const el = document.getElementById("sidebar");
    const here = bodyEl.dataset.page;
    const link = (n) => h("a", { href: n.href, class: n.key === here ? "active" : "", "aria-current": n.key === here ? "page" : null },
      h("span", { class: "ico", "aria-hidden": "true", text: n.icon }), n.label);
    const usageBox = h("div", { class: "meter-box stack", id: "usage-box" });
    ctx.renderUsage = (u) => {
      setKids(usageBox,
        h("div", { class: "row between small" }, h("strong", { text: "Today" }), h("span", { class: "badge plan", text: u.plan })),
        meterRow("Queries", u.queries_used, u.queries_limit),
        meterRow("Tokens", u.tokens_used, u.tokens_limit),
        u.token_balance > 0 ? h("div", { class: "small muted", text: `Purchased tokens: ${num(u.token_balance)}` }) : null,
        u.plan === "free" ? h("a", { class: "btn btn-primary btn-sm", href: "/billing", text: "Upgrade plan" }) : null);
    };
    ctx.renderUsage(ctx.me.usage);
    el.append(
      h("a", { class: "brand", href: "/chat" }, h("span", { class: "brand-mark", "aria-hidden": "true", text: "S" }), meta.app_name),
      h("nav", { class: "nav", "aria-label": "Main" },
        NAV.map(link),
        ctx.user.role === "admin" ? [h("div", { class: "nav-label", text: "Admin" }), ADMIN_NAV.map(link)] : null,
        h("div", { class: "nav-label", text: "Help" }), link(ABOUT)),
      h("div", { class: "side-bottom" }, usageBox,
        h("div", { class: "usercard" },
          h("div", { class: "avatar", "aria-hidden": "true", text: ctx.user.username.slice(0, 1) }),
          h("div", { class: "grow ellipsis" }, h("div", { class: "small", text: ctx.user.username }), h("div", { class: "small muted", text: ctx.user.email }))),
        h("button", { class: "btn btn-sm btn-block", text: "Log out", onclick: async (e) => {
          await run(e.target, async () => { await api("POST", "/api/auth/logout"); location.href = "/login"; });
        } })));
  }

  function buildBell(ctx) {
    const count = h("span", { class: "count hidden", "aria-hidden": "true" });
    const dd = h("div", { class: "dropdown hidden", role: "region", "aria-label": "Notifications" });
    const btn = h("button", { class: "btn btn-icon", "aria-label": "Notifications", "aria-expanded": "false", text: "🔔" });
    const setCount = (n) => { count.textContent = n > 99 ? "99+" : String(n); count.classList.toggle("hidden", !n); btn.setAttribute("aria-label", n ? `Notifications, ${n} unread` : "Notifications"); };
    setCount(ctx.me.unread_notifications);
    async function open() {
      dd.classList.remove("hidden"); btn.setAttribute("aria-expanded", "true");
      dd.replaceChildren(h("div", { class: "item muted", text: "Loading…" }));
      try {
        const r = await api("GET", "/api/notifications");
        dd.replaceChildren(...(r.items.length
          ? r.items.map((n) => h("div", { class: "item" + (n.is_read ? "" : " unread") }, h("div", { text: n.message }), h("div", { class: "small muted", text: date(n.created_at) })))
          : [h("div", { class: "item muted", text: "No notifications yet." })]));
        if (r.unread) { await api("POST", "/api/notifications/read-all"); setCount(0); }
      } catch (e) { dd.replaceChildren(h("div", { class: "item", text: e.message })); }
    }
    const close = () => { dd.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", (e) => { e.stopPropagation(); dd.classList.contains("hidden") ? open() : close(); });
    document.addEventListener("click", (e) => { if (!dd.contains(e.target)) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    setInterval(async () => { try { setCount((await api("GET", "/api/notifications?unread_only=true")).unread); } catch (e) { /* offline: ignore */ } }, 60000);
    ctx.setUnread = setCount;
    return h("div", { class: "bell" }, btn, count, dd);
  }

  function buildTopbar(ctx, meta) {
    const el = document.getElementById("topbar");
    const title = bodyEl.dataset.title || "";
    if (!ctx.user) {
      el.append(h("a", { class: "brand", href: "/" }, h("span", { class: "brand-mark", "aria-hidden": "true", text: "S" }), meta.app_name),
        h("div", { class: "grow" }), h("a", { class: "btn btn-primary", href: "/login", text: "Sign in" }));
      return;
    }
    const sidebar = document.getElementById("sidebar");
    const menu = h("button", { class: "btn btn-icon menu-btn", "aria-label": "Open menu", text: "☰", onclick: (e) => { e.stopPropagation(); sidebar.classList.toggle("open"); } });
    document.addEventListener("click", (e) => { if (!sidebar.contains(e.target)) sidebar.classList.remove("open"); });
    const themeBtn = h("button", { class: "btn btn-icon", "aria-label": "Toggle dark mode", onclick: async (e) => {
      const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      setTheme(next);
      await run(e.target, async () => { ctx.settings = await api("PUT", "/api/settings", { ...ctx.settings, theme: next }); });
    } }, "🌓");
    el.append(menu, h("h1", { text: title }), h("div", { class: "grow" }), themeBtn, buildBell(ctx));
  }

  /** Call once per page. Resolves with {meta, me, user, settings, setUsage}. */
  async function init() {
    const meta = await api("GET", "/api/meta").catch(() => ({ app_name: "SQL LLM" }));
    let me = null;
    try { me = await api("GET", "/api/auth/me"); }
    catch (e) {
      if (e.status === 401 && !PUBLIC) return new Promise(() => {}); // redirecting to /login
      if (!PUBLIC) { toast(e.message, "error"); throw e; }
    }
    const ctx = { meta, me, user: me && me.user, settings: me ? me.settings : { default_chart_type: "bar", theme: "light", rows_per_page: 50 } };
    if (ACCESS === "admin" && (!ctx.user || ctx.user.role !== "admin")) { location.href = "/chat"; return new Promise(() => {}); }
    if (me) setTheme(me.settings.theme);
    if (ctx.user) buildSidebar(ctx, meta); else document.getElementById("sidebar").remove();
    buildTopbar(ctx, meta);
    ctx.setUsage = (u) => { if (u && ctx.renderUsage) { ctx.me.usage = u; ctx.renderUsage(u); } };
    ctx.refreshMe = async () => { ctx.me = await api("GET", "/api/auth/me"); ctx.user = ctx.me.user; ctx.settings = ctx.me.settings; ctx.setUsage(ctx.me.usage); if (ctx.setUnread) ctx.setUnread(ctx.me.unread_notifications); return ctx.me; };
    return ctx;
  }

  window.App = {
    init, api, h, svg, toast, modal, confirmDialog, copyText, run, badge, codeBlock, statCard, table,
    barChart, lineChart, pieChart, resultView, setTheme, ApiError, fmt: { num, date, money, compact, short },
  };
})();

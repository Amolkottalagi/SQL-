"use strict";
(async () => {
  const { api, h, run, toast, fmt, modal, confirmDialog, table, badge, statCard, barChart, lineChart, pieChart, codeBlock } = App;
  const ctx = await App.init();
  const $ = (id) => document.getElementById(id);
  const me = ctx.user;
  const loaded = {};
  const cur = ctx.meta.currency || "INR";

  // ---------------------------------------------------------------- tabs
  const loaders = { overview, users, payments, prompts, logs };
  function tab(which) {
    document.querySelectorAll(".tab").forEach((t) => { const on = t.dataset.tab === which; t.classList.toggle("active", on); t.setAttribute("aria-selected", String(on)); });
    document.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== which));
    loaders[which]().catch((e) => toast(e.message, "error"));
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => tab(t.dataset.tab)));

  const form = (fields, submitText, onSubmit, opts = {}) => {
    const f = h("form", { class: "stack", novalidate: true }, fields, h("button", { class: "btn btn-primary", type: "submit", text: submitText }));
    f.addEventListener("submit", async (e) => { e.preventDefault(); await run(f.querySelector("button"), () => onSubmit(f)); });
    return f;
  };
  const field = (id, label, control, hint) => h("div", { class: "field" }, h("label", { for: id, text: label }), control, hint ? h("div", { class: "hint", text: hint }) : null);

  // ------------------------------------------------------------ overview
  async function overview() {
    const r = await api("GET", "/api/admin/overview"), o = r.overview;
    $("ov-stats").replaceChildren(
      statCard("Users", fmt.num(o.total_users), `${fmt.num(o.active_users)} active`),
      statCard("Questions", fmt.num(o.total_questions), `${fmt.num(o.queries_today)} today`),
      statCard("Tokens used", fmt.compact(o.total_tokens), `${fmt.compact(o.tokens_today)} today`),
      statCard("Error rate", (o.error_rate * 100).toFixed(1) + "%", "Questions whose final answer failed"),
      statCard("Feedback", `${fmt.num(o.thumbs_up)} 👍  ${fmt.num(o.thumbs_down)} 👎`, "From users"),
      statCard("Revenue", fmt.money(o.revenue, cur), "Successful payments"));
    pieChart($("ov-status"), r.status.map((s) => ({ label: s.status, value: s.n })), { title: "Questions by final status", empty: "No questions yet." });
    lineChart($("ov-tokens"), r.daily.map((d) => ({ label: String(d.day).slice(5), value: Number(d.tokens) })), { title: "Tokens per day", empty: "No usage yet." });
    barChart($("ov-queries"), r.daily.map((d) => ({ label: String(d.day).slice(5), value: Number(d.queries) })), { title: "Queries per day", empty: "No queries yet." });
    barChart($("ov-reasons"), r.feedback_reasons.map((d) => ({ label: d.reason, value: d.n })), { title: "Negative feedback reasons", empty: "No negative feedback." });
    $("ov-top").replaceChildren(table(["User", "Plan", "Questions", "Tokens"], r.top_users.map((u) => [u.username, u.plan, fmt.num(u.questions), fmt.num(u.tokens)]), { numeric: [2, 3], empty: "No users." }));
  }

  // --------------------------------------------------------------- users
  let planNames = [];
  async function users() {
    if (!planNames.length) planNames = (await api("GET", "/api/admin/plans")).items.map((p) => p.name);
    const q = $("user-search").value.trim();
    const r = await api("GET", "/api/admin/users" + (q ? "?q=" + encodeURIComponent(q) : ""));
    const reload = () => users().catch((e) => toast(e.message, "error"));
    $("users").replaceChildren(table(["User", "Role", "Plan", "Status", "Last login", "Actions"], r.items.map((u) => {
      const self = u.id === me.id;
      const plan = h("select", { "aria-label": `Plan for ${u.username}`, onchange: (e) => run(e.target, async () => { await api("POST", `/api/admin/users/${u.id}/plan`, { plan: e.target.value }); toast("Plan updated", "success"); }) },
        planNames.map((n) => h("option", { value: n, text: n, selected: n === u.plan ? true : null })));
      return [h("div", {}, h("strong", { text: u.username }), h("div", { class: "small muted", text: u.email })),
        badge(u.role === "admin" ? "info" : "", u.role), plan, badge(u.is_active ? "success" : "failed", u.is_active ? "active" : "disabled"), fmt.date(u.last_login_at) || "never",
        h("div", { class: "row" },
          h("button", { class: "btn btn-sm", text: u.is_active ? "Disable" : "Enable", disabled: self ? true : null, onclick: (e) => run(e.target, async () => { await api("POST", `/api/admin/users/${u.id}/active`, { active: !u.is_active }); reload(); }) }),
          h("button", { class: "btn btn-sm", text: u.role === "admin" ? "Make user" : "Make admin", disabled: self ? true : null, onclick: async (e) => {
            if (u.role !== "admin" && !(await confirmDialog(`Give ${u.username} full admin access?`, { confirmText: "Make admin" }))) return;
            await run(e.target, async () => { await api("POST", `/api/admin/users/${u.id}/role`, { role: u.role === "admin" ? "user" : "admin" }); reload(); });
          } }),
          h("button", { class: "btn btn-sm", text: "Give tokens", onclick: () => grantDialog(u) }))];
    }), { empty: "No users match." }));
  }
  function grantDialog(u) {
    const amount = h("input", { id: "g-amt", type: "number", min: "1", max: "1000000000", value: "10000" });
    const m = modal(`Give tokens to ${u.username}`, form([field("g-amt", "Tokens", amount)], "Give tokens", async () => {
      await api("POST", `/api/admin/users/${u.id}/grant-tokens`, { tokens: Number(amount.value) }); toast("Tokens added", "success"); m.close();
    }));
  }
  $("user-search").addEventListener("input", (() => { let t; return () => { clearTimeout(t); t = setTimeout(() => users().catch((e) => toast(e.message, "error")), 300); }; })());

  // ------------------------------------------------------------ payments
  async function payments() {
    const r = await api("GET", "/api/admin/payments");
    $("payments").replaceChildren(table(["Date", "User", "Item", "Amount", "Status", ""], r.items.map((p) => [
      fmt.date(p.created_at), p.username, p.plan ? `${p.plan} plan` : `${fmt.num(p.tokens_purchased)} tokens`, fmt.money(p.amount, p.currency), badge(p.status),
      p.status === "success" ? h("button", { class: "btn btn-sm", text: "Refund", onclick: async (e) => {
        if (!(await confirmDialog("Mark this payment refunded and take back what it granted? (Refund the money itself in your payment gateway.)", { danger: true, confirmText: "Refund" }))) return;
        await run(e.target, async () => { await api("POST", `/api/admin/payments/${p.id}/refund`); toast("Marked as refunded", "success"); payments(); });
      } }) : ""]), { numeric: [3], empty: "No payments yet." }));
  }

  // ------------------------------------------------------------- prompts
  async function prompts() {
    const [t, e] = await Promise.all([api("GET", "/api/admin/templates"), api("GET", "/api/admin/examples")]);
    $("templates").replaceChildren(table(["Name", "Version", "Strategy", "Status", ""], t.items.map((x) => [
      x.name, "v" + x.version, x.strategy.replace("_", "-"), x.is_active ? badge("success", "active") : "",
      h("div", { class: "row" },
        h("button", { class: "btn btn-sm", text: "View", onclick: () => modal(`${x.name} v${x.version}`, codeBlock(x.template_text), { wide: true }) }),
        x.is_active ? null : h("button", { class: "btn btn-sm", text: "Activate", onclick: (ev) => run(ev.target, async () => { await api("POST", `/api/admin/templates/${x.id}/activate`); toast("Template activated", "success"); prompts(); }) }))]),
      { empty: "No templates." }));
    $("examples").replaceChildren(table(["Question", "SQL", "Difficulty", "Active"], e.items.map((x) => [
      x.question_text, h("code", { class: "small", text: x.sql_text }), x.difficulty,
      h("input", { type: "checkbox", checked: x.is_active ? true : null, "aria-label": `Use example ${x.id}`, onchange: (ev) => run(ev.target, () => api("POST", `/api/admin/examples/${x.id}/active`, { active: ev.target.checked })) })]),
      { empty: "No few-shot examples." }));
  }
  $("new-template").addEventListener("click", () => {
    const name = h("input", { id: "t-name", type: "text", maxlength: "60", required: true });
    const strat = h("select", { id: "t-strat" }, ["few_shot", "zero_shot", "chain_of_thought"].map((s) => h("option", { value: s, text: s.replace(/_/g, "-") })));
    const text = h("textarea", { id: "t-text", class: "mono", rows: "10", maxlength: "20000" });
    text.value = "You are an expert MySQL analyst. Write ONE read-only SELECT query that answers the question.\nReply as:\nSQL: <query>\nEXPLANATION: <one sentence>\nIf the question is unclear reply: CLARIFY: <question>\n\nSchema:\n{schema}\n{examples}\nQuestion: {question}";
    const m = modal("New prompt template", form([field("t-name", "Name", name, "Saving the same name again creates a new version."), field("t-strat", "Strategy", strat, "Few-shot templates get the {examples} block filled in."), field("t-text", "Template text", text, "Must contain {schema} and {question}. {examples} is optional.")], "Create template", async () => {
      await api("POST", "/api/admin/templates", { name: name.value.trim(), strategy: strat.value, template_text: text.value }); toast("Template created (not active yet)", "success"); m.close(); prompts();
    }), { wide: true });
  });
  $("new-example").addEventListener("click", () => {
    const q = h("input", { id: "e-q", type: "text", maxlength: "1000" }), s = h("textarea", { id: "e-s", class: "mono", rows: "4", maxlength: "10000" });
    const d = h("select", { id: "e-d" }, ["easy", "medium", "hard"].map((x) => h("option", { value: x, text: x })));
    const m = modal("New few-shot example", form([field("e-q", "Question", q), field("e-s", "SQL (SELECT only)", s), field("e-d", "Difficulty", d)], "Add example", async () => {
      await api("POST", "/api/admin/examples", { question: q.value.trim(), sql_text: s.value, difficulty: d.value }); toast("Example added", "success"); m.close(); prompts();
    }), { wide: true });
  });

  // ---------------------------------------------------------------- logs
  async function logs() {
    const which = $("log-kind").value;
    const r = await api("GET", { errors: "/api/admin/errors", blocked: "/api/admin/blocked", audit: "/api/admin/audit" }[which]);
    const rows = {
      errors: [["Time", "User", "Source", "Code", "Message"], r.items.map((x) => [fmt.date(x.created_at), x.username || "–", x.source, x.error_code || "", h("span", { class: "small", text: x.message })])],
      blocked: [["Time", "User", "Reason", "Question", "SQL"], r.items.map((x) => [fmt.date(x.created_at), x.username || "–", badge("blocked", x.reason), x.question_text || "", h("code", { class: "small", text: x.attempted_sql || "" })])],
      audit: [["Time", "Admin", "Action", "Target", "Details"], r.items.map((x) => [fmt.date(x.created_at), x.admin || "–", x.action, x.target_type ? `${x.target_type} #${x.target_id ?? ""}` : "", h("code", { class: "small", text: x.details ? JSON.stringify(x.details) : "" })])],
    }[which];
    $("logs").replaceChildren(table(rows[0], rows[1], { empty: "Nothing logged." }));
  }
  $("log-kind").addEventListener("change", () => logs().catch((e) => toast(e.message, "error")));

  tab("overview");
})();

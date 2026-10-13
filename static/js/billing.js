"use strict";
(async () => {
  const { api, h, run, toast, fmt, modal, table, badge } = App;
  const ctx = await App.init();
  const $ = (id) => document.getElementById(id);
  const cur = ctx.meta.currency || "INR";

  // ---------------------------------------------------------------- tabs
  const panes = { plans: $("pane-plans"), history: $("pane-history") };
  function tab(which) {
    document.querySelectorAll(".tab").forEach((t) => { const on = t.dataset.tab === which; t.classList.toggle("active", on); t.setAttribute("aria-selected", String(on)); });
    for (const [k, p] of Object.entries(panes)) p.classList.toggle("hidden", k !== which);
    if (which === "history") loadHistory();
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => tab(t.dataset.tab)));

  // --------------------------------------------------------------- plans
  const lim = (n, unit) => (n == null ? `Unlimited ${unit}` : `${fmt.num(n)} ${unit} per day`);

  async function loadPlans() {
    const r = await api("GET", "/api/billing/plans");
    if (r.demo) $("demo-note").classList.remove("hidden");
    $("plans").replaceChildren(...r.plans.map((p) => {
      const current = p.name === r.current_plan, price = Number(p.price_monthly);
      return h("div", { class: "card plan-card" + (current ? " current" : "") },
        h("div", { class: "row between" }, h("h3", { text: p.name.charAt(0).toUpperCase() + p.name.slice(1) }), current ? h("span", { class: "badge plan", text: "Current" }) : null),
        h("div", { class: "price" }, price ? fmt.money(price, cur) : "Free", price ? h("small", { text: " / month" }) : null),
        h("ul", { class: "feature-list" },
          h("li", { text: lim(p.daily_query_limit, "queries") }), h("li", { text: lim(p.daily_token_limit, "tokens") }),
          p.can_use_charts ? h("li", { text: "Charts (bar, line, pie)" }) : null, p.can_export_csv ? h("li", { text: "CSV export" }) : null),
        current ? h("button", { class: "btn", disabled: true, text: "Your plan" })
          : price > 0 ? h("button", { class: "btn btn-primary", text: `Upgrade for ${fmt.money(price, cur)}`, onclick: (e) => checkout(e.target, { plan: p.name }, `${p.name} plan`) })
            : h("span", { class: "small muted", text: "Included with every account" }));
    }));
    $("packs").replaceChildren(...r.packs.map((k) => h("div", { class: "card" },
      h("h3", { text: `${fmt.num(k.tokens)} tokens` }), h("div", { class: "price" }, fmt.money(k.price, cur)),
      h("p", { class: "small muted", text: "Used after your daily allowance runs out. Never expires." }),
      h("button", { class: "btn btn-primary", text: "Buy", onclick: (e) => checkout(e.target, { pack: k.key }, `${fmt.num(k.tokens)} tokens`) }))));
  }

  // ------------------------------------------------------------ checkout
  async function confirmOnServer(order, paymentId, signature) {
    await api("POST", "/api/billing/confirm", { gateway_order_id: order.gateway_order_id, gateway_payment_id: paymentId, signature });
    await ctx.refreshMe();
    toast("Payment successful. Thank you!", "success");
    await loadPlans(); loadHistory();
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (document.querySelector(`script[src="${src}"]`)) return resolve();
      const s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = () => reject(new Error("Could not load the payment form."));
      document.head.append(s);
    });
  }

  async function checkout(btn, body, label) {
    await run(btn, async () => {
      const order = await api("POST", "/api/billing/order", body);
      if (order.gateway === "demo") {
        const pay = h("button", { class: "btn btn-primary", text: `Pay ${fmt.money(order.amount, cur)} (demo)` });
        const m = modal("Demo checkout", h("div", { class: "stack" },
          h("div", { class: "banner warn", text: "This is a simulated payment. No money is charged and no card is needed." }),
          h("div", { class: "row between" }, h("span", { text: label }), h("strong", { text: fmt.money(order.amount, cur) })),
          h("div", { class: "row" }, pay, h("button", { class: "btn", text: "Cancel", onclick: () => m.close() }))));
        pay.addEventListener("click", () => run(pay, async () => { await confirmOnServer(order, "demo_pay_" + Math.random().toString(36).slice(2, 12), "demo"); m.close(); }));
        return;
      }
      // Real Razorpay checkout (needs RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in .env)
      await loadScript("https://checkout.razorpay.com/v1/checkout.js");
      const rz = new window.Razorpay({
        key: order.razorpay_key_id, amount: Math.round(order.amount * 100), currency: order.currency, name: ctx.meta.app_name, description: label,
        order_id: order.gateway_order_id, prefill: { email: ctx.user.email },
        handler: (resp) => run(null, () => confirmOnServer(order, resp.razorpay_payment_id, resp.razorpay_signature)),
      });
      rz.on("payment.failed", () => toast("Payment failed. You were not charged.", "error"));
      rz.open();
    });
  }

  // ------------------------------------------------------------- history
  async function showInvoice(id) {
    const inv = await api("GET", `/api/billing/invoice/${id}`);
    const c = inv.currency;
    const content = h("div", { class: "invoice stack" },
      h("div", { class: "row between" }, h("strong", { text: inv.invoice_number }), h("span", { class: "muted", text: fmt.date(inv.issued_at) })),
      h("div", {}, h("div", { text: inv.username }), h("div", { class: "muted", text: inv.email })),
      table(["Item", "Amount"], [
        [inv.plan ? `${inv.plan} plan (1 month)` : `${fmt.num(inv.tokens_purchased)} tokens`, fmt.money(inv.subtotal, c)],
        ["Tax", fmt.money(inv.tax_amount, c)], [h("strong", { text: "Total" }), h("strong", { text: fmt.money(inv.total, c) })]]),
      h("div", { class: "row no-print" }, h("button", { class: "btn btn-primary", text: "Print / save as PDF", onclick: () => window.print() })));
    modal("Invoice", content);
  }

  async function loadHistory() {
    try {
      const r = await api("GET", "/api/billing/payments");
      $("payments").replaceChildren(table(["Date", "Item", "Amount", "Status", ""],
        r.items.map((p) => [fmt.date(p.created_at), p.plan ? `${p.plan} plan` : `${fmt.num(p.tokens_purchased)} tokens`, fmt.money(p.amount, p.currency), badge(p.status),
          p.invoice_number ? h("button", { class: "btn btn-sm", text: "Invoice", onclick: (e) => run(e.target, () => showInvoice(p.id)) }) : ""]),
        { numeric: [2], empty: "No payments yet." }));
    } catch (e) { toast(e.message, "error"); }
  }

  try { await loadPlans(); } catch (e) { toast(e.message, "error"); }
})();

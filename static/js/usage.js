"use strict";
(async () => {
  const { api, h, toast, fmt, statCard, barChart, lineChart, table } = App;
  const ctx = await App.init();
  const $ = (id) => document.getElementById(id);
  const left = (limit, used) => (limit == null ? "Unlimited" : `${fmt.num(Math.max(limit - used, 0))} left`);

  async function load() {
    const days = Number($("days").value);
    try {
      const r = await api("GET", `/api/usage?days=${days}`);
      const t = r.today;
      $("stats").replaceChildren(
        statCard("Plan", t.plan.charAt(0).toUpperCase() + t.plan.slice(1), t.plan === "free" ? "Upgrade for higher limits" : "Thanks for subscribing"),
        statCard("Queries today", t.queries_limit == null ? fmt.num(t.queries_used) : `${fmt.num(t.queries_used)} / ${fmt.num(t.queries_limit)}`, left(t.queries_limit, t.queries_used)),
        statCard("Tokens today", t.tokens_limit == null ? fmt.num(t.tokens_used) : `${fmt.num(t.tokens_used)} / ${fmt.num(t.tokens_limit)}`, left(t.tokens_limit, t.tokens_used)),
        statCard("Purchased tokens", fmt.num(t.token_balance), "Used after the daily allowance runs out"));
      ctx.setUsage(t);
      const items = r.daily.map((d) => ({ label: d.day.slice(5), value: d.tokens }));
      lineChart($("chart-tokens"), items, { title: "Tokens per day", empty: "No usage in this period yet." });
      barChart($("chart-queries"), r.daily.map((d) => ({ label: d.day.slice(5), value: d.queries })), { title: "Queries per day", empty: "No queries in this period yet." });
      $("table").replaceChildren(table(["Date", "Queries", "Failed", "Tokens"],
        [...r.daily].reverse().filter((d) => d.queries || d.tokens).map((d) => [d.day, fmt.num(d.queries), fmt.num(d.errors), fmt.num(d.tokens)]),
        { numeric: [1, 2, 3], empty: "No activity in this period." }));
    } catch (e) { toast(e.message, "error"); }
  }
  $("days").addEventListener("change", load);
  load();
})();

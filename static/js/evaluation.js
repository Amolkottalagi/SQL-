"use strict";
(async () => {
  const { api, h, run, toast, fmt, modal, confirmDialog, table, badge, statCard, barChart, codeBlock } = App;
  const ctx = await App.init();
  const $ = (id) => document.getElementById(id);
  let currentRun = null;

  if (ctx.meta.demo_ai) $("demo-note").classList.remove("hidden");

  const field = (id, label, control, hint) => h("div", { class: "field" }, h("label", { for: id, text: label }), control, hint ? h("div", { class: "hint", text: hint }) : null);

  // ----------------------------------------------------------------- runs
  async function loadRuns() {
    const r = await api("GET", "/api/admin/eval/runs");
    barChart($("acc-chart"), [...r.items].reverse().map((x) => ({ label: `#${x.id} ${x.strategy.replace("_", "-")}`, value: Number(x.accuracy_pct) || 0 })),
      { title: "Accuracy by run (%)", format: (v) => v.toFixed(0) + "%", empty: "Run an experiment to see accuracy here." });
    $("runs").replaceChildren(table(["Run", "Strategy", "Model", "Correct", "Accuracy", "When", ""], r.items.map((x) => [
      "#" + x.id, x.strategy.replace("_", "-"), x.model_name, `${x.correct} / ${x.total}`, `${Number(x.accuracy_pct ?? 0).toFixed(1)}%`, fmt.date(x.started_at),
      h("button", { class: "btn btn-sm", text: currentRun === x.id ? "Showing" : "Details", onclick: () => showRun(x.id) })]),
      { numeric: [3, 4], empty: "No experiments yet. Choose a strategy above and run one." }));
    return r.items;
  }

  async function showRun(id) {
    currentRun = id;
    const r = await api("GET", `/api/admin/eval/runs/${id}`);
    const run_ = r.run;
    $("detail").classList.remove("hidden");
    $("detail-title").textContent = `Run #${id}: ${run_.strategy.replace("_", "-")}`;
    $("detail-stats").replaceChildren(
      statCard("Accuracy", `${Number(run_.accuracy_pct ?? 0).toFixed(1)}%`, `${run_.correct} of ${run_.total} correct`),
      statCard("Model", run_.model_name), statCard("Failures", String(run_.total - run_.correct), r.failures.map((f) => `${f.failure_type.replace("_", " ")}: ${f.n}`).join(" · ") || "none"));
    barChart($("diff-chart"), r.by_difficulty.map((d) => ({ label: d.difficulty, value: Number(d.accuracy_pct) })), { title: "Accuracy by difficulty (%)", format: (v) => v.toFixed(0) + "%" });
    $("details").replaceChildren(table(["", "Question", "Difficulty", "Failure", "Generated SQL", "Expected SQL"], r.details.map((d) => [
      d.is_correct ? badge("success", "✓") : badge("failed", "✗"), d.question_text, d.difficulty, d.failure_type ? d.failure_type.replace("_", " ") : "",
      h("code", { class: "small", text: d.generated_sql || "(none)" }), h("code", { class: "small", text: d.expected_sql })]), { empty: "No results." }));
    loadRuns();
    $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("run-btn").addEventListener("click", async (e) => {
    const strategy = $("strategy").value;
    $("run-status").replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), " Running every test question…");
    await run(e.target, async () => {
      try {
        const r = await api("POST", "/api/admin/eval/run", { strategy, few_shot_k: Number($("k").value) });
        toast("Experiment finished", "success");
        await showRun(r.run_id);
      } finally { $("run-status").replaceChildren(); }
    });
  });

  // ------------------------------------------------------- test questions
  async function loadQuestions() {
    const r = await api("GET", "/api/admin/eval/questions");
    $("questions").replaceChildren(table(["Question", "Expected SQL", "Category", "Difficulty", ""], r.items.map((q) => [
      q.question_text, h("code", { class: "small", text: q.expected_sql }), q.category || "", q.difficulty,
      h("button", { class: "btn btn-sm btn-ghost", text: "Delete", onclick: async (e) => {
        if (!(await confirmDialog("Delete this test question?", { danger: true, confirmText: "Delete" }))) return;
        await run(e.target, async () => { await api("DELETE", `/api/admin/eval/questions/${q.id}`); toast("Deleted", "success"); loadQuestions(); });
      } })]), { empty: "No test questions yet." }));
    $("q-count").textContent = `${r.items.length} test question${r.items.length === 1 ? "" : "s"}`;
  }

  $("add-q").addEventListener("click", () => {
    const q = h("input", { id: "q-q", type: "text", maxlength: "1000" }), s = h("textarea", { id: "q-s", class: "mono", rows: "4", maxlength: "10000" });
    const c = h("input", { id: "q-c", type: "text", maxlength: "40", placeholder: "e.g. join, aggregate" });
    const d = h("select", { id: "q-d" }, ["easy", "medium", "hard"].map((x) => h("option", { value: x, text: x })));
    const f = h("form", { class: "stack", novalidate: true }, field("q-q", "Question", q), field("q-s", "Correct SQL", s, "It must run successfully. Results are compared against this query's output."), field("q-c", "Category (optional)", c), field("q-d", "Difficulty", d),
      h("button", { class: "btn btn-primary", type: "submit", text: "Add test question" }));
    const m = modal("New test question", f, { wide: true });
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      await run(f.querySelector("button"), async () => {
        await api("POST", "/api/admin/eval/questions", { question: q.value.trim(), expected_sql: s.value, category: c.value.trim() || null, difficulty: d.value });
        toast("Test question added", "success"); m.close(); loadQuestions();
      });
    });
  });

  try { const runs = await loadRuns(); await loadQuestions(); } catch (e) { toast(e.message, "error"); }
})();

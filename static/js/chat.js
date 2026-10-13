"use strict";
(async () => {
  const { api, h, run, toast, fmt, resultView, codeBlock, badge } = App;
  const ctx = await App.init();

  const EXAMPLES = [
    "How many customers do we have?",
    "Total revenue per product",
    "Which customers live in Pune?",
    "Most expensive product",
    "Orders per month",
    "Best selling products",
  ];

  const $ = (id) => document.getElementById(id);
  const messages = $("messages"), form = $("ask-form"), input = $("question"), askBtn = $("ask-btn");
  let sessionId = null;
  let busy = false;

  // ------------------------------------------------------------ helpers
  const viewCtx = () => ({
    chartType: ctx.settings.default_chart_type,
    perPage: ctx.settings.rows_per_page || 50,
    canChart: ctx.me.usage.can_use_charts,
  });

  function scrollToEnd() { messages.lastElementChild && messages.lastElementChild.scrollIntoView({ block: "end", behavior: "auto" }); }

  function questionBubble(text) { return h("div", { class: "q-bubble", text }); }

  function feedbackControls(a) {
    const up = h("button", { class: "btn btn-sm", "aria-label": "Good answer", "aria-pressed": "false", text: "👍" });
    const down = h("button", { class: "btn btn-sm", "aria-label": "Bad answer", "aria-pressed": "false", text: "👎" });
    const pop = h("div", { class: "fb-pop hidden" });
    const mark = (r) => {
      up.classList.toggle("btn-primary", r === 1); up.setAttribute("aria-pressed", String(r === 1));
      down.classList.toggle("btn-primary", r === -1); down.setAttribute("aria-pressed", String(r === -1));
    };
    mark(a.my_rating);
    up.addEventListener("click", () => run(up, async () => {
      await api("POST", "/api/chat/feedback", { response_id: a.response_id, rating: 1 });
      mark(1); pop.classList.add("hidden"); toast("Thanks for the feedback", "success");
    }));
    down.addEventListener("click", () => {
      pop.classList.toggle("hidden");
      if (pop.childElementCount) return;
      const reason = h("select", { id: "fb-reason-" + a.response_id, "aria-label": "What was wrong?" },
        [["", "What went wrong? (optional)"], ["wrong_result", "Wrong result"], ["wrong_sql", "Wrong SQL"], ["slow", "Too slow"], ["confusing", "Confusing"], ["other", "Something else"]]
          .map(([v, t]) => h("option", { value: v, text: t })));
      const comment = h("textarea", { maxlength: "2000", rows: "2", placeholder: "Comment (optional)", "aria-label": "Comment" });
      const send = h("button", { class: "btn btn-primary btn-sm", text: "Send feedback", onclick: () => run(send, async () => {
        await api("POST", "/api/chat/feedback", { response_id: a.response_id, rating: -1, reason: reason.value || null, comment: comment.value.trim() || null });
        mark(-1); pop.classList.add("hidden"); toast("Thanks, feedback saved", "success");
      }) });
      pop.append(h("div", { class: "stack" }, reason, comment, send));
    });
    return { buttons: [up, down], pop };
  }

  /** Render one answer. `a` = {prompt_id, response_id, status, sql, explanation, clarification, error, rows|null, columns, row_count, truncated, my_rating, attempts} */
  function answerCard(a) {
    const card = h("div", { class: "card a-card" });
    const results = h("div", { class: "mt" });

    if (a.status === "clarification") {
      card.append(h("div", { class: "banner" }, h("strong", { text: "I need a bit more detail: " }), a.clarification || a.explanation || "Could you rephrase the question?"));
    } else if (a.status === "blocked") {
      card.append(h("div", { class: "form-error" }, h("strong", { text: "Blocked for safety. " }), a.error || "Only read-only queries are allowed."));
    } else if (a.status === "error") {
      card.append(h("div", { class: "form-error" }, h("strong", { text: "That didn't work. " }), a.error || "The query failed.", " Try rephrasing the question."));
    }

    if (a.explanation && a.status === "success") card.append(h("p", { class: "muted", text: a.explanation }));
    if (a.sql) card.append(a.status === "success" ? codeBlock(a.sql) : h("details", {}, h("summary", { text: "Show the SQL that was generated" }), codeBlock(a.sql)));

    if (a.status === "success") {
      if (a.rows) {
        results.append(resultView(a, viewCtx()));
        const note = [`${a.row_count} row${a.row_count === 1 ? "" : "s"}`];
        if (a.truncated) note.push("showing the first rows only, refine your question to narrow it down");
        if (a.attempts > 1) note.push("fixed automatically after an error");
        results.append(h("div", { class: "small muted mt", text: note.join(" · ") }));
      } else {
        const show = h("button", { class: "btn btn-sm", text: "Show results", onclick: () => run(show, async () => {
          const r = await api("POST", `/api/history/prompt/${a.prompt_id}/rerun`);
          results.replaceChildren(resultView(r, viewCtx()));
        }) });
        results.append(h("div", { class: "row" }, show, h("span", { class: "small muted", text: `${fmt.num(a.row_count)} rows. Re-running is free (no AI call).` })));
      }
    }
    card.append(results);

    // actions
    const actions = h("div", { class: "actions" });
    if (a.response_id) {
      const fb = feedbackControls(a);
      actions.append(...fb.buttons);
      if (a.status === "success") {
        actions.append(
          h("button", { class: "btn btn-sm", text: a.is_saved ? "★ Saved" : "☆ Save", disabled: a.is_saved ? true : null, onclick: (e) => run(e.target, async () => {
            await api("POST", `/api/history/prompt/${a.prompt_id}/save`, {});
            e.target.textContent = "★ Saved"; e.target.disabled = true; toast("Saved to your saved queries", "success");
          }) }),
          h("button", { class: "btn btn-sm", text: "Export CSV", onclick: () => {
            if (!ctx.me.usage.can_export_csv) return toast("CSV export is available on the Pro plan.", "error");
            location.href = `/api/chat/export/${a.response_id}.csv`;
          } }));
      }
      card.append(actions, fb.pop);
    }
    return card;
  }

  function fromHistory(it) {
    return { prompt_id: it.prompt_id, response_id: it.response_id, status: it.status, sql: it.generated_sql,
      explanation: it.explanation, clarification: it.status === "clarification" ? it.explanation : null,
      error: it.error_message, rows: null, columns: [], row_count: it.row_count || 0, my_rating: it.my_rating, is_saved: !!it.is_saved, attempts: it.attempt_no };
  }

  // ------------------------------------------------------------ sessions
  async function loadSessions() {
    const box = $("sessions");
    try {
      const r = await api("GET", "/api/history/sessions");
      box.replaceChildren(...(r.items.length ? r.items.map((s) => {
        const a = h("a", { href: `/chat?session=${s.id}`, class: s.id === sessionId ? "active" : "", title: s.title, text: s.title });
        a.addEventListener("click", (e) => { e.preventDefault(); openSession(s.id); });
        return a;
      }) : [h("div", { class: "small muted", text: "No chats yet." })]));
    } catch (e) { box.replaceChildren(h("div", { class: "small muted", text: e.message })); }
  }

  const setChips = (on) => $("chips").classList.toggle("hidden", !on);

  function welcome() {
    setChips(true);
    messages.replaceChildren(h("div", { class: "card" },
      h("h2", { text: "Ask your data a question" }),
      h("p", { class: "muted", text: "Type a question in plain English. You'll see the SQL that was generated and the result, and you can chart it, save it or export it." })));
  }

  async function openSession(id) {
    sessionId = id;
    history.pushState(null, "", `/chat?session=${id}`);
    messages.replaceChildren(h("div", { class: "empty" }, h("span", { class: "spinner", "aria-label": "Loading" })));
    try {
      const r = await api("GET", `/api/history/session/${id}`);
      messages.replaceChildren(...r.items.flatMap((it) => [questionBubble(it.question_text), answerCard(fromHistory(it))]));
      setChips(!r.items.length);
      scrollToEnd();
    } catch (e) { sessionId = null; welcome(); toast(e.message, "error"); }
    loadSessions();
  }

  function newChat() {
    sessionId = null; history.pushState(null, "", "/chat"); welcome(); loadSessions(); input.focus();
  }

  // ----------------------------------------------------------------- ask
  async function ask(question) {
    if (busy) return;
    question = question.trim();
    if (!question) return;
    busy = true; askBtn.disabled = true;
    setChips(false);
    if (messages.querySelector(".card:only-child h2")) messages.replaceChildren();
    messages.append(questionBubble(question));
    const pending = h("div", { class: "card a-card" }, h("span", { class: "spinner", "aria-hidden": "true" }), " Thinking…");
    messages.append(pending); scrollToEnd();
    try {
      const r = await api("POST", "/api/chat/ask", { question, chat_session_id: sessionId });
      sessionId = r.chat_session_id;
      history.replaceState(null, "", `/chat?session=${sessionId}`);
      pending.replaceWith(answerCard({ ...r, rows: r.rows, my_rating: null }));
      ctx.setUsage(r.usage);
      input.value = ""; updateCount();
      loadSessions();
    } catch (e) {
      pending.replaceWith(h("div", { class: "card a-card" }, h("div", { class: "form-error", role: "alert", text: e.message }),
        e.status === 429 ? h("a", { class: "btn btn-primary btn-sm", href: "/billing", text: "See plans" }) : null));
      if (e.status === 429) ctx.refreshMe().catch(() => {});
    } finally {
      busy = false; askBtn.disabled = false; scrollToEnd(); input.focus();
    }
  }

  const updateCount = () => { $("charcount").textContent = `${input.value.length}/500`; };

  // ---------------------------------------------------------------- wiring
  form.addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); } });
  input.addEventListener("input", updateCount);
  $("new-chat").addEventListener("click", newChat);
  $("chips").replaceChildren(...EXAMPLES.map((q) => h("button", { class: "chip", type: "button", text: q, onclick: () => ask(q) })));
  window.addEventListener("popstate", () => {
    const s = new URLSearchParams(location.search).get("session");
    if (s) openSession(Number(s)); else { sessionId = null; welcome(); loadSessions(); }
  });

  if (ctx.meta.demo_ai) {
    const b = $("demo-banner");
    b.classList.remove("hidden");
    b.textContent = "Demo mode: answers come from a small built-in rule set about the sample shop database. Set GEMINI_API_KEY to use a real AI model.";
    b.classList.add("small");
  }

  const params = new URLSearchParams(location.search);
  if (params.get("session")) await openSession(Number(params.get("session")));
  else { welcome(); loadSessions(); }
  if (params.get("q")) { input.value = params.get("q").slice(0, 500); updateCount(); }
  input.focus();
})();

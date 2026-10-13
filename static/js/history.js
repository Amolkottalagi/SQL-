"use strict";
(async () => {
  const { api, h, run, toast, fmt, resultView, codeBlock, badge, modal, confirmDialog } = App;
  const ctx = await App.init();
  const $ = (id) => document.getElementById(id);
  const list = $("list"), more = $("more");
  const PAGE = 20;
  let offset = 0, timer = null;

  const viewCtx = () => ({ chartType: ctx.settings.default_chart_type, perPage: ctx.settings.rows_per_page || 50, canChart: ctx.me.usage.can_use_charts });

  function itemCard(it) {
    const card = h("div", { class: "item-card" });
    const actions = h("div", { class: "row" });
    card.append(
      h("div", { class: "row between" },
        h("strong", { class: "grow", text: it.question_text }),
        badge(it.status || "pending", it.status || "no answer"),
        h("span", { class: "small muted nowrap", text: fmt.date(it.created_at) })),
      it.error_message && it.status !== "success" ? h("div", { class: "small muted mt", text: it.error_message }) : null,
      it.generated_sql ? h("details", {}, h("summary", { text: "SQL" }), codeBlock(it.generated_sql)) : null,
      h("div", { class: "mt", id: "res-" + it.prompt_id }),
      actions);
    if (it.status === "success") {
      actions.append(
        h("button", { class: "btn btn-sm", text: "Re-run (free)", onclick: (e) => run(e.target, async () => {
          const r = await api("POST", `/api/history/prompt/${it.prompt_id}/rerun`);
          const m = modal(it.question_text, h("div", {}, codeBlock(r.sql), h("div", { class: "mt" }, resultView(r, viewCtx())),
            h("div", { class: "small muted mt", text: `${r.row_count} row${r.row_count === 1 ? "" : "s"}${r.truncated ? " (truncated)" : ""} · ${r.latency_ms} ms` })), { wide: true });
        }) }),
        h("button", { class: "btn btn-sm", text: it.is_saved ? "★ Saved" : "☆ Save", disabled: it.is_saved ? true : null, onclick: (e) => run(e.target, async () => {
          await api("POST", `/api/history/prompt/${it.prompt_id}/save`, {});
          e.target.textContent = "★ Saved"; e.target.disabled = true; toast("Saved", "success");
        }) }));
    }
    actions.append(
      h("a", { class: "btn btn-sm", href: `/chat?session=${it.chat_session_id}`, text: "Open chat" }),
      h("a", { class: "btn btn-sm", href: `/chat?q=${encodeURIComponent(it.question_text)}`, text: "Ask again" }),
      h("button", { class: "btn btn-sm btn-ghost", text: "Delete", onclick: async (e) => {
        if (!(await confirmDialog("Delete this question from your history?", { danger: true, confirmText: "Delete" }))) return;
        await run(e.target, async () => { await api("DELETE", `/api/history/prompt/${it.prompt_id}`); card.remove(); toast("Deleted", "success"); if (!list.children.length) load(true); });
      } }));
    return card;
  }

  async function load(reset) {
    if (reset) { offset = 0; list.replaceChildren(); }
    const q = new URLSearchParams({ limit: PAGE, offset });
    if ($("search").value.trim()) q.set("q", $("search").value.trim());
    if ($("status").value) q.set("status", $("status").value);
    try {
      const r = await api("GET", "/api/history?" + q);
      if (reset && !r.items.length) list.append(h("div", { class: "empty", text: $("search").value || $("status").value ? "No questions match your filters." : "Nothing here yet. Ask your first question on the Chat page." }));
      r.items.forEach((it) => list.append(itemCard(it)));
      offset += r.items.length;
      more.classList.toggle("hidden", r.items.length < PAGE);
    } catch (e) { toast(e.message, "error"); }
  }

  $("search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => load(true), 300); });
  $("status").addEventListener("change", () => load(true));
  more.addEventListener("click", () => run(more, () => load(false)));
  $("clear-all").addEventListener("click", async (e) => {
    if (!(await confirmDialog("Delete ALL your chats and questions? Saved queries are kept. This cannot be undone.", { danger: true, confirmText: "Delete everything" }))) return;
    await run(e.target, async () => { const r = await api("DELETE", "/api/history"); toast(`Deleted ${r.deleted_chats} chat${r.deleted_chats === 1 ? "" : "s"}`, "success"); load(true); });
  });
  load(true);
})();

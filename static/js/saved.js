"use strict";
(async () => {
  const { api, h, run, toast, fmt, resultView, codeBlock, modal, confirmDialog } = App;
  const ctx = await App.init();
  const list = document.getElementById("list");

  function card(s) {
    const el = h("div", { class: "item-card" },
      h("div", { class: "row between" }, h("strong", { text: s.title }), h("span", { class: "small muted", text: fmt.date(s.created_at) })),
      h("div", { class: "muted", text: s.question_text }),
      s.sql_text ? h("details", {}, h("summary", { text: "SQL" }), codeBlock(s.sql_text)) : null,
      h("div", { class: "row mt" },
        s.sql_text ? h("button", { class: "btn btn-sm", text: "Run (free)", onclick: (e) => run(e.target, async () => {
          const r = await api("POST", `/api/saved/${s.id}/rerun`);
          modal(s.title, h("div", {}, codeBlock(r.sql), h("div", { class: "mt" }, resultView(r, { chartType: ctx.settings.default_chart_type, perPage: ctx.settings.rows_per_page || 50, canChart: ctx.me.usage.can_use_charts }))), { wide: true });
        }) }) : null,
        h("a", { class: "btn btn-sm", href: `/chat?q=${encodeURIComponent(s.question_text)}`, text: "Ask again" }),
        h("button", { class: "btn btn-sm btn-ghost", text: "Remove", onclick: async (e) => {
          if (!(await confirmDialog("Remove this saved query?", { danger: true, confirmText: "Remove" }))) return;
          await run(e.target, async () => { await api("DELETE", `/api/saved/${s.id}`); el.remove(); toast("Removed", "success"); if (!list.children.length) load(); });
        } })));
    return el;
  }

  async function load() {
    try {
      const r = await api("GET", "/api/saved");
      list.replaceChildren(...(r.items.length ? r.items.map(card) : [h("div", { class: "empty", text: "No saved queries yet. Use ☆ Save on any successful answer in Chat." })]));
    } catch (e) { toast(e.message, "error"); }
  }
  load();
})();

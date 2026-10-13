"use strict";
(() => {
  const { api, h, run } = App;
  const root = document.getElementById("auth-root");
  const msg = h("div", { class: "hidden", role: "status" });
  const form = h("form", { novalidate: true },
    h("p", { class: "muted", text: "Enter your account email and we'll send you a link to choose a new password." }),
    h("div", { class: "field" }, h("label", { for: "fp-email", text: "Email" }),
      h("input", { id: "fp-email", type: "email", autocomplete: "email", required: true, maxlength: "120" })),
    h("button", { class: "btn btn-primary btn-block", type: "submit", text: "Send reset link" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = form.querySelector("#fp-email").value.trim();
    if (!email) return;
    await run(form.querySelector("button"), async () => {
      const r = await api("POST", "/api/auth/forgot", { email });
      msg.className = "form-ok"; msg.replaceChildren(r.message);
      if (r.dev_reset_link) msg.append(h("div", { class: "mt small" }, "Development mode link: ", h("a", { href: r.dev_reset_link, text: "choose a new password" })));
      form.classList.add("hidden");
    });
  });
  root.append(
    h("div", { class: "brand" }, h("span", { class: "brand-mark", "aria-hidden": "true", text: "S" }), "SQL LLM"),
    h("div", { class: "card mt" }, h("h2", { text: "Forgot your password?" }), msg, form,
      h("p", { class: "mt small" }, h("a", { href: "/login", text: "← Back to sign in" }))));
})();

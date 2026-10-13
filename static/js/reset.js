"use strict";
(() => {
  const { api, h, run } = App;
  const root = document.getElementById("auth-root");
  const token = new URLSearchParams(location.search).get("token") || "";
  const err = h("div", { class: "form-error hidden", role: "alert" });
  const form = h("form", { novalidate: true },
    h("div", { class: "field" }, h("label", { for: "rp-pass", text: "New password" }),
      h("input", { id: "rp-pass", type: "password", autocomplete: "new-password", required: true, maxlength: "72" }),
      h("div", { class: "hint", text: "At least 8 characters." })),
    h("div", { class: "field" }, h("label", { for: "rp-pass2", text: "Confirm new password" }),
      h("input", { id: "rp-pass2", type: "password", autocomplete: "new-password", required: true, maxlength: "72" })),
    h("button", { class: "btn btn-primary btn-block", type: "submit", text: "Set new password" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault(); err.classList.add("hidden");
    const p1 = form.querySelector("#rp-pass").value, p2 = form.querySelector("#rp-pass2").value;
    const fail = (m) => { err.textContent = m; err.classList.remove("hidden"); };
    if (p1 !== p2) return fail("The two passwords don't match.");
    await run(form.querySelector("button"), async () => {
      try {
        await api("POST", "/api/auth/reset", { token, new_password: p1 });
        form.classList.add("hidden");
        root.querySelector(".card").append(h("div", { class: "form-ok", text: "Your password was changed. All other devices were signed out." }), h("a", { class: "btn btn-primary", href: "/login", text: "Sign in" }));
      } catch (ex) { fail(ex.message); }
    });
  });
  root.append(
    h("div", { class: "brand" }, h("span", { class: "brand-mark", "aria-hidden": "true", text: "S" }), "SQL LLM"),
    h("div", { class: "card mt" }, h("h2", { text: "Choose a new password" }),
      token ? [err, form] : h("div", { class: "form-error", text: "This reset link is incomplete. Request a new one." }),
      h("p", { class: "mt small" }, h("a", { href: "/forgot", text: "Request a new link" }), " · ", h("a", { href: "/login", text: "Sign in" }))));
})();

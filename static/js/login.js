"use strict";
(() => {
  const { api, h, run } = App;
  const root = document.getElementById("auth-root");

  function safeNext() {
    const n = new URLSearchParams(location.search).get("next");
    return n && /^\/[A-Za-z0-9_\-\/?=&.]*$/.test(n) && !n.startsWith("//") ? n : "/chat";
  }

  const err = h("div", { class: "form-error hidden", role: "alert" });
  const showErr = (m) => { err.textContent = m; err.classList.toggle("hidden", !m); };

  // ---- sign in
  const loginForm = h("form", { novalidate: true },
    h("div", { class: "field" }, h("label", { for: "li-user", text: "Username or email" }),
      h("input", { id: "li-user", type: "text", autocomplete: "username", required: true, maxlength: "120" })),
    h("div", { class: "field" }, h("label", { for: "li-pass", text: "Password" }),
      h("input", { id: "li-pass", type: "password", autocomplete: "current-password", required: true, maxlength: "200" })),
    h("button", { class: "btn btn-primary", type: "submit", text: "Sign in" }),
    h("div", { class: "mt small" }, h("a", { href: "/forgot", text: "Forgot your password?" })));
  loginForm.querySelector("button").classList.add("btn-block");
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault(); showErr("");
    const user = loginForm.querySelector("#li-user").value.trim(), pw = loginForm.querySelector("#li-pass").value;
    if (!user || !pw) return showErr("Enter your username and password.");
    await run(loginForm.querySelector("button"), async () => {
      try { await api("POST", "/api/auth/login", { username: user, password: pw }); location.href = safeNext(); }
      catch (ex) { showErr(ex.message); }
    });
  });

  // ---- sign up
  const signupForm = h("form", { novalidate: true },
    h("div", { class: "field" }, h("label", { for: "su-user", text: "Username" }),
      h("input", { id: "su-user", type: "text", autocomplete: "username", required: true, maxlength: "30" }),
      h("div", { class: "hint", text: "3–30 letters, digits or underscores." })),
    h("div", { class: "field" }, h("label", { for: "su-email", text: "Email" }),
      h("input", { id: "su-email", type: "email", autocomplete: "email", required: true, maxlength: "120" })),
    h("div", { class: "field" }, h("label", { for: "su-pass", text: "Password" }),
      h("input", { id: "su-pass", type: "password", autocomplete: "new-password", required: true, maxlength: "72" }),
      h("div", { class: "hint", text: "At least 8 characters." })),
    h("div", { class: "field" }, h("label", { for: "su-pass2", text: "Confirm password" }),
      h("input", { id: "su-pass2", type: "password", autocomplete: "new-password", required: true, maxlength: "72" })),
    h("div", { class: "field" }, h("label", { class: "check", for: "su-consent" },
      h("input", { id: "su-consent", type: "checkbox" }),
      h("span", { text: "I understand that my questions and the SQL generated for them are stored to provide my history and for research. If I delete my account they are kept anonymously." }))),
    h("button", { class: "btn btn-primary btn-block", type: "submit", text: "Create account" }));
  signupForm.addEventListener("submit", async (e) => {
    e.preventDefault(); showErr("");
    const v = (id) => signupForm.querySelector("#" + id);
    if (v("su-pass").value !== v("su-pass2").value) return showErr("The two passwords don't match.");
    if (!v("su-consent").checked) return showErr("Please accept that your questions are stored to register.");
    await run(signupForm.querySelector("button"), async () => {
      try {
        await api("POST", "/api/auth/signup", { username: v("su-user").value, email: v("su-email").value, password: v("su-pass").value, consent: true });
        location.href = "/chat";
      } catch (ex) { showErr(ex.message); }
    });
  });

  // ---- tabs
  const tabs = {}, panels = { login: loginForm, signup: signupForm };
  function select(which) {
    showErr("");
    for (const k of Object.keys(panels)) {
      tabs[k].classList.toggle("active", k === which); tabs[k].setAttribute("aria-selected", String(k === which));
      panels[k].classList.toggle("hidden", k !== which);
    }
    history.replaceState(null, "", (which === "signup" ? "/signup" : "/login") + location.search);
    document.title = (which === "signup" ? "Create account" : "Sign in") + " · SQL LLM";
  }
  const tablist = h("div", { class: "tabs", role: "tablist" });
  [["login", "Sign in"], ["signup", "Create account"]].forEach(([k, label]) => {
    tabs[k] = h("button", { class: "tab", role: "tab", type: "button", text: label, onclick: () => select(k) });
    tablist.append(tabs[k]);
  });

  root.append(
    h("div", { class: "brand" }, h("span", { class: "brand-mark", "aria-hidden": "true", text: "S" }), "SQL LLM"),
    h("p", { class: "auth-tag", text: "Ask questions in plain English. Get SQL and answers." }),
    h("div", { class: "card" }, tablist, err, loginForm, signupForm),
    h("p", { class: "mt small muted right" }, h("a", { href: "/about", text: "About this project" })));
  select(location.pathname === "/signup" ? "signup" : "login");
  api("GET", "/api/meta").then((m) => { if (m.demo_ai) root.querySelector(".auth-tag").textContent += " (Demo mode: built-in rules, no AI key set.)"; }).catch(() => {});
})();

"use strict";
(async () => {
  const { api, h, run, toast, confirmDialog, setTheme } = App;
  const ctx = await App.init();
  const $ = (id) => document.getElementById(id);

  // ---- preferences
  $("default-chart").value = ctx.settings.default_chart_type;
  $("theme").value = ctx.settings.theme;
  $("rows").value = ctx.settings.rows_per_page;
  $("prefs-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await run($("prefs-save"), async () => {
      ctx.settings = await api("PUT", "/api/settings", { default_chart_type: $("default-chart").value, theme: $("theme").value, rows_per_page: Number($("rows").value) });
      setTheme(ctx.settings.theme);
      toast("Settings saved", "success");
    });
  });

  // ---- password
  const pwErr = $("pw-msg");
  const say = (ok, m) => { pwErr.className = ok ? "form-ok" : "form-error"; pwErr.textContent = m; };
  $("pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("pw-new").value !== $("pw-new2").value) return say(false, "The two new passwords don't match.");
    await run($("pw-save"), async () => {
      try {
        await api("POST", "/api/auth/change-password", { old_password: $("pw-old").value, new_password: $("pw-new").value });
        $("pw-form").reset(); say(true, "Password changed. Your other devices were signed out.");
      } catch (ex) { say(false, ex.message); }
    });
  });

  // ---- account details + delete
  $("acct").textContent = `${ctx.user.username} · ${ctx.user.email}`;
  $("delete-btn").addEventListener("click", async () => {
    const pw = $("del-pw").value;
    if (!pw) return toast("Enter your password to confirm.", "error");
    if (!(await confirmDialog("Delete your account permanently? Your questions will be kept anonymously for research, as you agreed at sign-up.", { danger: true, confirmText: "Delete my account" }))) return;
    await run($("delete-btn"), async () => {
      const r = await api("POST", "/api/auth/delete-account", { password: pw });
      location.href = "/login?deleted=1";
    });
  });
})();

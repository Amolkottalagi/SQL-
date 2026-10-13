/* Runs in <head> before first paint so the page doesn't flash the wrong theme. */
(function () {
  try {
    var t = localStorage.getItem("theme");
    if (t !== "dark" && t !== "light") {
      t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: keep the default */ }
})();

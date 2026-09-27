/* frontend/static/js/app.js
   Sansa dashboard client glue.

   Two responsibilities:
     1. Attach the CSRF header to every HTMX request that mutates state.
     2. Provide a tiny toast helper for HTMX responses.

   Everything else is HTMX's job. No framework, no build step.
*/

(function () {
  "use strict";

  function readCookie(name) {
    var prefix = name + "=";
    var parts = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].indexOf(prefix) === 0) {
        return decodeURIComponent(parts[i].substring(prefix.length));
      }
    }
    return null;
  }

  // The CSRF cookie is set by the backend on login and on refresh. It is
  // deliberately NOT HttpOnly — the double-submit pattern requires JS to
  // read it. Enforcement is server-side in require_csrf.
  var csrfName = window.SANSA_CSRF_COOKIE || "sansa_csrf";
  var csrfHeader = window.SANSA_CSRF_HEADER || "X-CSRF-Token";

  document.body.addEventListener("htmx:configRequest", function (evt) {
    var method = (evt.detail.method || "GET").toUpperCase();
    if (method === "GET" || method === "HEAD" || method === "OPTIONS") {
      return;
    }
    var token = readCookie(csrfName);
    if (token) {
      evt.detail.headers[csrfHeader] = token;
    }
  });

  // Toast helper. Backend fragments can include an out-of-band swap that
  // contains a <template data-toast="..."> — we pop it and render a toast.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var tpl = evt.target.querySelector
      ? evt.target.querySelector("template[data-toast]")
      : null;
    if (!tpl) return;
    showToast(tpl.getAttribute("data-toast"));
    tpl.remove();
  });

  function showToast(message) {
    var host = document.querySelector(".toast-host");
    if (!host) {
      host = document.createElement("div");
      host.className = "toast-host";
      document.body.appendChild(host);
    }
    var el = document.createElement("div");
    el.className = "toast";
    el.textContent = message;
    host.appendChild(el);
    setTimeout(function () {
      el.style.transition = "opacity 200ms ease";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 220);
    }, 2600);
  }

  window.sansaToast = showToast;
})();
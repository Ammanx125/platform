/* frontend/static/js/app.js
   Sansa dashboard client glue.

   Two responsibilities:
     1. Attach the CSRF header to every HTMX request that mutates state.
     2. Provide a tiny toast helper for HTMX responses.

   Everything else is HTMX's job. No framework, no build step.
*/

(function () {
  "use strict";

  var themeToggle = document.querySelector(".theme-toggle");
  if (themeToggle) {
    var isDark = document.documentElement.dataset.theme === "dark";
    themeToggle.setAttribute("aria-checked", String(isDark));
    themeToggle.setAttribute("aria-label", isDark ? "Enable light mode" : "Enable dark mode");

    themeToggle.addEventListener("click", function () {
      isDark = !isDark;
      document.documentElement.dataset.theme = isDark ? "dark" : "light";
      themeToggle.setAttribute("aria-checked", String(isDark));
      themeToggle.setAttribute("aria-label", isDark ? "Enable light mode" : "Enable dark mode");
      try {
        localStorage.setItem("sansa-theme", isDark ? "dark" : "light");
      } catch (_) {}
    });
  }

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

  document.body.addEventListener("click", function (evt) {
    var button = evt.target.closest("[data-demo-ingest]");
    if (!button || button.disabled) return;

    var result = document.querySelector("[data-demo-ingest-result]");
    var token = readCookie(csrfName);
    if (!result) return;
    if (!token) {
      result.textContent = "Request not sent: refresh the page to renew the security token.";
      return;
    }

    button.disabled = true;
    result.textContent = "Requesting the latest files from the registered watcher…";
    fetch(button.getAttribute("data-ingest-url"), {
      method: "POST",
      credentials: "same-origin",
      headers: { [csrfHeader]: token }
    }).then(function (response) {
      return response.text().then(function (text) {
        var body = {};
        try {
          body = text ? JSON.parse(text) : {};
        } catch (error) {
          throw new Error("Server returned an unreadable upload response.");
        }
        if (!response.ok) {
          var detail = body.detail;
          var message = typeof detail === "string"
            ? detail
            : detail && detail.message
              ? detail.message
              : "Upload request failed (HTTP " + response.status + ").";
          throw new Error(message);
        }
        return body;
      });
    }).then(function (body) {
      var count = Number(body.agent_job_count || 0);
      result.textContent = count
        ? count + " upload job(s) queued. Waiting for the watcher to deliver the files."
        : "The server accepted the request, but reported no upload jobs.";
      button.textContent = "Uploads requested";
      startDemoQueuePolling();
    }).catch(function (error) {
      result.textContent = "Upload request failed: " + error.message;
      button.disabled = false;
    });
  });

  function startDemoQueuePolling() {
    var queue = document.querySelector("[data-demo-queue]");
    if (!queue) return;

    var rows = Array.prototype.slice.call(
      document.querySelectorAll("[data-demo-file-row]")
    );
    var progress = queue.querySelector("[data-demo-queue-bar]");
    var label = queue.querySelector("[data-demo-queue-label]");
    var detail = queue.querySelector("[data-demo-queue-detail]");
    var result = document.querySelector("[data-demo-ingest-result]");
    if (!progress || !label || !detail) return;
    queue.hidden = false;

    function poll() {
      fetch(queue.dataset.jobsUrl, { credentials: "same-origin" })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("Queue status request failed (HTTP " + response.status + ").");
          }
          return response.json();
        })
        .then(function (jobs) {
          var latestByPath = Object.create(null);
          jobs.forEach(function (job) {
            if (job.pending_path && !latestByPath[job.pending_path]) {
              latestByPath[job.pending_path] = job;
            }
          });

          var trackedRows = rows.filter(function (row) {
            return Boolean(latestByPath[row.dataset.path]);
          });
          var total = trackedRows.length;
          if (!total) {
            detail.textContent = "No ingestion jobs are visible yet.";
            window.setTimeout(poll, 1200);
            return;
          }
          progress.max = total;
          queue.dataset.fileTotal = String(total);

          var succeeded = 0;
          var failed = 0;
          var active = 0;
          var queued = 0;
          trackedRows.forEach(function (row) {
            var job = latestByPath[row.dataset.path];
            var status = row.querySelector("[data-demo-file-status]");
            if (!job) return;
            row.dataset.jobStatus = job.status;
            if (job.status === "succeeded") {
              succeeded += 1;
              if (status) status.textContent = "Ingested · " + job.rows_staged + " rows";
            } else if (job.status === "failed") {
              failed += 1;
              if (status) {
                status.textContent = "Ingestion failed · " +
                  (job.error_message || "open source record for details");
              }
            } else {
              active += 1;
              if (status) {
                status.textContent = job.status === "running"
                  ? "Parsing and analyzing"
                  : "Queued for ingestion";
              }
              if (job.status === "pending") queued += 1;
            }
          });

          var completed = succeeded + failed;
          progress.value = succeeded + failed;
          progress.classList.toggle("has-failures", failed > 0);
          label.textContent = completed + "/" + total + " ingestion jobs complete";
          detail.textContent = failed + " failed · " + (active - queued) +
            " processing · " + queued + " queued";

          if (completed === total) {
            if (result) {
              result.textContent = failed
                ? "Queue finished with " + failed + " failed file(s). Open the source record for job details."
                : "All source files have been ingested.";
            }
            return;
          }
          window.setTimeout(poll, 1200);
        })
        .catch(function (error) {
          detail.textContent = error.message;
          if (result) {
            result.textContent = error.message + " Refresh the page to retry status updates.";
          }
        });
    }

    poll();
  }

  var demoQueue = document.querySelector("[data-demo-queue]");
  if (demoQueue && !demoQueue.hidden) {
    startDemoQueuePolling();
  }

  document.body.addEventListener("click", function (evt) {
    var button = evt.target.closest("[data-simulate-action]");
    if (!button) return;

    var card = button.closest("[data-approval-card]");
    var result = card && card.querySelector("[data-demo-result]");
    if (!card || !result || button.disabled) return;

    button.disabled = true;
    button.textContent = "Demo completed";
    card.setAttribute("data-demo-completed", "true");
    result.hidden = false;
    result.textContent =
      "✓ Completed — Demonstration only. No action was executed or recorded on the server.";
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
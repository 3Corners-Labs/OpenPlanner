/*
 * Live progress of an update request (ADR 0048). Without this script the updates page shows the
 * host updater's steps as they were when it loaded, with a "Refresh status" link; with it:
 *
 *   - every 5 seconds it asks the fragment's poll URL; the server answers 204 while nothing
 *     changed, so the list is only replaced when the updater reported something;
 *   - a new status line is announced in the page's live region, and focus that was inside the
 *     replaced part moves to the card's heading instead of being lost;
 *   - while web restarts during the update the requests fail; it keeps asking;
 *   - it stops once the fragment carries no poll URL (the request ended) or the session ended
 *     (a redirect to the login page).
 *
 * All text is in the server-rendered fragment, so the script has no strings to translate.
 */
(function () {
  "use strict";

  var INTERVAL = 5000;
  var region = document.querySelector("[data-update-progress]");
  var announce = document.querySelector("[data-update-announce]");
  if (!region || !announce || !window.fetch || !window.DOMParser) {
    return;
  }

  function pollUrl() {
    var fragment = region.querySelector("[data-poll]");
    return fragment ? fragment.getAttribute("data-poll") : null;
  }

  function swap(html) {
    var parsed = new DOMParser().parseFromString(html, "text/html");
    var fragment = parsed.querySelector("[data-update-fragment]");
    if (!fragment) {
      return;
    }
    var hadFocus = region.contains(document.activeElement);
    while (region.firstChild) {
      region.removeChild(region.firstChild);
    }
    region.appendChild(document.importNode(fragment, true));
    var status = fragment.querySelector("[data-status]");
    if (status) {
      announce.textContent = status.textContent.trim();
    }
    if (hadFocus) {
      var heading = document.getElementById(region.getAttribute("data-heading"));
      if (heading) {
        heading.focus();
      }
    }
  }

  function schedule() {
    window.setTimeout(poll, INTERVAL);
  }

  function poll() {
    var url = pollUrl();
    if (!url) {
      return;
    }
    fetch(url, { credentials: "same-origin", cache: "no-store", redirect: "manual" })
      .then(function (response) {
        if (response.type === "opaqueredirect") {
          return null;
        }
        if (response.status === 200) {
          return response.text().then(function (html) {
            swap(html);
            schedule();
          });
        }
        schedule();
        return null;
      })
      .catch(schedule);
  }

  schedule();
})();

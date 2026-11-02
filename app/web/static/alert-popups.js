/*
 * Alert pop-ups on the overview (ADR 0061 §7). live-region.js refreshes the Alerts widget and
 * fires "live:replaced"; this script pops up the alerts that were not on the page before, at
 * the levels chosen in the profile menu (data-popup-level on [data-alert-popups]:
 * info = all, warning = warnings and outages, outage = outages only; absent = none).
 *
 *   - a pop-up stays at least 8 seconds (fixed, WCAG 2.2.1); hovering or focusing it pauses
 *     the time, leaving it starts the full time again; Escape or its button closes it;
 *   - each new alert is read out once in the page's one polite live region (#overview-announce);
 *     focus is never moved;
 *   - all text is the server-rendered widget's own (textContent only): nothing to translate.
 *
 * Without this script the alerts are in the widget and, for outages, in the banner.
 */
(function () {
  "use strict";

  var SHOW_MS = 8000;
  var RANK = { info: 0, warning: 1, outage: 2 };

  var box = document.querySelector("[data-alert-popups]");
  if (!box) {
    return;
  }
  var floor = box.getAttribute("data-popup-level");
  var known = {};

  function alertsOnPage() {
    return document.querySelectorAll("[data-alert-token]");
  }

  Array.prototype.forEach.call(alertsOnPage(), function (item) {
    known[item.getAttribute("data-alert-token")] = true;
  });

  function textOf(item, selector) {
    var found = item.querySelector(selector);
    return found ? found.textContent.trim() : "";
  }

  function announce(text) {
    var target = document.getElementById("overview-announce");
    if (target) {
      target.textContent = text;
    }
  }

  function show(item) {
    var level = item.getAttribute("data-alert-level");
    var title = textOf(item, "[data-alert-title]");
    var text = textOf(item, "[data-alert-text]");
    var link = item.querySelector("[data-alert-link]");
    var popup = document.createElement("div");
    popup.className = "alert-popup alert-popup--" + level;
    var line = document.createElement("p");
    var strong = document.createElement("strong");
    strong.textContent = textOf(item, "[data-alert-level-label]") + ": " + title;
    line.appendChild(strong);
    line.appendChild(document.createTextNode(" " + text));
    popup.appendChild(line);
    if (link) {
      var open = document.createElement("a");
      open.href = link.getAttribute("href");
      open.textContent = box.getAttribute("data-open-label");
      var more = document.createElement("p");
      more.appendChild(open);
      popup.appendChild(more);
    }
    var close = document.createElement("button");
    close.type = "button";
    close.className = "button button--ghost button--small";
    close.textContent = box.getAttribute("data-close-label");
    close.setAttribute("aria-label", box.getAttribute("data-close-label") + ": " + title);
    popup.appendChild(close);

    var timer = null;
    function remove() {
      window.clearTimeout(timer);
      var hadFocus = popup.contains(document.activeElement);
      if (popup.parentNode) {
        popup.parentNode.removeChild(popup);
      }
      var heading = document.getElementById("widget-alerts-title");
      if (hadFocus && heading) {
        heading.focus(); // closed by hand: focus goes back to the widget, not to the page
      }
    }
    function start() {
      window.clearTimeout(timer);
      timer = window.setTimeout(remove, SHOW_MS);
    }
    function pause() {
      window.clearTimeout(timer);
    }
    close.addEventListener("click", remove);
    popup.addEventListener("mouseenter", pause);
    popup.addEventListener("mouseleave", start);
    popup.addEventListener("focusin", pause);
    popup.addEventListener("focusout", function (event) {
      if (!popup.contains(event.relatedTarget)) {
        start();
      }
    });
    popup.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        remove();
      }
    });
    box.appendChild(popup);
    announce(box.getAttribute("data-new-label") + " " + strong.textContent + ". " + text);
    start();
  }

  document.addEventListener("live:replaced", function () {
    Array.prototype.forEach.call(alertsOnPage(), function (item) {
      var token = item.getAttribute("data-alert-token");
      if (known[token]) {
        return;
      }
      known[token] = true;
      var level = item.getAttribute("data-alert-level");
      if (floor && RANK[level] >= RANK[floor]) {
        show(item);
      }
    });
  });
})();

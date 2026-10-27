/*
 * Slot picker enhancement (ADR 0020). Without this script the month calendar is a table of
 * links and every choice reloads the page; with it:
 *
 *   - the table becomes an ARIA grid with one tab stop (roving tabindex): arrow keys move a
 *     day or a week, Home/End go to the start or end of the week, Page Up/Down change month;
 *   - choosing a day fetches that day's page and swaps in its times, announces the result in
 *     the live region and moves focus to the times heading (the next step).
 *
 * All text is in the server-rendered page, so the script has no strings to translate.
 * Any failure falls back to normal navigation.
 */
(function () {
  "use strict";

  var picker = document.querySelector("[data-picker]");
  if (!picker || !window.fetch || !window.DOMParser) {
    return;
  }
  var table = picker.querySelector("[data-calendar]");
  var announce = document.querySelector("[data-announce]");
  var items = Array.prototype.slice.call(table.querySelectorAll(".calendar__day"));
  if (!items.length) {
    return;
  }

  function isFree(item) {
    return item.tagName === "A";
  }

  function cellOf(item) {
    return item.parentElement;
  }

  // Grid semantics: only now that the keys below actually work.
  table.setAttribute("role", "grid");
  Array.prototype.forEach.call(table.querySelectorAll("tr"), function (row) {
    row.setAttribute("role", "row");
  });
  Array.prototype.forEach.call(table.querySelectorAll("th"), function (header) {
    header.setAttribute("role", "columnheader");
  });
  Array.prototype.forEach.call(table.querySelectorAll("td"), function (cell) {
    cell.setAttribute("role", "gridcell");
  });

  function markSelected(selected) {
    items.forEach(function (item) {
      var on = item === selected;
      if (isFree(item)) {
        if (on) {
          item.setAttribute("aria-current", "true");
        } else {
          item.removeAttribute("aria-current");
        }
      }
      cellOf(item).setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  var current =
    items.filter(function (item) { return item.getAttribute("aria-current") === "true"; })[0] ||
    items.filter(isFree)[0] ||
    items[0];

  items.forEach(function (item) {
    item.setAttribute("tabindex", item === current ? "0" : "-1");
    if (!isFree(item)) {
      item.setAttribute("aria-disabled", "true");
    }
  });
  markSelected(items.filter(function (item) {
    return item.getAttribute("aria-current") === "true";
  })[0] || null);

  function moveTo(index) {
    var next = items[Math.max(0, Math.min(items.length - 1, index))];
    if (next === current) {
      return;
    }
    current.setAttribute("tabindex", "-1");
    next.setAttribute("tabindex", "0");
    next.focus();
    current = next;
  }

  function weekdayOf(item) {
    // Monday = 0: the column index of the cell within its row.
    return Array.prototype.indexOf.call(cellOf(item).parentElement.children, cellOf(item));
  }

  function followLink(rel) {
    var link = picker.querySelector('.calendar__nav a[rel="' + rel + '"]');
    if (link) {
      window.location.href = link.href;
    }
  }

  function choose(item) {
    var times = picker.querySelector("[data-times]");
    var href = item.href;
    times.setAttribute("aria-busy", "true");
    window
      .fetch(href, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        return response.text();
      })
      .then(function (html) {
        var page = new window.DOMParser().parseFromString(html, "text/html");
        var fresh = page.querySelector("[data-times]");
        if (!fresh) {
          throw new Error("no times");
        }
        times.replaceWith(fresh);
        markSelected(item);
        window.history.replaceState(null, "", href.split("#")[0]);
        var heading = fresh.querySelector("h2");
        var count = fresh.querySelector(".times__count");
        if (announce) {
          announce.textContent =
            (heading ? heading.textContent : "") + (count ? ": " + count.textContent : "");
        }
        if (heading) {
          heading.focus();
        }
      })
      .catch(function () {
        window.location.href = href;
      });
  }

  table.addEventListener("keydown", function (event) {
    var index = items.indexOf(current);
    var day = weekdayOf(current);
    switch (event.key) {
      case "ArrowRight":
        moveTo(index + 1);
        break;
      case "ArrowLeft":
        moveTo(index - 1);
        break;
      case "ArrowDown":
        moveTo(index + 7);
        break;
      case "ArrowUp":
        moveTo(index - 7);
        break;
      case "Home":
        moveTo(index - day);
        break;
      case "End":
        moveTo(index + (6 - day));
        break;
      case "PageUp":
        followLink("prev");
        break;
      case "PageDown":
        followLink("next");
        break;
      case " ":
      case "Enter":
        if (isFree(current)) {
          choose(current);
        }
        break;
      default:
        return;
    }
    event.preventDefault();
  });

  table.addEventListener("click", function (event) {
    var item = event.target.closest ? event.target.closest("a.calendar__day") : null;
    if (!item || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    current.setAttribute("tabindex", "-1");
    item.setAttribute("tabindex", "0");
    current = item;
    choose(item);
  });
})();

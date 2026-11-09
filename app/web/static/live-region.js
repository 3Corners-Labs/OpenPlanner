/*
 * Live regions (ADR 0072): the participant's waiting page, the own queue and a claimed booking
 * keep themselves current without reloading. Without this script each page shows the state
 * it loaded with and offers a Refresh link; with it, per region marked [data-live]:
 *
 *   - the region holds one [data-live-fragment] with a poll URL (data-poll) and an interval
 *     in seconds (data-poll-seconds, the platform setting QUEUE_REFRESH_SECONDS);
 *   - at every interval the fragment's URL is asked; the server answers 204 while nothing
 *     changed, so the fragment is only replaced when something did;
 *   - the new fragment's [data-live-status] sentence is announced in the region's
 *     [data-live-announce] element, or in the element whose id the region names in
 *     data-announce when several regions share the page's one polite live region (the
 *     overview, ADR 0061 §4); focus that was inside the replaced part moves to the heading
 *     the region names (data-heading), so it is never lost;
 *   - after a swap the region fires a "live:replaced" event (bubbling), for a page script
 *     that reacts to new content (the overview's alert pop-ups, ADR 0061 §7);
 *   - a failed request is retried at the next interval; a redirect (session ended) or a
 *     fragment without poll URL (the call ended, the state is final) stops it;
 *   - a region that names data-stale-after="3" (the wallboard, ADR 0117 point 20) counts failed
 *     requests in a row; at that count, or at once when the session ended, it sets data-stale
 *     on the region and announces the sentence of the fragment's [data-live-stale-status]. The
 *     stylesheet shows the old-data state in words; the next good answer removes it;
 *   - a button [data-live-pause] in the region pauses and resumes the asking (aria-pressed and
 *     data-paused on the region); a paused region keeps its last fragment and says so.
 *
 * All text is in the server-rendered fragment: the script has nothing to translate.
 */
(function () {
  "use strict";

  var DEFAULT_SECONDS = 10;
  var MIN_SECONDS = 5;

  if (!window.fetch || !window.DOMParser) {
    return;
  }

  function fragmentOf(region) {
    return region.querySelector("[data-live-fragment]");
  }

  function secondsOf(fragment) {
    var seconds = fragment ? parseInt(fragment.getAttribute("data-poll-seconds"), 10) : NaN;
    if (isNaN(seconds)) {
      return DEFAULT_SECONDS;
    }
    return Math.max(MIN_SECONDS, seconds);
  }

  function announceTarget(region) {
    return (
      region.querySelector("[data-live-announce]") ||
      document.getElementById(region.getAttribute("data-announce") || "")
    );
  }

  function announce(region, fragment) {
    var target = announceTarget(region);
    var status = fragment.querySelector("[data-live-status]");
    if (target && status) {
      var text = status.textContent.trim();
      if (target.textContent !== text) {
        target.textContent = text;
      }
    }
  }

  function staleLimit(region) {
    var limit = parseInt(region.getAttribute("data-stale-after"), 10);
    return isNaN(limit) || limit < 1 ? 0 : limit;
  }

  function replaceFragment(region, html) {
    var parsed = new DOMParser().parseFromString(html, "text/html");
    var incoming = parsed.querySelector("[data-live-fragment]");
    var outgoing = fragmentOf(region);
    if (!incoming || !outgoing) {
      return;
    }
    var hadFocus = outgoing.contains(document.activeElement);
    var fresh = document.importNode(incoming, true);
    outgoing.parentNode.replaceChild(fresh, outgoing);
    announce(region, fresh);
    if (typeof window.CustomEvent === "function") {
      region.dispatchEvent(new CustomEvent("live:replaced", { bubbles: true }));
    }
    if (hadFocus) {
      var heading = document.getElementById(region.getAttribute("data-heading") || "");
      if (heading) {
        heading.focus();
      }
    }
  }

  function watch(region) {
    var failures = 0;
    var paused = false;
    var timer = null;

    function markStale() {
      if (!staleLimit(region) || region.hasAttribute("data-stale")) {
        return;
      }
      region.setAttribute("data-stale", "");
      var target = announceTarget(region);
      var status = region.querySelector("[data-live-stale-status]");
      if (target && status) {
        target.textContent = status.textContent.trim();
      }
    }

    function failed() {
      failures += 1;
      if (staleLimit(region) && failures >= staleLimit(region)) {
        markStale();
      }
    }

    function later() {
      window.clearTimeout(timer);
      timer = window.setTimeout(ask, secondsOf(fragmentOf(region)) * 1000);
    }

    function handle(response) {
      if (response.type === "opaqueredirect") {
        markStale(); // the session ended: the next click shows the login page
        return false;
      }
      if (response.status === 204) {
        failures = 0; // unchanged is a good answer
        region.removeAttribute("data-stale");
        return true;
      }
      if (response.status !== 200) {
        failed(); // a hiccup: ask again at the next interval
        return true;
      }
      return response.text().then(function (html) {
        failures = 0;
        region.removeAttribute("data-stale");
        replaceFragment(region, html);
        return true;
      });
    }

    function ask() {
      if (paused) {
        later();
        return;
      }
      var fragment = fragmentOf(region);
      var url = fragment ? fragment.getAttribute("data-poll") : null;
      if (!url) {
        return;
      }
      fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        redirect: "manual",
        headers: { "X-Live-Poll": "1" },
      })
        .then(handle)
        .then(function (again) {
          if (again) {
            later();
          }
        })
        .catch(function () {
          failed();
          later();
        });
    }

    Array.prototype.forEach.call(region.querySelectorAll("[data-live-pause]"), function (button) {
      button.hidden = false; // a pause button is no use without the script, so it starts hidden
      button.addEventListener("click", function () {
        paused = !paused;
        button.setAttribute("aria-pressed", paused ? "true" : "false");
        if (paused) {
          region.setAttribute("data-paused", "");
        } else {
          region.removeAttribute("data-paused");
          ask();
        }
      });
    });

    later();
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-live]"), watch);
})();

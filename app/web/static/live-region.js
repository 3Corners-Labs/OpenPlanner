/*
 * Live regions (ADR 0072): the citizen's waiting page, the own queue and a claimed booking
 * keep themselves current without reloading. Without this script each page shows the state
 * it loaded with and offers a Refresh link; with it, per region marked [data-live]:
 *
 *   - the region holds one [data-live-fragment] with a poll URL (data-poll) and an interval
 *     in seconds (data-poll-seconds, the platform setting QUEUE_REFRESH_SECONDS);
 *   - at every interval the fragment's URL is asked; the server answers 204 while nothing
 *     changed, so the fragment is only replaced when something did;
 *   - the new fragment's [data-live-status] sentence is announced in the region's
 *     [data-live-announce] element (one polite live region per page), and focus that was
 *     inside the replaced part moves to the heading the region names (data-heading);
 *   - a failed request is retried at the next interval; a redirect (session ended) or a
 *     fragment without poll URL (the call ended, the state is final) stops it.
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

  function announce(region, fragment) {
    var target = region.querySelector("[data-live-announce]");
    var status = fragment.querySelector("[data-live-status]");
    if (target && status) {
      target.textContent = status.textContent.trim();
    }
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
    if (hadFocus) {
      var heading = document.getElementById(region.getAttribute("data-heading") || "");
      if (heading) {
        heading.focus();
      }
    }
  }

  function watch(region) {
    function later() {
      window.setTimeout(ask, secondsOf(fragmentOf(region)) * 1000);
    }

    function handle(response) {
      if (response.type === "opaqueredirect") {
        return false; // the session ended: the next click shows the login page
      }
      if (response.status !== 200) {
        return true; // 204 (unchanged) or a hiccup: ask again at the next interval
      }
      return response.text().then(function (html) {
        replaceFragment(region, html);
        return true;
      });
    }

    function ask() {
      var fragment = fragmentOf(region);
      var url = fragment ? fragment.getAttribute("data-poll") : null;
      if (!url) {
        return;
      }
      fetch(url, { credentials: "same-origin", cache: "no-store", redirect: "manual" })
        .then(handle)
        .then(function (again) {
          if (again) {
            later();
          }
        })
        .catch(later);
    }

    later();
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-live]"), watch);
})();

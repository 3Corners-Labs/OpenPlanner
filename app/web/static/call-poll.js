/*
 * The one poll of the participant's call page: who the participant talks to (ADR 0123 step 5c).
 *
 * Owns: asking our own server, while the page waits in the lobby or is in the call, for the staff
 * member's name (`GET` on the address the server put in the markup) once on entering the lobby,
 * once on entering the call and then again every `data-call-poll-seconds` (a platform setting),
 * with one timer and at most one request in flight. The answer is a name or nothing; the name is
 * put into the state (`state.staffName`) and said once through the polite region when the page is
 * in the call: what was last said is kept apart (`state.staffSaid`), so a name learned in the lobby
 * is said on entering the call, the same name asked ten times is said once, and a change is said
 * once more. A name that is cleared is not announced and the label is the team's word again. An
 * answer of 404 (the switch went off) clears the name; any other refusal or a network error keeps
 * the name and makes the next turn wait twice as long, up to `BACKOFF_MAX_MS`, and a success brings
 * back the setting's interval. Nothing is announced for a failure. This is the page's one poll: the chat of step 7c is asked
 * for in the same turns, not by a timer of its own.
 *
 * Never: it asks nobody but our own server, at the address the markup names, never the video platform,
 * and stores nothing (the name lives in the page's state only and is gone with the call). The
 * poll is stopped, and an answer on its way is dropped, whenever the page leaves the lobby and the
 * call (leaving, the call ending, failing, a page restored out of the browser's memory) or a new
 * connection starts: every answer is checked against the call's epoch, as the steps of the
 * connection are. A failed or unreadable answer changes nothing and is tried again at the next
 * turn; the name is only ever set as text. It runs only when the page's `render` asks it to
 * (`sync`).
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  // The most characters of a name (mirrors MAX_LENGTH of app/domain/screen_name.py; a test reads
  // both): a longer one is cut, the label is one short line.
  var MAX_NAME = 40;
  // The longest wait between two turns after failures; the setting's interval is the shortest.
  var BACKOFF_MAX_MS = 30000;

  var url = "";
  var turnMs = 0; // the setting's interval
  var waitMs = 0; // the wait to the next turn: turnMs, or more after failures
  var timer = 0; // the one timer: the wait to the next turn
  var request = null; // the AbortController of the request in flight
  var watching = ""; // the screen (lobby or call) the poll has asked for, "" when it is stopped

  function watches(screen) {
    return screen === "lobby" || screen === "call";
  }

  function stop() {
    window.clearTimeout(timer);
    timer = 0;
    if (request) request.abort();
    request = null;
    watching = "";
  }

  // The name in an answer: text, at most MAX_NAME characters, or "" for none.
  function nameOf(body) {
    var name = body && typeof body.name === "string" ? body.name.trim() : "";
    return name.slice(0, MAX_NAME);
  }

  // The answer's name is known now. It is said once, in the call, when it is not the one last said:
  // a name learned in the lobby waits for the call (the sentence of being let in comes first, the
  // answer of the call's own first turn follows it). A cleared name is forgotten, not announced.
  function receive(name) {
    var state = app.state;
    var patch = {};
    if (name !== state.staffName) patch.staffName = name;
    var say = state.screen === "call" && name !== "" && name !== state.staffSaid;
    if (say) patch.staffSaid = name;
    else if (name === "" && state.staffSaid !== "") patch.staffSaid = "";
    if (Object.keys(patch).length) app.go(state.screen, patch);
    if (say) app.say("say_staff_name", { name: name });
  }

  // A turn went well: the next one is at the setting's interval. A turn went wrong: it waits twice
  // as long as the last one, up to BACKOFF_MAX_MS.
  function settle(epoch, ok) {
    waitMs = ok ? turnMs : Math.min(waitMs * 2, BACKOFF_MAX_MS);
    later(epoch);
  }

  function later(epoch) {
    if (epoch !== app.call.epoch || !watching) return;
    timer = window.setTimeout(function () {
      timer = 0;
      if (watching) ask();
    }, waitMs);
  }

  function ask() {
    var epoch = app.call.epoch;
    var controller = new AbortController();
    request = controller;
    window
      .fetch(url, { method: "GET", credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(function (response) {
        if (response.status === 404) return { gone: true };
        if (!response.ok) throw new Error("refused");
        return response.json();
      })
      .then(
        function (body) {
          if (request !== controller) return;
          request = null;
          if (epoch !== app.call.epoch || !watches(app.state.screen)) return;
          receive(nameOf(body));
          settle(epoch, !(body && body.gone));
        },
        function () {
          if (request !== controller) return;
          request = null;
          if (epoch !== app.call.epoch || !watches(app.state.screen)) return;
          settle(epoch, false);
        },
      );
  }

  // Called by `render` after every change of the state: starts the poll when the page enters the
  // lobby or the call (and asks again when it moves on to the call), stops it
  // everywhere else.
  function sync(state) {
    if (!url || !turnMs || !watches(state.screen)) {
      if (watching || timer || request) stop();
      return;
    }
    if (watching === state.screen) return;
    stop();
    watching = state.screen;
    waitMs = turnMs;
    ask();
  }

  function init() {
    url = app.root.dataset.callStaffUrl || "";
    turnMs = (parseInt(app.root.dataset.callPollSeconds, 10) || 0) * 1000;
  }

  app.poll = { init: init, sync: sync };
})();

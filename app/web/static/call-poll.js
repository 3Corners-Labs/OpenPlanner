/*
 * The one poll of the participant's call page: who the participant talks to (ADR 0123 step 5c).
 *
 * Owns: asking our own server, while the page waits in the lobby or is in the call, for the staff
 * member's name (`GET` on the address the server put in the markup) once on entering the lobby,
 * once on entering the call and then again every `data-call-poll-seconds` (a platform setting),
 * with one timer and at most one request in flight. The answer is a name or nothing; a name that
 * arrives or changes is put into the state (`state.staffName`) and, when it happens in the call,
 * said once through the polite region. This is the page's one poll: the chat of step 7c is asked
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

  var MAX_NAME = 40; // a name longer than the server allows is cut: the label is one short line

  var url = "", turnMs = 0;
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

  // A name arrived or changed. In the call it is said once; in the lobby the picture is not shown
  // yet, so it waits for the call (the sentence of the participant being let in comes first).
  function receive(name) {
    var state = app.state;
    if (name === state.staffName) return;
    var said = state.screen === "call" && name !== "";
    app.go(state.screen, { staffName: name });
    if (said) app.say("say_staff_name", { name: name });
  }

  function later(epoch) {
    if (epoch !== app.call.epoch || !watching) return;
    timer = window.setTimeout(function () {
      timer = 0;
      if (watching) ask();
    }, turnMs);
  }

  function ask() {
    var epoch = app.call.epoch;
    var controller = new AbortController();
    request = controller;
    window
      .fetch(url, { method: "GET", credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(function (response) {
        if (!response.ok) throw new Error("refused");
        return response.json();
      })
      .then(
        function (body) {
          if (request !== controller) return;
          request = null;
          if (epoch !== app.call.epoch || !watches(app.state.screen)) return;
          receive(nameOf(body));
          later(epoch);
        },
        function () {
          if (request !== controller) return;
          request = null;
          later(epoch);
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
    ask();
  }

  function init() {
    url = app.root.dataset.callStaffUrl || "";
    turnMs = (parseInt(app.root.dataset.callPollSeconds, 10) || 0) * 1000;
  }

  app.poll = { init: init, sync: sync };
})();

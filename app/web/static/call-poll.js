/*
 * The one poll of the participant's call page: who the participant talks to (ADR 0123 step 5c) and,
 * in the same turns, what is new in the chat beside the call (step 7c).
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
 * back the setting's interval. Nothing is announced for a failure. This is the page's one poll: the chat
 * (`call-chat.js`, which has no timer) is asked for in the same turns, in every state after the start while
 * `app.chat.wants` says the page still needs it; a turn is one request for the name (in the lobby and the call)
 * and one for the chat, together, and it went well only when both did. When neither is wanted any more the poll
 * stops by itself.
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

  // The staff member's name is asked for in the lobby and the call only.
  function watches(screen) {
    return screen === "lobby" || screen === "call";
  }

  // What this turn asks for: the name (when the page has an address for it and is in the lobby or the
  // call), the chat (when the page has one and still needs it).
  function wantsName(state) {
    return Boolean(url) && watches(state.screen);
  }

  function wantsAny(state) {
    return wantsName(state) || app.chat.wants(state);
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

  // The name: resolves true when the turn went well. An answer that comes after the call moved on is
  // dropped.
  function askName(controller, epoch) {
    return window
      .fetch(url, { method: "GET", credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(function (response) {
        if (response.status === 404) return { gone: true };
        if (!response.ok) throw new Error("refused");
        return response.json();
      })
      .then(
        function (body) {
          if (request !== controller || epoch !== app.call.epoch || !wantsName(app.state)) return true;
          receive(nameOf(body));
          return !(body && body.gone);
        },
        function () {
          return false;
        },
      );
  }

  function ask() {
    var epoch = app.call.epoch;
    var controller = new AbortController();
    request = controller;
    var jobs = [];
    if (wantsName(app.state)) jobs.push(askName(controller, epoch));
    if (app.chat.wants(app.state)) jobs.push(app.chat.turn(controller.signal));
    if (!jobs.length) {
      stop();
      return;
    }
    Promise.all(jobs).then(function (results) {
      if (request !== controller) return;
      request = null;
      if (epoch !== app.call.epoch || !wantsAny(app.state)) return;
      settle(epoch, results.every(Boolean));
    });
  }

  // Called by `render` after every change of the state: starts the poll when the page enters a screen
  // that needs it (the lobby and the call for the name, every screen after the start for the chat) and
  // asks again when it moves on to another, stops it everywhere else.
  function sync(state) {
    if (!turnMs || !wantsAny(state)) {
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

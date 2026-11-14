/*
 * The participant's call page: its one state and its one view (ADR 0123 steps 4b, 4c, 5a and 5b).
 *
 * Owns: the page's single `state` and the one way to change it, `go(screen, patch)`, which checks
 * the move against a table of allowed transitions, renders, moves focus to the new heading and
 * hands one sentence to the polite status line. It also owns `render` (what is shown and the place
 * focus goes; the lobby's and the call's parts are drawn by call-render.js), the texts and the
 * wiring of the buttons. Every word is the server's own markup (the sections of the page and [data-text]); the
 * script only shows and hides it and sets text with textContent.
 *
 * Split in files that share the one object `window.openplannerCall` (no build step): call-status.js
 * is the polite status line, call-sound.js the level meter and test sound, call-devices.js the
 * devices (the check, the lists, the choice), call-connection.js the call (token, SDK, attempts,
 * lobby, leaving), call-media.js the participant's camera, microphone and screen in the call,
 * call-captions.js the captions, call-remote.js the other side's picture, sound and screen,
 * call-render.js how the call's parts look, call-events.js what the meeting tells the page. They only define; this file, loaded last,
 * starts them.
 *
 * Never: it makes no request, loads no file, stores nothing, sets no inline style and builds no
 * markup out of text. Without the script, or in a browser without the needed features, the page
 * keeps the start state with the sentence that says what the call needs.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});
  var root = document.querySelector("[data-call]");
  if (!root) return;

  var AudioContextClass = window.AudioContext || window.webkitAudioContext;
  var media = navigator.mediaDevices;
  if (!media || !media.getUserMedia || !AudioContextClass || !window.MediaStream) return;

  var $ = function (selector) {
    return root.querySelector(selector);
  };
  var $$ = function (selector) {
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  };
  var KINDS = ["camera", "microphone"];
  app.root = root;
  app.media = media;
  app.AudioContextClass = AudioContextClass;
  app.$ = $;
  app.$$ = $$;
  app.KINDS = KINDS;

  // Every sentence the page can say: [data-text] in the markup, in the reader's language.
  var texts = {};
  $$("[data-text]").forEach(function (node) {
    texts[node.dataset.text] = node.textContent.trim();
  });
  app.texts = texts;

  var preview = $("[data-call-preview]");
  var joinButton = $("[data-call-join]");
  var beginButton = $("[data-call-begin]");
  var toggles = {
    camera: $$('[data-call-toggle="camera"]'),
    microphone: $$('[data-call-toggle="microphone"]'),
  };
  var safari =
    /Safari/.test(navigator.userAgent) &&
    !/Chrome|Chromium|Edg|Firefox|FxiOS|CriOS/.test(navigator.userAgent);

  // The pictures and the sound of the call. They are streams, not state: `render` shows them.
  app.streams = { self: null, remote: null, audio: null, share: null }; // share: the staff member's screen

  // ---- the one state ---------------------------------------------------------------------------
  function fresh() {
    return Object.assign({
      screen: "start", // start, check, connecting, lobby, call, failed, left, ended or unavailable
      refused: [], // kinds the browser refuses
      missing: [], // kinds with no device
      wish: { camera: true, microphone: true }, // asked for by the participant
      has: { camera: false, microphone: false }, // a live track in the check
      soundPlayed: false,
      speaker: false, // the browser can choose a speaker and there is one to choose
      live: { camera: false, microphone: false }, // on in the lobby or the call
      attempt: 0, // the attempt at connecting; the first is 1, any after it is a retry
      failure: "", // attempts, generic, not_open, rate_limited or unavailable
      media: "none", // none, adding or added: the call's picture and sound go in
      remote: "unknown", // unknown, waiting, live or off: the other side's picture
      soundBlocked: false, // the browser refused to play the other side's sound
    }, inCallParts());
  }

  // What belongs to the call alone and starts afresh with every screen outside it.
  function inCallParts() {
    return {
      panel: false, // the panel of devices in the call is open
      recording: "off", // off, pending (being recorded, not yet said) or on (said): the meeting is recorded
      connection: "ok", // ok, pending (being restored, not yet said) or restoring (said)
      share: "off", // off, asking (the browser's picker is open), mine or theirs: whose screen is shared
      captions: "off", // off, starting or on
      lines: [], // the captions in view: their final lines (in memory only, gone with the call)
      interim: null, // the caption being spoken, until its final text arrives
      can: { share: false, captions: false }, // what the meeting offers this participant
      stopHeld: false, // focus was last on the notice's stop button (it goes away with the share)
    };
  }
  var state = fresh();
  app.state = state;

  // The moves that are allowed. A move to the screen it is already on is always allowed: it only
  // changes what that screen shows.
  var ALLOWED = {
    start: ["check", "unavailable"],
    check: ["connecting", "unavailable"],
    connecting: ["lobby", "call", "failed", "unavailable", "left", "ended"],
    lobby: ["call", "failed", "left", "ended"],
    call: ["failed", "left", "ended"],
    failed: ["connecting"],
    left: [],
    ended: [],
    unavailable: [],
  };

  // ---- go: the one way the state changes -------------------------------------------------------
  // `patch` holds fields of the state and, besides them, `say` (the key of the one sentence for
  // the live region, "" to clear it) and `focus`, which names the place focus goes to: "denied" (the panel
  // of a refused device), "devices" (the button that opens the panel of the call's devices),
  // "state" (the heading of the screen, which is also what a change of screen without `focus`
  // does), or nothing else: any other move of focus is decided in `render` out of the
  // state (the toggle of sharing when its stop button went away with the share). Returns whether
  // the move was allowed. Leaving the lobby and the call closes the panel and ends its notices,
  // sharing and captions.
  function go(screen, patch) {
    var from = state.screen;
    var changed = screen !== from;
    if (changed && ALLOWED[from].indexOf(screen) === -1) return false;
    var fields = Object.assign({}, patch);
    var sentence = fields.say;
    var focus = fields.focus;
    delete fields.say;
    delete fields.focus;
    Object.assign(state, fields);
    state.screen = screen;
    var inCall = screen === "lobby" || screen === "call";
    // A new screen outside the lobby and the call, or a new attempt, starts the call's panel and
    // notices afresh.
    if (!inCall && (changed || fields.attempt !== undefined)) {
      Object.assign(state, inCallParts());
    }
    render();
    if (sentence !== undefined) app.say(sentence);
    // A notice is said once, when it first shows: it may have started while connecting.
    if (state.recording === "pending" && inCall) {
      state.recording = "on";
      app.say("say_recording_on");
    }
    if (state.connection === "pending" && inCall) {
      state.connection = "restoring";
      app.say("say_restoring");
    }
    var target = null;
    if (focus === "denied") {
      target = $('[data-call-denied="' + app.deniedKind(state.refused) + '"] h2');
    } else if (focus === "devices") {
      target = $("[data-call-devices-button]");
    } else if (focus === "state" || (changed && focus === undefined)) {
      target = $('[data-call-state="' + screen + '"] h1');
    }
    if (target) target.focus();
    return true;
  }
  app.go = go;

  // A page restored by the browser out of its memory: the devices and the call are gone, so the
  // page starts again at the start. The one move that is not in the table.
  function restart() {
    Object.keys(state).forEach(function (key) {
      delete state[key];
    });
    Object.assign(state, fresh());
    render();
    app.say("");
  }

  // Which panel of a refused permission is shown: one kind, both, or none.
  app.deniedKind = function (refused) {
    if (refused.length === 2) return "both";
    return refused.length === 1 ? refused[0] : "";
  };

  function joinLabel() {
    var absent = state.refused.concat(state.missing);
    var camera = absent.indexOf("camera") !== -1;
    var microphone = absent.indexOf("microphone") !== -1;
    if (camera && microphone) return texts.join_both;
    if (camera) return texts.join_camera;
    return microphone ? texts.join_microphone : texts.join;
  }

  // ---- render: what the state looks like ---------------------------------------------------------
  var view = app.view; // call-render.js, loaded before this file

  function render() {
    var screen = state.screen;
    var checking = screen === "check";
    var inCall = screen === "lobby" || screen === "call";
    var denied = app.deniedKind(state.refused);
    $$("[data-call-state]").forEach(function (section) {
      section.hidden = section.dataset.callState !== screen;
    });
    $$("[data-call-denied]").forEach(function (panel) {
      panel.hidden = !(checking && panel.dataset.callDenied === denied);
    });
    $$("[data-call-missing]").forEach(function (note) {
      note.hidden = !(checking && state.missing.indexOf(note.dataset.callMissing) !== -1);
    });
    // Nothing found and nothing refused: the participant may plug something in and look again.
    $("[data-call-recheck]").hidden = !(
      checking &&
      !state.has.camera &&
      !state.has.microphone &&
      state.refused.length === 0 &&
      state.wish.camera &&
      state.wish.microphone
    );
    // In the check a device is on while its track is live; in the lobby and the call, while the
    // participant has it switched on.
    KINDS.forEach(function (kind) {
      var on = checking ? state.has[kind] : state.live[kind];
      toggles[kind].forEach(function (button) {
        button.setAttribute("aria-pressed", String(on));
        button.querySelector("[data-call-toggle-state]").textContent = texts[on ? "on" : "off"];
      });
      // The list of a device that is refused or not found would only list placeholders.
      var absent = state.refused.indexOf(kind) !== -1 || state.missing.indexOf(kind) !== -1;
      $('[data-call-field="' + kind + '"]').hidden = checking ? absent : true;
    });
    preview.hidden = !state.has.camera;
    $("[data-call-speaker-field]").hidden = !(checking && state.speaker);
    $("[data-call-sound-note]").hidden = !state.soundPlayed;
    joinButton.textContent = joinLabel();
    renderCall(inCall);
    // The notice's stop button goes away with the share; focus does not fall to the page.
    if (state.stopHeld && state.share !== "mine") {
      state.stopHeld = false;
      $('[data-call-switch="share"]').focus();
    }
  }

  // The parts of the page that begin at the click that joins.
  function renderCall(inCall) {
    var screen = state.screen;
    var retrying = screen === "connecting" && state.attempt > 1;
    $("[data-call-retry-note]").hidden = !retrying;
    var count = $("[data-call-attempt]");
    count.hidden = !retrying;
    count.textContent = retrying ? texts.attempt.replace("{n}", String(state.attempt)) : "";
    $$("[data-call-failure]").forEach(function (note) {
      note.hidden = note.dataset.callFailure !== state.failure;
    });
    // A call that is not open cannot be tried again, and would not open through the other way
    // either: contact details only.
    $("[data-call-again]").hidden = state.failure === "not_open";
    var otherWay = $("[data-call-failed-other-way]");
    var offered = Boolean(otherWay) && state.failure !== "not_open";
    if (otherWay) otherWay.hidden = !offered;
    $("[data-call-failed-contacts]").hidden = offered;
    // Leaving through a link would end the call: the way back appears when it is over.
    $("[data-call-back]").hidden = screen === "connecting" || inCall;
    view.pictures(state);
    view.notices(state);
    view.sharing(state);
    view.captions(state);
    view.bar(state);
  }
  // ---- what the participant does -----------------------------------------------------------------
  function join() {
    var picked = app.devices.snapshot();
    app.devices.release();
    if (!app.connection.joinable) {
      go("unavailable", { say: "" });
      return;
    }
    app.connection.start(picked);
  }

  function toggle(kind) {
    if (state.screen === "lobby" || state.screen === "call") {
      app.connection.setLive(kind, !state.live[kind]);
    } else {
      app.devices.toggle(kind);
    }
  }

  // The panel of the call's devices is a disclosure: its button opens and closes it (the lists are
  // looked at again when it opens), Escape closes it and returns focus to the button.
  function wireDevicesPanel() {
    $("[data-call-devices-button]").addEventListener("click", function () {
      if (state.screen !== "lobby" && state.screen !== "call") return;
      go(state.screen, { panel: !state.panel });
      if (state.panel) app.devices.list();
    });
    $("[data-call-controls]").addEventListener("keydown", function (event) {
      if (event.key === "Escape" && state.panel) go(state.screen, { panel: false, focus: "devices" });
    });
  }

  // The page goes away: the devices of the check and the call, if any, are given back.
  function leavePage() {
    app.devices.release();
    app.connection.hangUp();
  }

  function pageBack(event) {
    // Restored by the browser (its back-forward cache): start again at the start.
    if (!event.persisted) return;
    leavePage();
    restart();
  }

  app.sound.init();
  app.devices.init();
  app.connection.init();
  app.remote.init();

  $$("[data-call-browser]").forEach(function (step) {
    step.hidden = step.dataset.callBrowser !== (safari ? "safari" : "other");
  });
  $$("[data-call-retry]").forEach(function (button) {
    button.addEventListener("click", app.devices.acquire);
  });
  beginButton.addEventListener("click", app.devices.begin);
  joinButton.addEventListener("click", join);
  $("[data-call-test-sound]").addEventListener("click", app.sound.playTestSound);
  wireDevicesPanel();
  $$("[data-call-leave]").forEach(function (button) {
    button.addEventListener("click", app.connection.leave);
  });
  $("[data-call-again]").addEventListener("click", app.connection.again);
  // One share in a meeting: while the staff member's is shown, a press on the share switch says so.
  $('[data-call-switch="share"]').addEventListener("click", function () {
    if (state.share === "theirs") app.say("say_share_theirs");
    else app.callMedia.share(state.share === "off");
  });
  $("[data-call-share-stop]").addEventListener("click", function () {
    app.callMedia.share(false);
  });
  $('[data-call-switch="captions"]').addEventListener("click", app.captions.press);
  // The last place of focus: a stop button that goes away is not a stop button that loses focus.
  document.addEventListener("focusin", function (event) {
    state.stopHeld = event.target === $("[data-call-share-stop]");
  });
  KINDS.forEach(function (kind) {
    toggles[kind].forEach(function (button) {
      button.addEventListener("click", function () {
        toggle(kind);
      });
    });
  });
  // The three lists of the check and the three of the call's panel are one choice each.
  $$("[data-call-select], [data-call-pick]").forEach(function (list) {
    list.addEventListener("change", function (event) {
      var kind = list.dataset.callSelect || list.dataset.callPick;
      app.devices.choose(kind, event.target.value);
    });
  });
  media.addEventListener("devicechange", function () {
    var inCall = state.screen === "lobby" || state.screen === "call";
    if (state.screen === "check" || (inCall && state.panel)) app.devices.list();
  });
  window.addEventListener("pagehide", leavePage);
  window.addEventListener("pageshow", pageBack);

  // The browser can run the call: the sentence that says what it needs is no longer true.
  $("[data-call-needs]").hidden = true;
  beginButton.hidden = false;
})();

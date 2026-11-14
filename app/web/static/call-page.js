/*
 * The participant's call page: its one state and its one view (ADR 0123 steps 4b and 4c).
 *
 * Owns: the page's single `state` and the one way to change it, `go(screen, patch)`, which checks
 * the move against a table of allowed transitions, renders, moves focus to the new heading and
 * hands one sentence to the polite status line. It also owns `render` (every hidden flag, pressed
 * state, picture and word of the page), the status queue, the texts and the wiring of the
 * buttons. Every word is the server's own markup (the sections of the page and [data-text]); the
 * script only shows and hides it and sets text with textContent.
 *
 * Split in four files that share the one object `window.openplannerCall` (no build step):
 * call-devices.js is the device check, call-connection.js is the call (token, SDK, attempts,
 * lobby, leaving), call-media.js is the participant's camera and microphone in the call. They
 * only define; this file, loaded last, starts them.
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

  var statusLine = $("[data-call-status]");
  var preview = $("[data-call-preview]");
  var joinButton = $("[data-call-join]");
  var beginButton = $("[data-call-begin]");
  var toggles = {
    camera: $$('[data-call-toggle="camera"]'),
    microphone: $$('[data-call-toggle="microphone"]'),
  };
  var selfVideos = $$("[data-call-self]");
  var remoteVideo = $("[data-call-remote]");
  var remoteAudio = $("[data-call-remote-audio]");
  var safari =
    /Safari/.test(navigator.userAgent) &&
    !/Chrome|Chromium|Edg|Firefox|FxiOS|CriOS/.test(navigator.userAgent);

  // The pictures and the sound of the call. They are streams, not state: `render` shows them.
  app.streams = { self: null, remote: null, audio: null };

  // ---- the one state ---------------------------------------------------------------------------
  function fresh() {
    return {
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

  // ---- the status line: one sentence at a time -----------------------------------------------
  // The polite region gets one sentence at a time, each kept on screen for a minimum time, so a
  // new sentence never replaces one that has not been read out: none is dropped, none repeats
  // (a sentence equal to the one waiting last is not queued twice). "" clears region and queue.
  var SAY_MS = 1500;
  var queue = [];
  var sayTimer = 0;
  var sayBusy = false;
  function pump() {
    if (sayBusy || !queue.length) return;
    sayBusy = true;
    statusLine.textContent = "";
    var next = queue.shift();
    // Emptied first, so a sentence equal to the one before is read out again.
    window.setTimeout(function () {
      statusLine.textContent = next;
    }, 60);
    sayTimer = window.setTimeout(function () {
      sayBusy = false;
      pump();
    }, SAY_MS);
  }
  function say(key) {
    if (!key) {
      window.clearTimeout(sayTimer);
      queue = [];
      sayBusy = false;
      statusLine.textContent = "";
      return;
    }
    var text = texts[key] || "";
    if (text && queue[queue.length - 1] !== text) queue.push(text);
    pump();
  }

  // ---- go: the one way the state changes -------------------------------------------------------
  // `patch` holds fields of the state and, besides them, `say` (the key of the one sentence for
  // the live region, "" to clear it) and `focus` ("denied" for the panel of a refused device,
  // "state" or, on a change of screen, nothing for the new heading, false for none). Returns
  // whether the move was allowed.
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
    render();
    if (sentence !== undefined) say(sentence);
    var target = null;
    if (focus === "denied") {
      target = $('[data-call-denied="' + app.deniedKind(state.refused) + '"] h2');
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
    say("");
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
  function show(video, stream) {
    if (video.srcObject !== stream) video.srcObject = stream;
  }

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
    selfVideos.forEach(function (video) {
      var here = video.dataset.callSelf === screen;
      video.hidden = !(here && state.live.camera && app.streams.self);
      show(video, here ? app.streams.self : null);
    });
    show(remoteVideo, screen === "call" ? app.streams.remote : null);
    show(remoteAudio, screen === "call" ? app.streams.audio : null);
    $("[data-call-remote-off]").hidden = state.remote === "live";
    $("[data-call-hear]").hidden = !(screen === "call" && state.soundBlocked);
  }

  // The other side's picture and sound play. A browser that refuses (it wants a tap first) is
  // answered with a button and one message; the button calls this again.
  var playing = false; // one try at a time; a stream that comes meanwhile is tried after it
  var again = false;
  var pressed = false; // the participant pressed the button: focus has to land somewhere
  function playRemote() {
    if (state.screen !== "call") return Promise.resolve();
    if (playing) {
      again = true;
      return Promise.resolve();
    }
    playing = true;
    var plays = [remoteAudio, remoteVideo].map(function (element) {
      if (!element.srcObject) return Promise.resolve(true);
      return Promise.resolve(element.play()).then(
        function () {
          return true;
        },
        function (error) {
          return !(error && error.name === "NotAllowedError");
        },
      );
    });
    return Promise.all(plays).then(function (played) {
      playing = false;
      var blocked = played.indexOf(false) !== -1;
      // Said once per blocked start. When the participant's press makes the button go, focus goes
      // to the heading, not nowhere; when the sound starts on its own, focus stays put.
      if (state.screen === "call" && blocked !== state.soundBlocked) {
        go(
          "call",
          blocked
            ? { soundBlocked: true, say: "say_sound_blocked" }
            : { soundBlocked: false, focus: pressed ? "state" : undefined },
        );
      }
      if (!again) {
        pressed = false;
        return;
      }
      again = false;
      return playRemote();
    });
  }
  app.playRemote = playRemote;

  // ---- the other side's picture ------------------------------------------------------------------
  // The other side's picture, and whether it moves: waiting until it has moved once, then live
  // or off. A change after that is said once.
  function syncRemote(playing) {
    var tracks = app.streams.remote ? app.streams.remote.getVideoTracks() : [];
    var moving =
      playing ||
      tracks.some(function (track) {
        return !track.muted && track.readyState === "live";
      });
    var before = state.remote;
    var after = moving ? "live" : before === "live" || before === "off" ? "off" : "waiting";
    var patch = { remote: after };
    if (state.screen === "call" && before !== after) {
      if (before === "live" && after === "off") patch.say = "say_remote_off";
      if (before === "off" && after === "live") patch.say = "say_remote_on";
    }
    go(state.screen, patch);
  }

  function setRemote(kind, stream) {
    app.streams[kind] = stream;
    if (kind === "audio" && app.devices.speaker() && remoteAudio.setSinkId) {
      remoteAudio.setSinkId(app.devices.speaker()).catch(function () {});
    }
    if (kind === "remote" && stream) {
      stream.getVideoTracks().forEach(function (track) {
        track.addEventListener("mute", function () {
          syncRemote(false);
        });
        track.addEventListener("unmute", function () {
          syncRemote(false);
        });
      });
    }
    syncRemote(false);
    playRemote();
  }
  app.setRemote = setRemote;

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

  app.devices.init();
  app.connection.init();

  $$("[data-call-browser]").forEach(function (step) {
    step.hidden = step.dataset.callBrowser !== (safari ? "safari" : "other");
  });
  $$("[data-call-retry]").forEach(function (button) {
    button.addEventListener("click", app.devices.acquire);
  });
  beginButton.addEventListener("click", app.devices.begin);
  joinButton.addEventListener("click", join);
  $("[data-call-test-sound]").addEventListener("click", app.devices.playTestSound);
  $$("[data-call-leave]").forEach(function (button) {
    button.addEventListener("click", app.connection.leave);
  });
  $("[data-call-again]").addEventListener("click", app.connection.again);
  $("[data-call-hear]").addEventListener("click", function () {
    pressed = true;
    playRemote();
  });
  remoteVideo.addEventListener("playing", function () {
    if (state.screen === "call") syncRemote(true);
  });
  KINDS.forEach(function (kind) {
    toggles[kind].forEach(function (button) {
      button.addEventListener("click", function () {
        toggle(kind);
      });
    });
    $('[data-call-select="' + kind + '"]').addEventListener("change", function (event) {
      app.devices.choose(kind, event.target.value);
    });
  });
  $('[data-call-select="speaker"]').addEventListener("change", function (event) {
    app.devices.choose("speaker", event.target.value);
  });
  media.addEventListener("devicechange", function () {
    if (state.screen === "check") app.devices.list();
  });
  window.addEventListener("pagehide", leavePage);
  window.addEventListener("pageshow", pageBack);

  // The browser can run the call: the sentence that says what it needs is no longer true.
  $("[data-call-needs]").hidden = true;
  beginButton.hidden = false;
})();

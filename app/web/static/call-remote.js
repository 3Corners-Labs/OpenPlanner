/*
 * The other side's picture, sound and screen on the participant's call page (ADR 0123 steps 4c, 5a
 * and 5b).
 *
 * Owns: the other side's streams as the page keeps them (`app.streams`): their picture and sound
 * (whether the picture moves, said once when it stops and starts; playing the sound, and the button
 * that asks for a tap when the browser refuses) and, when the staff member shares a screen, that
 * screen, which takes the place of their picture. One share in a meeting: the staff member's start
 * ends the participant's own share through `app.callMedia.endShare`, and one sentence says both.
 *
 * Never: it makes no request, loads no file and stores nothing. It changes what the page shows only
 * through `app.go`, and it reads nothing a stream or an event carries but the stream itself. It runs
 * only when asked.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});
  var remoteAudio = null;
  var remoteVideo = null;
  var tries = 0;
  var pressed = false; // the participant pressed the button: focus has to land somewhere

  // The other side's picture and sound play. A browser that refuses (it wants a tap first) is
  // answered with a button and one message. Every call starts a try at once, so a press calls
  // `play()` inside the press itself, which is what a browser that wants a tap allows; only the
  // latest try's outcome is applied, so a `play()` that never settles blocks nothing.
  function play() {
    var state = app.state;
    if (state.screen !== "call") return Promise.resolve();
    var mine = (tries += 1);
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
      if (mine !== tries) return;
      var blocked = played.indexOf(false) !== -1;
      // Said once per blocked start. When the participant's press makes the button go, focus goes
      // to the heading, not nowhere; when the sound starts on its own, focus stays put.
      if (state.screen === "call" && blocked !== state.soundBlocked) {
        app.go(
          "call",
          blocked
            ? { soundBlocked: true, say: "say_sound_blocked" }
            : { soundBlocked: false, focus: pressed ? "state" : undefined },
        );
      }
      pressed = false;
    });
  }

  // The other side's picture, and whether it moves: waiting until it has moved once, then live
  // or off. A change after that is said once. The track decides: it moves when a video track of
  // the stream is live and not muted. Events only ask for a new look (the video element's
  // `playing` arrives after a mute in some browsers and must not turn it back to live).
  function sync() {
    var state = app.state;
    var tracks = app.streams.remote ? app.streams.remote.getVideoTracks() : [];
    var moving = tracks.some(function (track) {
      return !track.muted && track.readyState === "live";
    });
    var before = state.remote;
    var after = moving ? "live" : before === "live" || before === "off" ? "off" : "waiting";
    var patch = { remote: after };
    if (state.screen === "call" && before !== after) {
      if (before === "live" && after === "off") patch.say = "say_remote_off";
      if (before === "off" && after === "live") patch.say = "say_remote_on";
    }
    app.go(state.screen, patch);
  }

  function set(kind, stream) {
    app.streams[kind] = stream;
    if (kind === "audio" && app.devices.speaker() && remoteAudio.setSinkId) {
      remoteAudio.setSinkId(app.devices.speaker()).catch(function () {});
    }
    if (kind === "remote" && stream) {
      stream.getVideoTracks().forEach(function (track) {
        track.addEventListener("mute", sync);
        track.addEventListener("unmute", sync);
      });
    }
    sync();
    play();
  }

  // The sound button: the press itself calls `play()`.
  function hear() {
    pressed = true;
    play();
  }

  // The staff member's screen as the meeting hands it out. It may come just before the event that
  // their share began, so it waits as `pending`, and only a stream that is still running is used
  // when their share begins; nothing is kept while their share is off, and `offered(null)` (the
  // end of the call) drops it. A share that began before the participant was let in is said when the
  // call opens.
  var pending = null;
  function offered(stream) {
    pending = stream && stream.active !== false ? stream : null;
    if (pending && app.state.share === "theirs") app.streams.share = pending;
  }

  // The staff member starts or stops sharing their screen. One share in a meeting: the
  // participant's own ends first, and the one sentence says both.
  function staffShare(on) {
    var state = app.state;
    if (on === (state.share === "theirs")) return;
    var said = state.screen === "call";
    var stream = pending && pending.active !== false ? pending : null;
    pending = null;
    if (!on) {
      app.streams.share = null;
      app.go(state.screen, { share: "off", say: said ? "say_share_theirs_off" : undefined });
    } else if (app.call.screen) {
      app.streams.share = stream;
      app.callMedia.endShare(app.call.screen, { share: "theirs", say: said ? "say_share_replaced" : undefined });
    } else {
      app.streams.share = stream;
      app.go(state.screen, { share: "theirs", say: said ? "say_share_theirs" : undefined });
    }
  }

  function init() {
    remoteAudio = app.$("[data-call-remote-audio]");
    remoteVideo = app.$("[data-call-remote]");
    app.$("[data-call-hear]").addEventListener("click", hear);
    remoteVideo.addEventListener("playing", function () {
      if (app.state.screen === "call") sync();
    });
  }

  app.remote = { init: init, play: play, set: set, offered: offered, staffShare: staffShare };
})();

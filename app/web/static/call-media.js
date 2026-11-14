/*
 * The camera, the microphone and the screen of the participant's call (ADR 0123 steps 4c, 5a and 5b).
 *
 * Owns: taking the participant's streams out of the SDK (the devices they left on), switching
 * either on or off in the lobby and in the call, and giving a device back when it is off: in the
 * call the stream is taken out of the meeting and stopped, and switching on takes a new one and
 * sends it. Choosing another camera or microphone in the call takes the new one, sends it by the
 * same rule and only then gives the old one back. A press while the media is being added waits
 * until that is done. The screen is a third kind of stream, taken only at a press (the browser's
 * own picker), sent by the same rule, and given back whoever ends the share: the participant, the
 * browser's own control, the staff member (their own share replaces it: one share in a meeting) or
 * the end of the call, always through `endShare`. The staff member's share is handled by call-remote.js.
 *
 * Never: it makes no request, loads no file and stores nothing. It works on the `call` object of
 * call-connection.js (`app.call`, the SDK's client and meeting; that file is loaded first) and
 * changes what the page shows only through `app.go`. It runs only when asked.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});
  var call = app.call; // call-connection.js is loaded first and has made both
  var choice = app.choice; // the chosen device ids; call-devices.js is loaded first and has made it
  var PICTURE = { width: 1280, height: 720 }; // the size the picture is asked for
  var CLOSED = ["NotAllowedError", "AbortError"]; // the picker was closed or refused: no error, only no share

  function createStream(kind) {
    var helpers = call.client.meetings.mediaHelpers;
    if (kind === "screen") return helpers.createDisplayStream();
    if (kind === "microphone") {
      var sound = { echoCancellation: true, noiseSuppression: true };
      if (choice.microphone) sound.deviceId = choice.microphone;
      return helpers.createMicrophoneStream(sound);
    }
    var picture = { width: PICTURE.width, height: PICTURE.height };
    if (choice.camera) picture.deviceId = choice.camera;
    return helpers.createCameraStream(picture);
  }

  function showSelf() {
    app.streams.self = call.camera ? call.camera.outputStream : null;
  }

  // The devices the participant left on. One that cannot be taken is left out, and said. The
  // lobby and an admission that comes at the same moment share one capture, never two.
  function capture() {
    if (call.capturing) return call.capturing;
    var epoch = call.epoch;
    var failed = [];
    call.capturing = Promise.all(
      app.KINDS.map(function (kind) {
        if (!app.state.live[kind] || call[kind]) return null;
        return createStream(kind).then(
          function (stream) {
            if (epoch !== call.epoch) stream.stop();
            else call[kind] = stream;
          },
          function () {
            failed.push(kind);
          },
        );
      }),
    ).then(function () {
      if (epoch !== call.epoch) return;
      call.capturing = null;
      showSelf();
      if (failed.length) {
        var live = Object.assign({}, app.state.live);
        failed.forEach(function (kind) {
          live[kind] = false;
        });
        app.go(app.state.screen, { live: live, say: "say_device_failed" });
      }
    });
    return call.capturing;
  }

  // The toggle shows `on`; `say` is the one sentence for it (the usual one when not given).
  function switched(kind, on, say) {
    var live = Object.assign({}, app.state.live);
    live[kind] = on;
    app.go(app.state.screen, { live: live, say: say || "say_" + kind + (on ? "_on" : "_off") });
  }

  // A press or a choice while the devices are being taken, the media is being added or this device
  // is being made or taken out waits for that and then runs `again`; it is dropped if the call
  // ended meanwhile. Returns that waiting, or null when nothing is going on.
  function whenIdle(kind, again) {
    var epoch = call.epoch;
    var busy = call.adding || call.capturing || call.making[kind];
    if (!busy) return null;
    var replay = function () {
      return epoch === call.epoch ? again() : undefined;
    };
    return busy.then(replay, replay);
  }

  // One device of the call switched on or off. Off gives the device back, as in the check: in
  // the call the stream is taken out of the meeting and stopped (the camera light goes out), and
  // switching on takes a new one and sends it. If the SDK refuses to take it out, it is only
  // muted and kept, and the page says so (it is off for the other side, but the browser may still
  // show the device as in use). Before the media is in (the lobby) the device is simply stopped or
  // taken. One rule for sending: a new stream is sent when it arrives and the media is in (after
  // an adding that is under way, unless that adding took it along); never earlier, never twice.
  // A press waits (`whenIdle`). A stream that cannot be sent is stopped again and the page says
  // that it did not work. Taking a device out of the meeting counts as making it, so a choice of
  // another device made meanwhile waits for it.
  function setLive(kind, on) {
    var epoch = call.epoch;
    var waiting = whenIdle(kind, function () {
      return setLive(kind, on);
    });
    if (waiting) return waiting;
    var stream = call[kind];
    var drop = function () {
      if (epoch !== call.epoch) return;
      stream.stop();
      call[kind] = null;
      showSelf();
      switched(kind, false);
    };
    var mute = function () {
      if (epoch !== call.epoch) return;
      stream.setUserMuted(true);
      switched(kind, false, "say_device_kept");
    };
    if (!on) {
      if (!stream) switched(kind, false);
      else if (app.state.media === "added") {
        // Under the time of an attempt: a refusal and a silence are both "kept, muted".
        return track(kind, app.within(call.meeting.unpublishStreams([stream])).then(drop, mute));
      } else drop();
      return Promise.resolve();
    }
    if (stream) {
      stream.setUserMuted(false);
      switched(kind, true);
      return Promise.resolve();
    }
    var said = function () {
      if (epoch === call.epoch) app.go(app.state.screen, { say: "say_device_failed" });
    };
    var making = createStream(kind).then(function (created) {
      if (epoch !== call.epoch) return created.stop();
      call[kind] = created;
      showSelf();
      return send(kind, created, epoch)
        .then(function () {
          if (epoch === call.epoch && call[kind] === created) switched(kind, true);
        })
        .then(null, function () {
          if (epoch !== call.epoch) return;
          created.stop();
          if (call[kind] === created) call[kind] = null;
          showSelf();
          said();
        });
    }, said);
    return track(kind, making);
  }

  // Another camera or microphone, chosen in the panel of devices in the call. It is taken first
  // and sent by the one rule above; only when that worked is the old one given back (stopped) and
  // the choice kept. A device that cannot be taken or sent is stopped again, the old one stays
  // and the choice is as it was. A device that is off only has its choice noted. Resolves true
  // (changed), false (the old one stays) or undefined (the call ended meanwhile). A press while a
  // device is being made, taken out or the media added waits, like a toggle, and looks again
  // afterwards: if the device went off or is no longer the stream this started with, the new
  // stream is stopped and nothing changes.
  function change(kind, id) {
    var epoch = call.epoch;
    var waiting = whenIdle(kind, function () {
      return change(kind, id);
    });
    if (waiting) return waiting;
    var previous = choice[kind];
    var old = call[kind];
    choice[kind] = id;
    if (!app.state.live[kind] || !old) return Promise.resolve(true);
    var kept = function () {
      choice[kind] = previous;
      return epoch === call.epoch ? false : undefined;
    };
    var changing = createStream(kind).then(function (created) {
      if (epoch !== call.epoch) return created.stop();
      return send(kind, created, epoch).then(
        function () {
          if (epoch !== call.epoch) return created.stop();
          if (call[kind] !== old || !app.state.live[kind]) {
            // Already sent: taken out of the meeting first (as a toggle that goes off does), then stopped.
            return giveBack(created).then(kept);
          }
          call[kind] = created;
          old.stop();
          showSelf();
          return true;
        },
        function () {
          created.stop();
          return kept();
        },
      );
    }, kept);
    return track(kind, changing);
  }

  // A stream that was sent and is no longer wanted: out of the meeting, then stopped.
  function giveBack(created) {
    var stop = function () {
      created.stop();
    };
    if (app.state.media !== "added" || !call.meeting) {
      stop();
      return Promise.resolve();
    }
    return app.within(call.meeting.unpublishStreams([created])).then(stop, stop);
  }

  // The one rule for sending a new stream: when it arrives and the media is in (after an adding
  // that is under way, unless that adding took it along); never earlier, never twice.
  function send(kind, created, epoch) {
    var settled = function () {};
    return (call.adding || Promise.resolve()).then(settled, settled).then(function () {
      if (epoch !== call.epoch) return null;
      var taken = call.sent && call.sent[kind] === created;
      var published = {};
      if (kind === "screen") published.screenShare = { video: created };
      else published[kind] = created;
      return app.state.media === "added" && !taken ? app.within(call.meeting.publishStreams(published)) : null;
    });
  }

  // ---- the participant's screen -----------------------------------------------------------------
  // The one place a share ends, whoever ends it: our button, the browser's own control, the
  // meeting, the staff member sharing theirs (`patch.share` is then "theirs"), a failed sending or
  // the end of the call (`patch` null: the meeting is left anyway, only the capture is given back).
  // The capture is stopped first, so the browser's own sign that the screen is shared goes out at
  // once; taking the stream out of the meeting follows within the time of an attempt, and a refusal
  // changes nothing: it is stopped either way, also when it was only half sent. Only the stream
  // that is the current share ends it, so a second call does nothing.
  function endShare(display, patch) {
    if (!display || call.screen !== display) return;
    call.screen = null;
    display.stop();
    if (patch && app.state.media === "added" && call.meeting) {
      app.within(call.meeting.unpublishStreams([display])).then(null, function () {});
    }
    if (patch) app.go(app.state.screen, Object.assign({ share: "off" }, patch));
  }

  // The browser's own control, or the meeting, ends the share without our button. The track's
  // `ended` is the browser's; the stream's own `stream-ended` is the prototype's way, kept next to it.
  function watch(display, epoch) {
    var ended = function () {
      if (epoch === call.epoch) endShare(display, { say: "say_share_off" });
    };
    var track = display.outputStream && display.outputStream.getVideoTracks()[0];
    if (track) track.addEventListener("ended", ended);
    if (typeof display.on === "function") display.on("stream-ended", ended);
  }

  // The participant's press. Only in the call, only if the meeting allows it and never twice: a
  // press while a share exists or the picker is open does nothing. A closed picker is no error;
  // any failure after the picture was given (watching it, sending it) stops it and says so.
  function share(on) {
    var state = app.state;
    var epoch = call.epoch;
    if (state.screen !== "call") return Promise.resolve();
    if (!on) {
      endShare(call.screen, { say: "say_share_off" });
      return Promise.resolve();
    }
    if (state.share !== "off" || !state.can.share) return Promise.resolve();
    var waiting = whenIdle("screen", function () {
      return share(true);
    });
    if (waiting) return waiting;
    app.go("call", { share: "asking" });
    var picked = null;
    var making = Promise.resolve()
      .then(function () {
        return createStream("screen");
      })
      .then(function (display) {
        picked = display;
        // The call ended, or the staff member began to share while the picker was open: give it back.
        if (epoch !== call.epoch || state.share !== "asking") return display.stop();
        call.screen = display;
        watch(display, epoch);
        return send("screen", display, epoch).then(function () {
          if (epoch === call.epoch && call.screen === display) {
            app.go("call", { share: "mine", say: "say_share_mine" });
          }
        });
      })
      .then(null, function (error) {
        if (picked && epoch === call.epoch && call.screen === picked) {
          return endShare(picked, { say: "say_share_failed" });
        }
        if (picked) picked.stop();
        if (epoch !== call.epoch || state.share !== "asking") return;
        var closed = !picked && Boolean(error) && CLOSED.indexOf(error.name) !== -1;
        app.go("call", closed ? { share: "off" } : { share: "off", say: "say_share_failed" });
      });
    return track("screen", making);
  }

  // A device being made is a promise that a press of its toggle, or a choice, waits for.
  function track(kind, making) {
    var done = function () {
      if (call.making[kind] === making) call.making[kind] = null;
    };
    call.making[kind] = making;
    making.then(done, done);
    return making;
  }

  app.callMedia = {
    capture: capture,
    setLive: setLive,
    change: change,
    share: share,
    endShare: endShare,
  };
})();

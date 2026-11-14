/*
 * The camera and the microphone of the participant's call (ADR 0123 steps 4c and 5).
 *
 * Owns: taking the participant's streams out of the SDK (the devices they left on), switching
 * either on or off in the lobby and in the call, and giving a device back when it is off: in the
 * call the stream is taken out of the meeting and stopped, and switching on takes a new one and
 * sends it. A press while the media is being added waits until that is done.
 *
 * Never: it makes no request, loads no file and stores nothing. It works on the `call` object of
 * call-connection.js (`app.call`, the SDK's client and meeting; that file is loaded first) and
 * changes what the page shows only through `app.go`. It runs only when asked.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});
  var call = app.call; // call-connection.js is loaded first and has made both
  var choice = app.choice;
  var PICTURE = { width: 1280, height: 720 }; // the size the picture is asked for

  function createStream(kind) {
    var helpers = call.client.meetings.mediaHelpers;
    if (kind === "microphone") {
      var sound = { echoCancellation: true, noiseSuppression: true };
      if (choice.microphoneId) sound.deviceId = choice.microphoneId;
      return helpers.createMicrophoneStream(sound);
    }
    var picture = { width: PICTURE.width, height: PICTURE.height };
    if (choice.cameraId) picture.deviceId = choice.cameraId;
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

  // One device of the call switched on or off. Off gives the device back, as in the check: in
  // the call the stream is taken out of the meeting and stopped (the camera light goes out), and
  // switching on takes a new one and sends it. If the SDK refuses to take it out, it is only
  // muted and kept, and the page says so (it is off for the other side, but the browser may still
  // show the device as in use). Before the media is in (the lobby) the device is simply stopped or
  // taken. One rule for sending: a new stream is sent when it arrives and the media is in (after
  // an adding that is under way, unless that adding took it along); never earlier, never twice.
  // A press while the devices are being taken, the media is being added or this device is being
  // made waits for that, and is dropped if the call ended meanwhile. A stream that cannot be sent
  // is stopped again and the page says that it did not work.
  function setLive(kind, on) {
    var epoch = call.epoch;
    var busy = call.adding || call.capturing || call.making[kind];
    if (busy) {
      var replay = function () {
        return epoch === call.epoch ? setLive(kind, on) : undefined;
      };
      return busy.then(replay, replay);
    }
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
      else if (app.state.media === "added") return call.meeting.unpublishStreams([stream]).then(drop, mute);
      else drop();
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
      var settled = function () {};
      return (call.adding || Promise.resolve())
        .then(settled, settled)
        .then(function () {
          if (epoch !== call.epoch) return null;
          var taken = call.sent && call.sent[kind] === created;
          var published = {};
          published[kind] = created;
          return app.state.media === "added" && !taken ? call.meeting.publishStreams(published) : null;
        })
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
    var done = function () {
      if (call.making[kind] === making) call.making[kind] = null;
    };
    call.making[kind] = making;
    making.then(done, done);
    return making;
  }

  app.callMedia = { capture: capture, setLive: setLive };
})();

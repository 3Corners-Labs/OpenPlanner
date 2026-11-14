/*
 * The call of the participant's call page (ADR 0123 steps 4c and 5).
 *
 * Owns: everything that follows the click that joins. It loads the video SDK file (with its checksum as
 * integrity), asks our server for a guest token, initialises the SDK, creates the meeting out of the
 * guest link and joins, in attempts that are given up when they take too long and retried by
 * themselves; a failed attempt leaves the meeting and the registration first. It waits in the
 * lobby, adds the participant's picture and sound when they are let in, switches their camera and
 * microphone on and off, and leaves.
 *
 * Never: it makes one request, the guest token of this booking, to the address the server put in
 * the markup, and loads one file, the SDK. The token and the link live in the `call` object in
 * memory, are forgotten when the call ends or an attempt fails, and are never stored, written into
 * the page, put in an address or logged. It changes what the page shows only through `app.go`.
 * It runs only after call-page.js has started it with `init`.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});
  var KINDS = ["camera", "microphone"];

  // What the SDK is told: its logs are never uploaded and the registration of this browser is
  // temporary (ADR 0123 point 16). The automatic link to the real-time data service is off: it
  // carries captions, which this step does not show (step 5 turns it on when it builds them), and
  // nothing in the prototype's notes needs it to join. The first real meeting must confirm that.
  var SDK_CONFIG = {
    logger: { level: "warn" },
    device: { ephemeral: true },
    meetings: {
      reconnection: { enabled: true },
      enableRtx: true,
      enableAutomaticLLM: false,
      autoUploadLogs: false,
    },
  };
  var PICTURE = { width: 1280, height: 720 }; // the size the picture is asked for
  var TOKEN_MARGIN_MS = 60000; // a token that expires within this is not reused
  var GIVE_UP_MS = 5000; // how long leaving and unregistering may take before the page goes on
  var STALE = { stale: true }; // a step of a connection that something else has taken over

  var tokenUrl = "", sdkSource = "", sdkIntegrity = ""; // read out of the markup in `init`
  var attemptsMax = 0, attemptMs = 0;
  var choice = { cameraId: "", microphoneId: "" };

  // `epoch` moves whenever a connection starts, ends or is taken over; every step and every
  // event of the SDK checks it, so nothing of an old attempt reaches the page. The token is
  // kept here, in memory, until the call ends or an attempt fails.
  var call = { epoch: 0, token: "", link: "", expires: 0, loading: null, abort: null };
  call.client = call.meeting = call.camera = call.microphone = call.capturing = null;
  call.joined = false;
  call.making = { camera: false, microphone: false };

  function noop() {}

  function turnedAway(reason) {
    return { refusal: reason };
  }

  function check(epoch) {
    if (epoch !== call.epoch) throw STALE;
  }

  // One step is given the time of one attempt.
  function within(promise) {
    return new Promise(function (resolve, reject) {
      var timer = window.setTimeout(function () {
        reject(new Error("timeout"));
      }, attemptMs);
      promise.then(
        function (value) {
          window.clearTimeout(timer);
          resolve(value);
        },
        function (error) {
          window.clearTimeout(timer);
          reject(error);
        },
      );
    });
  }

  function giveUp(promise) {
    var limit = new Promise(function (resolve) {
      window.setTimeout(resolve, GIVE_UP_MS);
    });
    return Promise.race([promise.then(noop, noop), limit]);
  }

  function forget() {
    call.token = call.link = "";
    call.expires = 0;
  }

  // The one place that names what the SDK file puts on the window.
  function sdkGlobal() {
    return window.Webex;
  }

  // The SDK file, with its checksum as integrity: the browser refuses a file that differs.
  function loadSdk() {
    if (sdkGlobal()) return Promise.resolve();
    if (!call.loading) {
      call.loading = new Promise(function (resolve, reject) {
        var script = document.createElement("script");
        var failed = function () {
          script.remove();
          call.loading = null;
          reject(new Error("sdk"));
        };
        script.src = sdkSource;
        script.integrity = sdkIntegrity;
        script.crossOrigin = "anonymous";
        script.onload = function () {
          if (sdkGlobal()) resolve();
          else failed();
        };
        script.onerror = failed;
        document.head.appendChild(script);
      });
    }
    return call.loading;
  }

  // The guest token and the meeting's guest link, by this server; reused while it is valid.
  // The answer belongs to its attempt (it is dropped when the attempt is over), the request is
  // given the time of an attempt, and leaving or giving up cancels it.
  function authorise(epoch) {
    if (call.token && call.expires - Date.now() > TOKEN_MARGIN_MS) return Promise.resolve();
    var controller = new AbortController();
    var timer = window.setTimeout(controller.abort.bind(controller), attemptMs);
    call.abort = controller;
    var settle = function () {
      window.clearTimeout(timer);
      if (call.abort === controller) call.abort = null;
    };
    var request = { method: "POST", credentials: "same-origin", cache: "no-store" };
    request.signal = controller.signal;
    return window
      .fetch(tokenUrl, request)
      .then(function (response) {
        var refused = { 409: "not_open", 429: "rate_limited", 503: "unavailable" }[response.status];
        if (refused) throw turnedAway(refused);
        if (!response.ok) throw turnedAway("generic");
        return response.json();
      })
      .then(function (body) {
        check(epoch);
        var expires = Date.parse(body && body.expires_at);
        if (!body || !body.token || !body.link || isNaN(expires)) throw turnedAway("generic");
        call.token = body.token;
        call.link = body.link;
        call.expires = expires;
        settle();
      })
      .then(null, function (error) {
        settle();
        if (error === STALE || (error && error.refusal)) throw error;
        if (epoch !== call.epoch) throw STALE;
        // Too slow is a failed attempt, like the SDK being slow; anything else is plain failure.
        throw controller.signal.aborted ? new Error("timeout") : turnedAway("generic");
      });
  }

  // ---- the devices of the call ---------------------------------------------------------------
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
      KINDS.map(function (kind) {
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

  function switched(kind, on) {
    var live = Object.assign({}, app.state.live);
    live[kind] = on;
    app.go(app.state.screen, { live: live, say: "say_" + kind + (on ? "_on" : "_off") });
  }

  // One device of the call switched on or off. Off gives the device back, as in the check: in
  // the call the stream is taken out of the meeting and stopped (the camera light goes out), and
  // switching on takes a new one and sends it. If the SDK refuses to take it out, it is only
  // muted and kept. Before the media is in (the lobby) the device is simply stopped or taken.
  function setLive(kind, on) {
    var epoch = call.epoch;
    var stream = call[kind];
    var sent = app.state.media === "added";
    var drop = function () {
      stream.stop();
      call[kind] = null;
      showSelf();
      switched(kind, false);
    };
    var mute = function () {
      stream.setUserMuted(true);
      switched(kind, false);
    };
    if (!on) {
      if (!stream) switched(kind, false);
      else if (sent) return call.meeting.unpublishStreams([stream]).then(drop, mute);
      else drop();
      return Promise.resolve();
    }
    if (stream) {
      stream.setUserMuted(false);
      switched(kind, true);
      return Promise.resolve();
    }
    if (call.making[kind]) return Promise.resolve();
    call.making[kind] = true;
    return createStream(kind)
      .then(function (created) {
        call.making[kind] = false;
        if (epoch !== call.epoch) return created.stop();
        call[kind] = created;
        showSelf();
        var published = {};
        published[kind] = created;
        return sent ? call.meeting.publishStreams(published) : null;
      })
      .then(
        function () {
          if (epoch === call.epoch && call[kind]) switched(kind, true);
        },
        function () {
          call.making[kind] = false;
          app.go(app.state.screen, { say: "say_device_failed" });
        },
      );
  }

  // ---- the attempts ----------------------------------------------------------------------------
  // The lobby: the participant is in the meeting and waits to be let in; the own picture shows.
  function lobby(epoch) {
    return capture().then(function () {
      check(epoch);
      app.go("lobby", { say: "say_waiting" });
    });
  }

  // Admitted, or never held: the media goes in and the first moment of the call is shown.
  function enter(epoch) {
    if (app.state.media !== "none") return Promise.resolve();
    app.go(app.state.screen, { media: "adding" });
    return capture()
      .then(function () {
        check(epoch);
        var local = {};
        if (call.microphone) local.microphone = call.microphone;
        if (call.camera) local.camera = call.camera;
        return within(call.meeting.addMedia({ localStreams: local }));
      })
      .then(function () {
        check(epoch);
        app.go("call", { media: "added", say: "say_admitted" });
        app.playRemote();
      });
  }

  // What the meeting tells the page, for this connection only.
  function follow(epoch) {
    var client = call.client;
    var meeting = call.meeting;
    meeting.on("meeting:self:guestAdmitted", function () {
      if (epoch !== call.epoch) return;
      enter(epoch).then(null, function () {
        if (epoch === call.epoch) fail("generic");
      });
    });
    meeting.on("media:ready", function (media) {
      if (epoch !== call.epoch) return;
      if (media.type === "remoteVideo") app.setRemote("remote", media.stream);
      if (media.type === "remoteAudio") app.setRemote("audio", media.stream);
    });
    client.meetings.on("meeting:removed", function () {
      var screen = app.state.screen;
      if (epoch === call.epoch && (screen === "lobby" || screen === "call")) finish("ended");
    });
  }

  // A step that answers after its attempt was given up is undone when it arrives: a late
  // registration is unregistered, a late join leaves the meeting.
  function undoLate(promise, epoch, undo) {
    promise.then(function () {
      if (epoch !== call.epoch) undo();
    }, noop);
    return promise;
  }

  // One attempt: the SDK file first (a file the browser refuses must not cost a token), then the
  // token, the SDK's registration, the meeting out of the guest link, joining.
  function setUp(epoch) {
    var client;
    return within(loadSdk())
      .then(function () {
        check(epoch);
        return authorise(epoch);
      })
      .then(function () {
        check(epoch);
        client = call.client = sdkGlobal().init({
          config: SDK_CONFIG,
          credentials: { access_token: call.token },
        });
        if (client.ready) return null;
        return within(
          new Promise(function (resolve) {
            client.once("ready", resolve);
          }),
        );
      })
      .then(function () {
        check(epoch);
        var registering = undoLate(client.meetings.register(), epoch, function () {
          client.meetings.unregister().then(noop, noop);
        });
        return within(registering);
      })
      .then(function () {
        check(epoch);
        return within(client.meetings.create(call.link));
      })
      .then(function (meeting) {
        check(epoch);
        call.meeting = meeting;
        follow(epoch);
        var joining = undoLate(meeting.join(), epoch, function () {
          meeting.leave().then(noop, noop);
        });
        return within(joining);
      })
      .then(function () {
        check(epoch);
        call.joined = true;
        return call.meeting.isUserUnadmitted ? lobby(epoch) : enter(epoch);
      });
  }

  // Leaves the meeting if it was joined, unregisters, and gives every stream back; resolves when
  // done or after GIVE_UP_MS each. Whatever answers late is undone by `undoLate`.
  function abandon() {
    var client = call.client;
    var meeting = call.meeting;
    var joined = call.joined;
    [call.microphone, call.camera].forEach(function (stream) {
      if (stream) stream.stop();
    });
    call.microphone = null;
    call.camera = null;
    call.client = null;
    call.meeting = null;
    call.joined = false;
    call.capturing = null;
    call.epoch += 1;
    if (call.abort) {
      call.abort.abort();
      call.abort = null;
    }
    app.streams.self = null;
    app.streams.remote = null;
    app.streams.audio = null;
    var leaving = joined && meeting ? giveUp(meeting.leave()) : Promise.resolve();
    return leaving.then(function () {
      if (client && client.meetings && client.meetings.registered) {
        return giveUp(client.meetings.unregister());
      }
      return null;
    });
  }

  function attempt(number) {
    var epoch = (call.epoch += 1);
    // The count is shown for every retry; it is said once, at the first.
    if (number > 1) {
      var retry = { attempt: number, media: "none" };
      if (number === 2) retry.say = "say_retry";
      app.go("connecting", retry);
    }
    setUp(epoch).then(null, function (error) {
      if (error === STALE || epoch !== call.epoch) return null;
      if (error && error.refusal) return fail(error.refusal);
      // A token the platform did not take is not asked for again with the same value.
      forget();
      return abandon().then(function () {
        if (number < attemptsMax) attempt(number + 1);
        else fail("attempts");
      });
    });
  }

  function fail(reason) {
    forget();
    abandon();
    app.go("failed", { failure: reason, media: "none", say: "say_failed_" + reason });
  }

  // The call is over, by the participant's own leaving ("left") or the meeting's end ("ended").
  function finish(kind) {
    forget();
    abandon();
    app.go(kind, { media: "none", say: kind === "left" ? "say_left" : "say_ended" });
  }

  // ---- what the page asks of the call ---------------------------------------------------------
  function start(picked) {
    if (picked) {
      choice.cameraId = picked.cameraId;
      choice.microphoneId = picked.microphoneId;
    }
    app.go("connecting", {
      attempt: 1,
      failure: "",
      media: "none",
      remote: "unknown",
      soundBlocked: false,
      live: picked ? { camera: picked.camera, microphone: picked.microphone } : app.state.live,
      say: "say_connecting",
    });
    attempt(1);
  }

  function again() {
    start(null);
  }

  function leave() {
    finish("left");
  }

  // The page goes away or comes back out of the browser's memory: nothing of a call stays.
  function hangUp() {
    forget();
    abandon();
  }

  function init() {
    var root = app.root;
    tokenUrl = root.dataset.callTokenUrl || "";
    sdkSource = root.dataset.callSdk || "";
    sdkIntegrity = root.dataset.callSdkIntegrity || "";
    attemptsMax = parseInt(root.dataset.callAttempts, 10) || 0;
    attemptMs = (parseInt(root.dataset.callSeconds, 10) || 0) * 1000;
    // Joining needs all of these: the server leaves the SDK out when the file is not whole.
    app.connection.joinable = Boolean(tokenUrl && sdkSource && sdkIntegrity && attemptsMax && attemptMs);
  }

  app.connection = {
    joinable: false,
    init: init,
    start: start,
    again: again,
    leave: leave,
    hangUp: hangUp,
    setLive: setLive,
  };
})();

/*
 * The call of the participant's call page (ADR 0123 steps 4c and 5a).
 *
 * Owns: everything that follows the click that joins, except the participant's own devices
 * (call-media.js). It loads the video SDK file (with its checksum as integrity), asks our server
 * for a guest token, initialises the SDK, creates the meeting out of the guest link and joins, in
 * attempts that are given up when they take too long and retried by themselves; a failed attempt
 * leaves the meeting and the registration first. It waits in the lobby, adds the participant's
 * picture and sound when they are let in, follows the meeting's end, and leaves. A file the
 * browser refuses or that fails to load ends the joining as "not available in this page"; a file
 * that never answers is a failed attempt like any other and, after the last, "we tried N times".
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
  var TOKEN_MARGIN_MS = 60000; // a token that expires within this is not reused
  var GIVE_UP_MS = 5000; // how long leaving and unregistering may take before the page goes on
  var STALE = { stale: true }; // a step of a connection that something else has taken over

  var tokenUrl = "", sdkSource = "", sdkIntegrity = ""; // read out of the markup in `init`
  var attemptsMax = 0, attemptMs = 0;

  // `epoch` moves whenever a connection starts, ends or is taken over; every step and every
  // event of the SDK checks it, so nothing of an old attempt reaches the page. The token is kept
  // here, in memory: one token per click, reused by every attempt of that joining while it is
  // valid, and forgotten when the joining ends (the call's end, leaving, the failure state) or
  // when the platform refuses it at registration. Forgetting a token in the page does not
  // withdraw it, and a new one for every attempt would spend three of the booking's ten an hour
  // on one failed click; "try again" asks a new one.
  var call = { epoch: 0, token: "", link: "", expires: 0, loading: null, abort: null };
  call.client = call.meeting = call.camera = call.microphone = call.capturing = null;
  call.joined = false;
  call.making = { camera: null, microphone: null }; // the device being made, as a promise
  call.sent = null; // the streams that addMedia took along
  call.adding = null; // the media is being added: a device press waits for it
  app.call = call; // shared with call-media.js, which takes and releases the streams

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

  app.within = within; // for call-media.js: the same time limit for taking a stream out or sending it

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
        // The browser refuses a file that is not the one its checksum names, and it does not say why
        // a file did not load: trying again will not make it better, so this ends the joining. A
        // file that never answers is not this: it times out (`within`) and is a failed attempt.
        var failed = function () {
          script.remove();
          call.loading = null;
          reject({ unavailable: true });
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
        return response.json().then(null, function () {
          throw turnedAway("generic");
        });
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
        // Too slow, or no answer at all, is a failed attempt like the SDK being slow: it is tried
        // again. Only the endpoint's own answers end the joining at once.
        throw new Error(controller.signal.aborted ? "timeout" : "network");
      });
  }

  // ---- the attempts ----------------------------------------------------------------------------
  // The lobby: the participant is in the meeting and waits to be let in; the own picture shows.
  function lobby(epoch) {
    return app.callMedia.capture().then(function () {
      check(epoch);
      app.go("lobby", { say: "say_waiting" });
    });
  }

  // Admitted, or never held: the media goes in and the first moment of the call is shown.
  function enter(epoch) {
    if (app.state.media !== "none") return Promise.resolve();
    app.go(app.state.screen, { media: "adding" });
    var adding = app.callMedia
      .capture()
      .then(function () {
        check(epoch);
        var local = {};
        if (call.microphone) local.microphone = call.microphone;
        if (call.camera) local.camera = call.camera;
        call.sent = local;
        return within(call.meeting.addMedia({ localStreams: local }));
      })
      .then(function () {
        check(epoch);
        app.go("call", { media: "added", say: "say_admitted" });
        app.playRemote();
      });
    // A press of a toggle waits for this (call-media.js).
    var done = function () {
      if (call.adding === adding) call.adding = null;
    };
    call.adding = adding;
    adding.then(done, done);
    return adding;
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
        app.events.follow(client, meeting, epoch);
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
    call.making = { camera: null, microphone: null }; // a device being made or taken out is given up with the call
    call.sent = null;
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

  // The platform refused the token itself: an authorisation error at any step of the chain
  // (registration, creating the meeting, joining). The shape of the real SDK's error (a
  // `statusCode` or a `code`) is a guess until a real meeting shows it; the stand-in uses the same.
  function tokenRefused(error) {
    var code = error && (error.statusCode || error.code);
    return code === 401 || code === 403;
  }

  function attempt(number) {
    var epoch = (call.epoch += 1);
    // The count is shown for every retry; it is said once, at the first.
    if (number > 1) {
      var retry = { attempt: number, media: "none" };
      if (number === 2) retry.say = "say_retry";
      if (!app.go("connecting", retry)) return;
    }
    setUp(epoch).then(null, function (error) {
      if (error === STALE || epoch !== call.epoch) return null;
      if (error && error.unavailable) return unavailable();
      if (error && error.refusal) return fail(error.refusal);
      if (tokenRefused(error)) forget();
      var gone = abandon();
      var mine = call.epoch;
      return gone.then(function () {
        // The page was left or reset while the attempt was being undone: nothing starts again.
        if (mine !== call.epoch) return;
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

  // The SDK file is refused: the page says the call is not available in it, at once.
  function unavailable() {
    forget();
    abandon();
    app.go("unavailable", { media: "none", say: "" });
  }

  // The call is over, by the participant's own leaving ("left") or the meeting's end ("ended").
  function finish(kind) {
    forget();
    abandon();
    app.go(kind, { media: "none", say: kind === "left" ? "say_left" : "say_ended" });
  }

  // ---- what the page asks of the call ---------------------------------------------------------
  function start(picked) {
    var moved = app.go("connecting", {
      attempt: 1,
      failure: "",
      media: "none",
      remote: "unknown",
      soundBlocked: false,
      live: picked ? { camera: picked.camera, microphone: picked.microphone } : app.state.live,
      say: "say_connecting",
    });
    if (moved) attempt(1);
  }

  function again() {
    start(null);
  }

  function leave() {
    finish("left");
  }

  // What the meeting tells the page (call-events.js, which checks the epoch first): the staff
  // member let the participant in (the media goes in; if it cannot, the call fails), the meeting
  // was removed (the staff member ended it), or the connection is lost for good.
  function admitted(epoch) {
    enter(epoch).then(null, function () {
      if (epoch === call.epoch) fail("media");
    });
  }

  function ended() {
    var screen = app.state.screen;
    if (screen === "lobby" || screen === "call") finish("ended");
  }

  function lost() {
    fail("lost");
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
    admitted: admitted,
    ended: ended,
    lost: lost,
    hangUp: hangUp,
    setLive: function (kind, on) {
      return app.callMedia.setLive(kind, on);
    },
  };
})();

/*
 * The participant's call page (ADR 0123 steps 4b and 4c).
 *
 * Does: after the button of the start state it asks the browser for the camera and the
 * microphone, shows the participant their own picture, a level meter and a test sound, lets them
 * choose camera, microphone and speaker and join with either switched off, and explains a refused
 * permission. At the click that joins it asks the server for a guest token, loads the video
 * SDK (only when the server named the file and its checksum in the markup), connects with
 * retries, waits in the lobby and reaches the first moment of the call: the other side's picture
 * and sound, the own picture, the microphone and camera toggles and leaving. Every failure ends
 * in a state with plain words. Every word is the server's own markup ([data-call-texts] and the
 * sections of the page); the script only shows and hides them and sets text with textContent.
 *
 * One `view` says what the page is in (the screen, what was refused or is missing, which devices
 * are on, which attempt, why it failed) and one `render` makes the page show it: every hidden
 * flag, every pressed state, the pictures, the focus and the sentence for the live region.
 * Nothing reads the page to learn what was written. The next steps add screens to that one
 * function.
 *
 * Never: before the click that joins it makes no request of any kind and loads no other file.
 * It makes exactly one kind of request, the guest token of this booking, to the address the
 * server gave, and loads exactly one other file, the SDK with its checksum as integrity; the
 * SDK itself contacts the hosts the page's policy names. The token lives in memory: it is never
 * stored, written into the page, put in an address or logged. It stores nothing, sets no inline
 * style and builds no markup out of text; the picture and the sound stay in this browser except
 * what the call itself sends.
 *
 * Without the script, or in a browser without the needed features, the page keeps the start
 * state with the sentence that says what the call needs.
 */
(function () {
  "use strict";

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

  var texts = {};
  $$("[data-call-texts] [data-text]").forEach(function (node) {
    texts[node.dataset.text] = node.textContent.trim();
  });

  var statusLine = $("[data-call-status]");
  var preview = $("[data-call-preview]");
  var level = $("[data-call-level]");
  var levelText = $("[data-call-level-text]");
  var joinButton = $("[data-call-join]");
  var testAudio = $("[data-call-test-audio]");
  var beginButton = $("[data-call-begin]");
  var selects = {
    camera: $('[data-call-select="camera"]'),
    microphone: $('[data-call-select="microphone"]'),
    speaker: $('[data-call-select="speaker"]'),
  };
  var toggles = {
    camera: $$('[data-call-toggle="camera"]'),
    microphone: $$('[data-call-toggle="microphone"]'),
  };
  var selfVideos = $$("[data-call-self]");
  var remoteVideo = $("[data-call-remote]");
  var remoteAudio = $("[data-call-remote-audio]");
  var tokenUrl = root.dataset.callTokenUrl || "";
  var sdkSource = root.dataset.callSdk || "";
  var sdkIntegrity = root.dataset.callSdkIntegrity || "";
  var attemptsMax = parseInt(root.dataset.callAttempts, 10) || 0;
  var attemptMs = (parseInt(root.dataset.callSeconds, 10) || 0) * 1000;
  // Joining needs all of these: the server leaves the SDK out when the file is not whole.
  var joinable = Boolean(tokenUrl && sdkSource && sdkIntegrity && attemptsMax && attemptMs);
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  var safari =
    /Safari/.test(navigator.userAgent) &&
    !/Chrome|Chromium|Edg|Firefox|FxiOS|CriOS/.test(navigator.userAgent);

  // ---- the view and its one render ------------------------------------------------------------
  var view;
  // The pictures and the sound of the call, shown by `render`. They are streams, not state.
  var streams = { self: null, remote: null, audio: null };
  function fresh() {
    return {
      screen: "start", // start, check, connecting, lobby, call, failed, left, ended or unavailable
      refused: [], // kinds the browser refuses
      missing: [], // kinds with no device
      wish: { camera: true, microphone: true }, // asked for by the participant
      has: { camera: false, microphone: false }, // a live track
      soundPlayed: false,
      speaker: false, // the browser can choose a speaker and there is one to choose
      live: { camera: false, microphone: false }, // on in the lobby or the call
      attempt: 0, // the attempt at connecting; the first is 1
      retrying: false, // an attempt failed and the next one runs
      failure: "", // attempts, generic, not_open, rate_limited or unavailable
      remoteLive: false, // the other side's picture moves
      focus: "", // "state" or "denied": the place of focus in this render
      say: null, // key of the sentence for the live region; "" clears it; null leaves it
    };
  }

  function deniedKind(refused) {
    if (refused.length === 2) return "both";
    return refused.length === 1 ? refused[0] : "";
  }

  function joinLabel(absent) {
    var camera = absent.indexOf("camera") !== -1;
    var microphone = absent.indexOf("microphone") !== -1;
    if (camera && microphone) return texts.join_both;
    if (camera) return texts.join_camera;
    return microphone ? texts.join_microphone : texts.join;
  }

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

  function show(video, stream) {
    if (video.srcObject !== stream) video.srcObject = stream;
  }

  // The parts of the page that begin at the click that joins.
  function renderCall() {
    var inCall = view.screen === "lobby" || view.screen === "call";
    $("[data-call-retry-note]").hidden = !view.retrying;
    var attempt = $("[data-call-attempt]");
    attempt.hidden = view.attempt < 2;
    attempt.textContent = view.attempt < 2 ? "" : texts.attempt.replace("{n}", String(view.attempt));
    $$("[data-call-failure]").forEach(function (note) {
      note.hidden = note.dataset.callFailure !== view.failure;
    });
    // A call that is not open would not open through the other way either: contact details only.
    var otherWay = $("[data-call-failed-other-way]");
    var offered = Boolean(otherWay) && view.failure !== "not_open";
    if (otherWay) otherWay.hidden = !offered;
    $("[data-call-failed-contacts]").hidden = offered;
    // Leaving through a link would end the call: the way back appears when it is over.
    $("[data-call-back]").hidden = view.screen === "connecting" || inCall;
    selfVideos.forEach(function (video) {
      var here = video.dataset.callSelf === view.screen;
      video.hidden = !(here && view.live.camera && streams.self);
      show(video, here ? streams.self : null);
    });
    show(remoteVideo, view.screen === "call" ? streams.remote : null);
    show(remoteAudio, view.screen === "call" ? streams.audio : null);
    $("[data-call-remote-off]").hidden = view.remoteLive;
  }

  function render() {
    var checking = view.screen === "check";
    var kind = deniedKind(view.refused);
    $$("[data-call-state]").forEach(function (section) {
      section.hidden = section.dataset.callState !== view.screen;
    });
    $$("[data-call-denied]").forEach(function (panel) {
      panel.hidden = !(checking && panel.dataset.callDenied === kind);
    });
    $$("[data-call-missing]").forEach(function (note) {
      note.hidden = !(checking && view.missing.indexOf(note.dataset.callMissing) !== -1);
    });
    // Nothing found and nothing refused: the participant may plug something in and look again.
    $("[data-call-recheck]").hidden = !(
      checking &&
      !view.has.camera &&
      !view.has.microphone &&
      view.refused.length === 0 &&
      view.wish.camera &&
      view.wish.microphone
    );
    // In the check a device is on while its track is live; in the lobby and the call, while the
    // participant has it switched on.
    KINDS.forEach(function (name) {
      var on = checking ? view.has[name] : view.live[name];
      toggles[name].forEach(function (button) {
        button.setAttribute("aria-pressed", String(on));
        button.querySelector("[data-call-toggle-state]").textContent = texts[on ? "on" : "off"];
      });
    });
    preview.hidden = !view.has.camera;
    renderCall();
    // The list of a device that is refused or not found would only list placeholders.
    KINDS.forEach(function (name) {
      var absent = view.refused.indexOf(name) !== -1 || view.missing.indexOf(name) !== -1;
      $('[data-call-field="' + name + '"]').hidden = checking ? absent : true;
    });
    $("[data-call-speaker-field]").hidden = !(checking && view.speaker);
    $("[data-call-sound-note]").hidden = !view.soundPlayed;
    joinButton.textContent = joinLabel(view.refused.concat(view.missing));

    if (view.say !== null) say(view.say);
    view.say = null;
    var target = null;
    if (view.focus === "denied") target = $('[data-call-denied="' + kind + '"] h2');
    else if (view.focus === "state") target = $('[data-call-state="' + view.screen + '"] h1');
    view.focus = "";
    if (target) target.focus();
  }

  // ---- the browser's devices ----------------------------------------------------------------
  var stream = null;
  var run = 0;
  var chosen = { camera: "", microphone: "", speaker: "" };
  var pending = { camera: false, microphone: false }; // a request for that device is waiting

  function tracksOf(kind) {
    if (!stream) return [];
    return kind === "camera" ? stream.getVideoTracks() : stream.getAudioTracks();
  }

  function constraint(kind) {
    var exact = chosen[kind] ? { deviceId: { exact: chosen[kind] } } : null;
    if (kind === "microphone") return exact || true;
    return Object.assign({ width: { ideal: 1280 } }, exact);
  }

  function ask(constraints) {
    return media.getUserMedia(constraints).then(
      function (found) {
        return { stream: found };
      },
      function (error) {
        return { error: error };
      },
    );
  }

  function refusal(error) {
    var name = error && error.name;
    return name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError";
  }

  // One device alone. A saved choice that the browser can no longer satisfy is dropped, for
  // this device only, and the device is asked for again without it.
  function askOne(kind) {
    var constraints = {};
    constraints[kind === "camera" ? "video" : "audio"] = constraint(kind);
    return ask(constraints).then(function (one) {
      var stale = one.error && one.error.name === "OverconstrainedError" && chosen[kind];
      if (!stale) return one;
      chosen[kind] = "";
      return askOne(kind);
    });
  }

  function problem(kind, error) {
    view.refused = view.refused.filter(function (name) {
      return name !== kind;
    });
    view.missing = view.missing.filter(function (name) {
      return name !== kind;
    });
    if (error) (refusal(error) ? view.refused : view.missing).push(kind);
  }

  function attach() {
    var tracks = tracksOf("camera");
    preview.srcObject = tracks.length ? new MediaStream(tracks) : null;
    KINDS.forEach(function (kind) {
      view.has[kind] = tracksOf(kind).length > 0;
    });
  }

  function dropTracks(kind) {
    tracksOf(kind).forEach(function (track) {
      track.stop();
      stream.removeTrack(track);
    });
    if (kind === "microphone") stopMeter();
  }

  // Ask for both at once (one prompt). When that fails, ask for each alone to tell which is the
  // trouble: refused (the browser says no) or missing (nothing found, or in use elsewhere).
  function open() {
    var wanted = KINDS.filter(function (kind) {
      return view.wish[kind];
    });
    if (!wanted.length) return Promise.resolve({ tracks: [], errors: {} });
    var constraints = {
      video: view.wish.camera ? constraint("camera") : false,
      audio: view.wish.microphone ? constraint("microphone") : false,
    };
    return ask(constraints).then(function (both) {
      if (both.stream) return { tracks: both.stream.getTracks(), errors: {} };
      var tracks = [];
      var errors = {};
      return wanted
        .reduce(function (chain, kind) {
          return chain.then(function () {
            return askOne(kind).then(function (one) {
              if (one.stream) tracks = tracks.concat(one.stream.getTracks());
              else errors[kind] = one.error;
            });
          });
        }, Promise.resolve())
        .then(function () {
          return { tracks: tracks, errors: errors };
        });
    });
  }

  function stopDevices() {
    stopMeter();
    if (stream) {
      stream.getTracks().forEach(function (track) {
        track.stop();
      });
    }
    stream = null;
    preview.srcObject = null;
    KINDS.forEach(function (kind) {
      view.has[kind] = false;
    });
  }

  // Leaving the check: the devices and the audio context are given back.
  function release() {
    stopDevices();
    if (audioContext) audioContext.close().catch(function () {});
    audioContext = null;
  }

  function outcomeSay(kind) {
    return kind ? "say_without_" + kind : null;
  }

  function acquire() {
    var mine = (run += 1);
    var hadRefusal = view.refused.length > 0;
    stopDevices();
    view.refused = [];
    view.missing = [];
    view.say = "say_checking";
    render();
    return open().then(function (outcome) {
      if (mine !== run) {
        // A newer request, or leaving the page, took over: this one's devices are given back.
        outcome.tracks.forEach(function (track) {
          track.stop();
        });
        return;
      }
      stream = outcome.tracks.length ? new MediaStream(outcome.tracks) : null;
      KINDS.forEach(function (kind) {
        if (outcome.errors[kind]) problem(kind, outcome.errors[kind]);
      });
      tracksOf("microphone").forEach(function (track) {
        track.enabled = true;
      });
      attach();
      var kind = deniedKind(view.refused);
      view.focus = kind ? "denied" : hadRefusal ? "state" : "";
      view.say = outcomeSay(kind) || (stream ? "say_ready" : "say_no_devices");
      if (!outcome.tracks.length && !view.wish.camera && !view.wish.microphone) view.say = "";
      render();
      if (view.has.microphone) startMeter();
      else showLevel(0, "level_off");
      list();
    });
  }

  // One device switched on or off. Off gives the device back, so the browser's indicator goes
  // out; on asks for that one device again and leaves the other alone.
  function switchOff(kind) {
    view.wish[kind] = false;
    dropTracks(kind);
    attach();
    if (kind === "microphone") showLevel(0, "level_off");
    view.say = "say_" + kind + "_off";
    render();
  }

  function switchOn(kind) {
    // A second request for a device that is still waiting is ignored: two tracks of one kind
    // must never be added.
    if (pending[kind]) return Promise.resolve();
    pending[kind] = true;
    var mine = run;
    view.wish[kind] = true;
    return askOne(kind).then(function (one) {
      pending[kind] = false;
      if (mine !== run) {
        if (one.stream) {
          one.stream.getTracks().forEach(function (track) {
            track.stop();
          });
        }
        return;
      }
      problem(kind, one.error);
      if (one.stream) {
        stream = stream || new MediaStream();
        one.stream.getTracks().forEach(function (track) {
          stream.addTrack(track);
        });
      }
      attach();
      var denied = deniedKind(view.refused);
      view.focus = denied ? "denied" : "";
      view.say = outcomeSay(denied) || (one.stream ? "say_" + kind + "_on" : "say_no_devices");
      render();
      if (kind === "microphone" && view.has.microphone) startMeter();
      if (one.stream) list();
    });
  }

  function replace(kind) {
    if (pending[kind]) return Promise.resolve();
    dropTracks(kind);
    return switchOn(kind);
  }

  function toggle(kind) {
    if (view.screen === "lobby" || view.screen === "call") setLive(kind, !view.live[kind]);
    else if (view.has[kind]) switchOff(kind);
    else switchOn(kind);
  }

  // ---- the lists of devices -------------------------------------------------------------------
  function fill(select, devices, current, label) {
    select.textContent = "";
    devices.forEach(function (device, index) {
      var option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || label + " " + (index + 1);
      option.selected = device.deviceId === current;
      select.appendChild(option);
    });
  }

  function currentId(kind) {
    var track = tracksOf(kind)[0];
    return (track && track.getSettings().deviceId) || "";
  }

  function list() {
    return media.enumerateDevices().then(function (devices) {
      var of = function (kind) {
        return devices.filter(function (device) {
          return device.kind === kind;
        });
      };
      chosen.camera = chosen.camera || currentId("camera");
      chosen.microphone = chosen.microphone || currentId("microphone");
      fill(selects.camera, of("videoinput"), chosen.camera, texts.device_camera);
      fill(selects.microphone, of("audioinput"), chosen.microphone, texts.device_microphone);
      fill(selects.speaker, of("audiooutput"), chosen.speaker, texts.device_speaker);
      // Choosing a speaker is not possible in every browser; the test sound always is.
      var canChoose = "setSinkId" in HTMLMediaElement.prototype && of("audiooutput").length > 0;
      view.speaker = canChoose;
      render();
    });
  }

  // ---- the level meter ------------------------------------------------------------------------
  var audioContext = null;
  var meter = { source: null, timer: 0, frame: 0, shown: "", announced: false };

  function context() {
    audioContext = audioContext || new AudioContextClass();
    if (audioContext.state === "suspended") audioContext.resume();
    return audioContext;
  }

  function showLevel(value, key) {
    level.value = value;
    if (meter.shown !== key) {
      meter.shown = key;
      levelText.textContent = texts[key];
      // The first time sound is heard after the microphone starts, once: never while it moves.
      if (key === "level_heard" && !meter.announced) {
        meter.announced = true;
        say("level_heard");
      }
    }
  }

  function stopMeter() {
    window.cancelAnimationFrame(meter.frame);
    window.clearTimeout(meter.timer);
    if (meter.source) meter.source.disconnect();
    meter.source = null;
    showLevel(0, "level_silent");
  }

  function startMeter() {
    stopMeter();
    meter.announced = false;
    var ctx = context();
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    meter.source = ctx.createMediaStreamSource(new MediaStream(tracksOf("microphone")));
    meter.source.connect(analyser);
    var samples = new Float32Array(analyser.fftSize);
    var lastHeard = 0;
    var tick = function () {
      analyser.getFloatTimeDomainData(samples);
      var sum = 0;
      for (var i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
      var value = Math.min(1, Math.sqrt(sum / samples.length) * 6);
      var now = window.performance.now();
      if (value > 0.05) lastHeard = now;
      // The words change only after a short while of quiet, so they do not flicker.
      showLevel(value, now - lastHeard < 1500 ? "level_heard" : "level_silent");
      // Under reduced motion the bar moves a few times a second, not on every frame.
      if (reduceMotion.matches) meter.timer = window.setTimeout(tick, 250);
      else meter.frame = window.requestAnimationFrame(tick);
    };
    tick();
  }

  // ---- the test sound ---------------------------------------------------------------------------
  function useSpeaker() {
    if (chosen.speaker && testAudio.setSinkId) {
      return testAudio.setSinkId(chosen.speaker).catch(function () {});
    }
    return Promise.resolve();
  }

  function playTestSound() {
    var ctx = context();
    var output = ctx.createMediaStreamDestination();
    var gain = ctx.createGain();
    gain.gain.value = 0.25;
    gain.connect(output);
    var start = ctx.currentTime;
    [440, 660].forEach(function (pitch, index) {
      var tone = ctx.createOscillator();
      tone.frequency.value = pitch;
      tone.connect(gain);
      tone.start(start + index * 0.45);
      tone.stop(start + index * 0.45 + 0.3);
    });
    testAudio.srcObject = output.stream;
    view.soundPlayed = true;
    view.say = "say_sound";
    render();
    return useSpeaker().then(function () {
      return testAudio.play().catch(function () {});
    });
  }

  // ---- the call: the token, the SDK and the meeting -----------------------------------------------
  // What the SDK is told: its logs are never uploaded and the registration of this browser is
  // temporary (ADR 0123 point 16); the rest is what the prototype ran with.
  var SDK_CONFIG = {
    logger: { level: "warn" },
    device: { ephemeral: true },
    meetings: {
      reconnection: { enabled: true },
      enableRtx: true,
      enableAutomaticLLM: true,
      autoUploadLogs: false,
    },
  };
  var PICTURE = { width: 1280, height: 720 }; // the size the picture is asked for
  var TOKEN_MARGIN_MS = 60000; // a token that expires within this is not reused
  var GIVE_UP_MS = 5000; // how long leaving and unregistering may take before the page goes on
  var STALE = { stale: true }; // a step of a connection that something else has taken over
  var choice = { cameraId: "", microphoneId: "" };
  // `epoch` moves whenever a connection starts, ends or is taken over; every step and every
  // event of the SDK checks it, so nothing of an old attempt reaches the page. The token is
  // kept here, in memory, for the retries and for nothing else.
  var call = {
    epoch: 0,
    token: "",
    link: "",
    expires: 0,
    loading: null,
    client: null,
    meeting: null,
    camera: null,
    microphone: null,
    making: { camera: false, microphone: false },
    capturing: null,
    entered: false,
    left: false,
  };

  function noop() {}

  function turnedAway(reason) {
    return { refusal: reason };
  }

  function check(epoch) {
    if (epoch !== call.epoch) throw STALE;
  }

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

  function forget() {
    call.token = "";
    call.link = "";
    call.expires = 0;
  }

  // The guest token and the meeting's guest link, by this server; reused while it is valid.
  function authorise() {
    if (call.token && call.expires - Date.now() > TOKEN_MARGIN_MS) return Promise.resolve();
    var request = { method: "POST", credentials: "same-origin", cache: "no-store" };
    return window.fetch(tokenUrl, request).then(
      function (response) {
        var refused = { 409: "not_open", 429: "rate_limited", 503: "unavailable" }[response.status];
        if (refused) throw turnedAway(refused);
        if (!response.ok) throw turnedAway("generic");
        return response.json().then(
          function (body) {
            var expires = Date.parse(body && body.expires_at);
            if (!body || !body.token || !body.link || isNaN(expires)) throw turnedAway("generic");
            call.token = body.token;
            call.link = body.link;
            call.expires = expires;
          },
          function () {
            throw turnedAway("generic");
          },
        );
      },
      function () {
        throw turnedAway("generic");
      },
    );
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

  // The one place that names what the SDK file puts on the window.
  function sdkGlobal() {
    return window.Webex;
  }

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

  // The devices the participant left on. One that cannot be taken is left out, and said. The
  // lobby and an admission that comes at the same moment share one capture, never two.
  function capture() {
    if (call.capturing) return call.capturing;
    var epoch = call.epoch;
    call.capturing = Promise.all(
      KINDS.map(function (kind) {
        if (!view.live[kind] || call[kind]) return null;
        return createStream(kind).then(
          function (stream) {
            if (epoch !== call.epoch) stream.stop();
            else call[kind] = stream;
          },
          function () {
            view.live[kind] = false;
            say("say_device_failed");
          },
        );
      }),
    ).then(function () {
      if (epoch !== call.epoch) return;
      call.capturing = null;
      streams.self = call.camera ? call.camera.outputStream : null;
    });
    return call.capturing;
  }

  function setRemote(kind, stream) {
    streams[kind] = stream;
    if (kind === "audio" && chosen.speaker && remoteAudio.setSinkId) {
      remoteAudio.setSinkId(chosen.speaker).catch(noop);
    }
    var sync = function () {
      view.remoteLive = Boolean(
        streams.remote &&
          streams.remote.getVideoTracks().some(function (track) {
            return !track.muted && track.readyState === "live";
          }),
      );
      render();
    };
    if (kind === "remote" && stream) {
      stream.getVideoTracks().forEach(function (track) {
        track.addEventListener("mute", sync);
        track.addEventListener("unmute", sync);
      });
    }
    sync();
  }

  // The lobby: the participant is in the meeting and waits to be let in; the own picture shows.
  function lobby(epoch) {
    return capture().then(function () {
      check(epoch);
      view.screen = "lobby";
      view.retrying = false;
      view.focus = "state";
      view.say = "say_waiting";
      render();
    });
  }

  // Admitted, or never held: the media goes in and the first moment of the call is shown.
  function enter(epoch) {
    if (call.entered) return Promise.resolve();
    call.entered = true;
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
        view.screen = "call";
        view.retrying = false;
        view.focus = "state";
        view.say = "say_admitted";
        render();
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
      if (media.type === "remoteVideo") setRemote("remote", media.stream);
      if (media.type === "remoteAudio") setRemote("audio", media.stream);
    });
    client.meetings.on("meeting:removed", function () {
      if (epoch === call.epoch && !call.left) finish("ended");
    });
  }

  // One attempt: the SDK, its registration, the meeting out of the guest link, joining.
  function setUp(epoch) {
    var client;
    return within(loadSdk())
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
        return within(client.meetings.register());
      })
      .then(function () {
        check(epoch);
        return within(client.meetings.create(call.link));
      })
      .then(function (meeting) {
        check(epoch);
        call.meeting = meeting;
        follow(epoch);
        return within(meeting.join());
      })
      .then(function () {
        check(epoch);
        return call.meeting.isUserUnadmitted ? lobby(epoch) : enter(epoch);
      });
  }

  // Gives the devices and the registration back; resolves when done or after GIVE_UP_MS.
  function abandon() {
    var client = call.client;
    [call.microphone, call.camera].forEach(function (stream) {
      if (stream) stream.stop();
    });
    call.microphone = null;
    call.camera = null;
    call.client = null;
    call.meeting = null;
    call.entered = false;
    call.capturing = null;
    call.epoch += 1;
    streams.self = null;
    streams.remote = null;
    streams.audio = null;
    view.remoteLive = false;
    if (!(client && client.meetings && client.meetings.registered)) return Promise.resolve();
    return Promise.race([
      client.meetings.unregister().then(noop, noop),
      new Promise(function (resolve) {
        window.setTimeout(resolve, GIVE_UP_MS);
      }),
    ]);
  }

  function attempt(number) {
    var epoch = (call.epoch += 1);
    view.attempt = number;
    view.retrying = number > 1;
    if (number > 1) {
      view.say = "say_retry";
      render();
    }
    authorise()
      .then(function () {
        check(epoch);
        return setUp(epoch);
      })
      .then(null, function (error) {
        if (error === STALE || epoch !== call.epoch) return null;
        if (error && error.refusal) return fail(error.refusal);
        return abandon().then(function () {
          if (number < attemptsMax) attempt(number + 1);
          else fail("attempts");
        });
      });
  }

  function connect() {
    call.left = false;
    view.screen = "connecting";
    view.attempt = 1;
    view.retrying = false;
    view.failure = "";
    view.focus = "state";
    view.say = "say_connecting";
    render();
    attempt(1);
  }

  function fail(reason) {
    abandon();
    view.screen = "failed";
    view.failure = reason;
    view.retrying = false;
    view.focus = "state";
    view.say = "say_failed_" + reason;
    render();
  }

  // The call is over, for the participant's own leaving ("left") or the meeting's end ("ended").
  function finish(kind) {
    forget();
    abandon();
    view.screen = kind;
    view.focus = "state";
    view.say = "say_" + kind;
    render();
  }

  function leaveCall() {
    var meeting = call.meeting;
    call.left = true;
    call.epoch += 1;
    var gone = meeting ? Promise.race([meeting.leave().then(noop, noop), pause()]) : Promise.resolve();
    finish("left");
    return gone;
  }

  function pause() {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, GIVE_UP_MS);
    });
  }

  // The page goes away or comes back out of the browser's memory: nothing of a call stays.
  function hangUp() {
    call.left = true;
    forget();
    var meeting = call.meeting;
    if (meeting) meeting.leave().then(noop, noop);
    abandon();
  }

  // One device of the call switched on or off. A device that was off at the start is taken
  // at once and, in the call, sent.
  function setLive(kind, on) {
    var stream = call[kind];
    if (!on || stream) {
      if (stream) stream.setUserMuted(!on);
      view.live[kind] = on;
      view.say = "say_" + kind + (on ? "_on" : "_off");
      render();
      return Promise.resolve();
    }
    if (call.making[kind]) return Promise.resolve();
    call.making[kind] = true;
    var epoch = call.epoch;
    return createStream(kind)
      .then(function (created) {
        call.making[kind] = false;
        if (epoch !== call.epoch) {
          created.stop();
          return null;
        }
        call[kind] = created;
        if (kind === "camera") streams.self = created.outputStream;
        var sent = {};
        sent[kind] = created;
        return call.entered ? call.meeting.publishStreams(sent) : null;
      })
      .then(
        function () {
          if (epoch !== call.epoch || !call[kind]) return;
          view.live[kind] = true;
          view.say = "say_" + kind + "_on";
          render();
        },
        function () {
          call.making[kind] = false;
          say("say_device_failed");
        },
      );
  }

  // ---- what the participant does -------------------------------------------------------------------
  function begin() {
    // Made here, in the click, so the browser lets the meter and the test sound make sound.
    context();
    view.screen = "check";
    view.focus = "state";
    view.say = "";
    render();
    return acquire();
  }

  // The click that joins: the devices of the check are given back (the call takes its own) and
  // the call starts with what the participant left on. Without a whole SDK file there is no call.
  function join() {
    run += 1;
    var wanted = { camera: view.has.camera, microphone: view.has.microphone };
    release();
    if (!joinable) {
      view.screen = "unavailable";
      view.focus = "state";
      view.say = "";
      render();
      return;
    }
    choice.camera = wanted.camera;
    choice.microphone = wanted.microphone;
    choice.cameraId = chosen.camera;
    choice.microphoneId = chosen.microphone;
    view.live.camera = wanted.camera;
    view.live.microphone = wanted.microphone;
    connect();
  }

  function leaveCheck() {
    // A request that is still waiting is dropped, so a stream that arrives late is stopped.
    run += 1;
    release();
    hangUp();
  }

  function restart(event) {
    // Restored by the browser (its back-forward cache): the devices are gone, so start again at the start.
    if (!event.persisted) return;
    run += 1;
    release();
    hangUp();
    view = fresh();
    render();
  }

  $$("[data-call-browser]").forEach(function (step) {
    step.hidden = step.dataset.callBrowser !== (safari ? "safari" : "other");
  });
  $$("[data-call-retry]").forEach(function (button) {
    button.addEventListener("click", acquire);
  });
  beginButton.addEventListener("click", begin);
  joinButton.addEventListener("click", join);
  $("[data-call-test-sound]").addEventListener("click", playTestSound);
  $$("[data-call-leave]").forEach(function (button) {
    button.addEventListener("click", leaveCall);
  });
  $("[data-call-again]").addEventListener("click", connect);
  remoteVideo.addEventListener("playing", function () {
    view.remoteLive = true;
    render();
  });
  KINDS.forEach(function (kind) {
    toggles[kind].forEach(function (button) {
      button.addEventListener("click", function () {
        toggle(kind);
      });
    });
    selects[kind].addEventListener("change", function () {
      chosen[kind] = selects[kind].value;
      if (view.has[kind]) replace(kind);
    });
  });
  selects.speaker.addEventListener("change", function () {
    chosen.speaker = selects.speaker.value;
    useSpeaker();
  });
  media.addEventListener("devicechange", function () {
    if (view.screen === "check") list();
  });
  window.addEventListener("pagehide", leaveCheck);
  window.addEventListener("pageshow", restart);

  // The browser can run the call: the sentence that says what it needs is no longer true.
  view = fresh();
  $("[data-call-needs]").hidden = true;
  beginButton.hidden = false;
})();

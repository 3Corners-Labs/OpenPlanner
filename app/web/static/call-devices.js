/*
 * The device check of the participant's call page (ADR 0123 steps 4b and 4c).
 *
 * Owns: the browser's camera and microphone before the call. After the begin button it asks for
 * both (one prompt), keeps a refused permission and a missing device apart, shows the own picture, a
 * level meter and a test sound, lists the devices, lets the participant switch either off and
 * choose another, and hands the choice to the call at the click that joins. It gives every track
 * back when the check ends. It is also the one home of the lists of devices and of choosing a
 * device in the call (step 5a): the panel of the call is filled by the same list, and a choice there
 * goes to call-media.js (camera, microphone) or to the other side's sound (speaker).
 *
 * Never: it makes no request of any kind, loads no file, stores nothing, and reads or writes no
 * text of its own. Every change of what the page shows goes through `app.go` (the one state of
 * call-page.js); this file only touches the elements of the check (the preview, the meter, the
 * lists, the test sound). It runs only after call-page.js has started it with `init`.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var media;
  var preview;
  var level;
  var levelText;
  var testAudio;
  var lists; // the three lists of the check and the three of the call's panel
  var remoteAudio;
  var reduceMotion;
  var stream = null; // the tracks of the check
  var run = 0; // moves when a request is replaced or the check ends; an older answer is dropped
  var chosen = { camera: "", microphone: "", speaker: "" };
  var pending = { camera: false, microphone: false }; // a request for that device is waiting
  var audioContext = null;
  var meter = { source: null, timer: 0, frame: 0, shown: "", announced: false };

  function copy(object, key, value) {
    var next = Object.assign({}, object);
    next[key] = value;
    return next;
  }

  // The one way this file changes what the page shows: a patch of the state, on the current screen.
  function set(patch) {
    app.go(app.state.screen, patch);
  }

  function tracksOf(kind) {
    if (!stream) return [];
    return kind === "camera" ? stream.getVideoTracks() : stream.getAudioTracks();
  }

  function hasNow() {
    return { camera: tracksOf("camera").length > 0, microphone: tracksOf("microphone").length > 0 };
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

  // The lists of kinds after one kind was asked for again: refused, missing, or neither.
  function problems(kind, error) {
    var keep = function (name) {
      return name !== kind;
    };
    var refused = app.state.refused.filter(keep);
    var missing = app.state.missing.filter(keep);
    if (error) (refusal(error) ? refused : missing).push(kind);
    return { refused: refused, missing: missing };
  }

  function showPreview() {
    var tracks = tracksOf("camera");
    preview.srcObject = tracks.length ? new MediaStream(tracks) : null;
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
    var wish = app.state.wish;
    var wanted = app.KINDS.filter(function (kind) {
      return wish[kind];
    });
    if (!wanted.length) return Promise.resolve({ tracks: [], errors: {} });
    var constraints = {
      video: wish.camera ? constraint("camera") : false,
      audio: wish.microphone ? constraint("microphone") : false,
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
  }

  function acquire() {
    var mine = (run += 1);
    var hadRefusal = app.state.refused.length > 0;
    stopDevices();
    set({
      refused: [],
      missing: [],
      has: { camera: false, microphone: false },
      say: "say_checking",
    });
    return open().then(function (outcome) {
      if (mine !== run) {
        // A newer request, or leaving the page, took over: this one's devices are given back.
        outcome.tracks.forEach(function (track) {
          track.stop();
        });
        return;
      }
      stream = outcome.tracks.length ? new MediaStream(outcome.tracks) : null;
      var refused = [];
      var missing = [];
      app.KINDS.forEach(function (kind) {
        var error = outcome.errors[kind];
        if (error) (refusal(error) ? refused : missing).push(kind);
      });
      tracksOf("microphone").forEach(function (track) {
        track.enabled = true;
      });
      showPreview();
      var denied = app.deniedKind(refused);
      var say = denied ? "say_without_" + denied : stream ? "say_ready" : "say_no_devices";
      var wish = app.state.wish;
      if (!outcome.tracks.length && !wish.camera && !wish.microphone) say = "";
      set({
        refused: refused,
        missing: missing,
        has: hasNow(),
        say: say,
        focus: denied ? "denied" : hadRefusal ? "state" : undefined,
      });
      if (tracksOf("microphone").length) startMeter();
      else showLevel(0, "level_off");
      list();
    });
  }

  // One device switched on or off. Off gives the device back, so the browser's indicator goes
  // out; on asks for that one device again and leaves the other alone.
  function switchOff(kind) {
    dropTracks(kind);
    showPreview();
    if (kind === "microphone") showLevel(0, "level_off");
    set({
      wish: copy(app.state.wish, kind, false),
      has: hasNow(),
      say: "say_" + kind + "_off",
    });
  }

  function switchOn(kind) {
    // A second request for a device that is still waiting is ignored: two tracks of one kind
    // must never be added.
    if (pending[kind]) return Promise.resolve();
    pending[kind] = true;
    var mine = run;
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
      if (one.stream) {
        stream = stream || new MediaStream();
        one.stream.getTracks().forEach(function (track) {
          stream.addTrack(track);
        });
      }
      showPreview();
      var found = problems(kind, one.error);
      var denied = app.deniedKind(found.refused);
      var say = denied ? "say_without_" + denied : one.stream ? "say_" + kind + "_on" : "say_no_devices";
      set({
        wish: copy(app.state.wish, kind, true),
        refused: found.refused,
        missing: found.missing,
        has: hasNow(),
        say: say,
        focus: denied ? "denied" : undefined,
      });
      if (kind === "microphone" && tracksOf("microphone").length) startMeter();
      if (one.stream) list();
    });
  }

  function replace(kind) {
    if (pending[kind]) return Promise.resolve();
    dropTracks(kind);
    return switchOn(kind);
  }

  function toggle(kind) {
    if (app.state.has[kind]) switchOff(kind);
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
      // The check's three lists and the call's three are filled alike: one list of devices.
      lists.forEach(function (one) {
        fill(one.camera, of("videoinput"), chosen.camera, app.texts.device_camera);
        fill(one.microphone, of("audioinput"), chosen.microphone, app.texts.device_microphone);
        fill(one.speaker, of("audiooutput"), chosen.speaker, app.texts.device_speaker);
      });
      // Choosing a speaker is not possible in every browser; the test sound always is.
      set({ speaker: "setSinkId" in HTMLMediaElement.prototype && of("audiooutput").length > 0 });
    });
  }

  // A device chosen in a list: the camera or the microphone is replaced, the speaker is used.
  function choose(kind, id) {
    if (app.state.screen === "call") return chooseInCall(kind, id);
    chosen[kind] = id;
    if (kind === "speaker") useSpeaker();
    else if (app.state.has[kind]) replace(kind);
    return Promise.resolve();
  }

  // In the call the speaker is the other side's sound, applied at once; a camera or microphone is
  // sent through the one rule of call-media.js. A choice the browser or the call refuses leaves
  // the old device in use, and the page says so; the lists show what is in use.
  function chooseInCall(kind, id) {
    var applied = kind === "speaker" ? sink(id) : app.callMedia.change(kind, id);
    return applied.then(function (ok) {
      if (ok === undefined) return null; // the call ended meanwhile
      if (ok) chosen[kind] = id;
      set({ say: ok ? "say_device_changed" : "say_device_change_failed" });
      return ok ? null : list();
    });
  }

  function sink(id) {
    if (!remoteAudio.setSinkId) return Promise.resolve(false);
    return remoteAudio.setSinkId(id).then(
      function () {
        return true;
      },
      function () {
        return false;
      },
    );
  }

  // ---- the level meter ------------------------------------------------------------------------
  function context() {
    audioContext = audioContext || new app.AudioContextClass();
    if (audioContext.state === "suspended") audioContext.resume();
    return audioContext;
  }

  function showLevel(value, key) {
    level.value = value;
    if (meter.shown !== key) {
      meter.shown = key;
      levelText.textContent = app.texts[key];
      // The first time sound is heard after the microphone starts, once: never while it moves.
      if (key === "level_heard" && !meter.announced) {
        meter.announced = true;
        set({ say: "level_heard" });
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
    set({ soundPlayed: true, say: "say_sound" });
    return useSpeaker().then(function () {
      return testAudio.play().catch(function () {});
    });
  }

  // ---- the start and the end of the check -------------------------------------------------------
  function init() {
    media = app.media;
    preview = app.$("[data-call-preview]");
    level = app.$("[data-call-level]");
    levelText = app.$("[data-call-level-text]");
    testAudio = app.$("[data-call-test-audio]");
    remoteAudio = app.$("[data-call-remote-audio]");
    lists = ["select", "pick"].map(function (name) {
      var one = {};
      ["camera", "microphone", "speaker"].forEach(function (kind) {
        one[kind] = app.$("[data-call-" + name + '="' + kind + '"]');
      });
      return one;
    });
    reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  }

  // Made in the click of the begin button, so the browser lets the meter and the test sound sound.
  function begin() {
    context();
    app.go("check", { say: "" });
    return acquire();
  }

  // What the participant left on, and which devices they chose, for the call.
  function snapshot() {
    return {
      camera: app.state.has.camera,
      microphone: app.state.has.microphone,
      cameraId: chosen.camera,
      microphoneId: chosen.microphone,
      speakerId: chosen.speaker,
    };
  }

  // Leaving the check: the request that is still waiting is dropped (a stream that arrives late
  // is stopped), the devices and the audio context are given back.
  function release() {
    run += 1;
    stopDevices();
    if (audioContext) audioContext.close().catch(function () {});
    audioContext = null;
    set({ has: { camera: false, microphone: false } });
  }

  app.devices = {
    init: init,
    begin: begin,
    acquire: acquire,
    toggle: toggle,
    choose: choose,
    list: list,
    playTestSound: playTestSound,
    snapshot: snapshot,
    release: release,
    speaker: function () {
      return chosen.speaker;
    },
  };
})();

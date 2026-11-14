/*
 * The participant's call page before the click that joins (ADR 0123 step 4b).
 *
 * Does: after the button of the start state it asks the browser for the camera and the
 * microphone, shows the participant their own picture, a level meter and a test sound, lets them
 * choose camera, microphone and speaker and join with either switched off, and explains a refused
 * permission. Every word is the server's own markup ([data-call-texts] and the sections of the
 * page); the script only shows and hides them and sets text with textContent.
 *
 * One `view` says what the page is in (the screen, what was refused or is missing, which devices
 * are on) and one `render` makes the page show it: every hidden flag, every pressed state, the
 * focus and the sentence for the live region. Nothing reads the page to learn what was written.
 * The next steps add screens to that one function.
 *
 * Never: it makes no request of any kind, loads no other file, stores nothing and sends nothing
 * anywhere; the picture and the sound stay in this browser. It sets no inline style and builds
 * no markup out of text. The join button leads to the state that says the call is not available in
 * this page yet; joining is the next step.
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
    camera: $('[data-call-toggle="camera"]'),
    microphone: $('[data-call-toggle="microphone"]'),
  };
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  var safari =
    /Safari/.test(navigator.userAgent) &&
    !/Chrome|Chromium|Edg|Firefox|FxiOS|CriOS/.test(navigator.userAgent);

  // ---- the view and its one render ------------------------------------------------------------
  var view;
  function fresh() {
    return {
      screen: "start", // start, check or unavailable
      refused: [], // kinds the browser refuses
      missing: [], // kinds with no device
      wish: { camera: true, microphone: true }, // asked for by the participant
      has: { camera: false, microphone: false }, // a live track
      soundPlayed: false,
      speaker: false, // the browser can choose a speaker and there is one to choose
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
    KINDS.forEach(function (name) {
      toggles[name].setAttribute("aria-pressed", String(view.has[name]));
      toggles[name].querySelector("[data-call-toggle-state]").textContent =
        texts[view.has[name] ? "on" : "off"];
    });
    preview.hidden = !view.has.camera;
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
    if (view.has[kind]) switchOff(kind);
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

  function join() {
    run += 1;
    release();
    view.screen = "unavailable";
    view.focus = "state";
    view.say = "";
    render();
  }

  function leave() {
    // A request that is still waiting is dropped, so a stream that arrives late is stopped.
    run += 1;
    release();
  }

  function restart(event) {
    // Restored by the browser (its back-forward cache): the devices are gone, so start again at the start.
    if (!event.persisted) return;
    run += 1;
    release();
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
  KINDS.forEach(function (kind) {
    toggles[kind].addEventListener("click", function () {
      toggle(kind);
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
  window.addEventListener("pagehide", leave);
  window.addEventListener("pageshow", restart);

  // The browser can run the call: the sentence that says what it needs is no longer true.
  view = fresh();
  $("[data-call-needs]").hidden = true;
  beginButton.hidden = false;
})();

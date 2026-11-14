/*
 * The participant's call page before the click that joins (ADR 0123 step 4b).
 *
 * Does: after the button of the start state it asks the browser for the camera and the
 * microphone, shows the participant their own picture, a level meter and a test sound, lets them
 * choose camera, microphone and speaker and join with either switched off, and explains a refused
 * permission. Every word is the server's own markup ([data-call-texts] and the sections of the
 * page); the script only shows and hides them and sets text with textContent.
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

  var texts = {};
  $$("[data-call-texts] [data-text]").forEach(function (node) {
    texts[node.dataset.text] = node.textContent.trim();
  });

  var statusLine = $("[data-call-status]");
  var preview = $("[data-call-preview]");
  var level = $("[data-call-level]");
  var levelText = $("[data-call-level-text]");
  var joinButton = $("[data-call-join]");
  var soundNote = $("[data-call-sound-note]");
  var testAudio = $("[data-call-test-audio]");
  var selects = {
    camera: $('[data-call-select="camera"]'),
    microphone: $('[data-call-select="microphone"]'),
    speaker: $('[data-call-select="speaker"]'),
  };
  var toggles = {
    camera: $('[data-call-toggle="camera"]'),
    microphone: $('[data-call-toggle="microphone"]'),
  };

  var want = { camera: true, microphone: true };
  var chosen = { camera: "", microphone: "", speaker: "" };
  var stream = null;
  var run = 0;
  var audioContext = null;
  var meter = { source: null, timer: 0, frame: 0, shown: "" };
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  // ---- states, focus and announcements --------------------------------------------------
  var sayTimer = 0;
  function say(key) {
    // Clearing first makes an unchanged sentence, such as a second test sound, read out again;
    // a sentence that is not said yet is dropped when a newer one comes.
    window.clearTimeout(sayTimer);
    statusLine.textContent = "";
    if (!key) return;
    sayTimer = window.setTimeout(function () {
      statusLine.textContent = texts[key] || "";
    }, 60);
  }

  function show(name) {
    $$("[data-call-state]").forEach(function (section) {
      section.hidden = section.dataset.callState !== name;
    });
    say("");
    var heading = $('[data-call-state="' + name + '"] h1');
    if (heading) heading.focus();
  }

  function setToggle(button, on) {
    button.setAttribute("aria-pressed", String(on));
    button.querySelector("[data-call-toggle-state]").textContent = texts[on ? "on" : "off"];
  }

  // ---- the browser's devices ----------------------------------------------------------------
  function tracksOf(kind) {
    if (!stream) return [];
    return kind === "camera" ? stream.getVideoTracks() : stream.getAudioTracks();
  }

  function release() {
    stopMeter();
    if (stream) {
      stream.getTracks().forEach(function (track) {
        track.stop();
      });
    }
    stream = null;
    preview.srcObject = null;
  }

  function attempt(constraints) {
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

  // Ask for both at once (one prompt). When that fails, ask for each alone to tell which is the
  // trouble: "refused" (the browser says no) or "missing" (nothing found, or in use elsewhere).
  function open() {
    var video = false;
    if (want.camera) {
      video = { width: { ideal: 1280 } };
      if (chosen.camera) video.deviceId = { exact: chosen.camera };
    }
    var audio = chosen.microphone ? { deviceId: { exact: chosen.microphone } } : true;
    return attempt({ video: video, audio: audio }).then(function (both) {
      if (both.stream) return { stream: both.stream, problems: {} };
      var gone = both.error && both.error.name === "OverconstrainedError";
      if (gone && (chosen.camera || chosen.microphone)) {
        chosen.camera = "";
        chosen.microphone = "";
        return open();
      }
      var parts = [];
      var problems = {};
      var asks = [];
      if (video) asks.push(["camera", { video: video }]);
      asks.push(["microphone", { audio: audio }]);
      return asks
        .reduce(function (chain, ask) {
          return chain.then(function () {
            return attempt(ask[1]).then(function (one) {
              if (one.stream) parts = parts.concat(one.stream.getTracks());
              else problems[ask[0]] = refusal(one.error) ? "refused" : "missing";
            });
          });
        }, Promise.resolve())
        .then(function () {
          return { stream: parts.length ? new MediaStream(parts) : null, problems: problems };
        });
    });
  }

  function problemKinds(problems, which) {
    return Object.keys(problems).filter(function (kind) {
      return problems[kind] === which;
    });
  }

  function browserKey() {
    var agent = navigator.userAgent;
    var safari = /Safari/.test(agent) && !/Chrome|Chromium|Edg|Firefox|FxiOS|CriOS/.test(agent);
    return safari ? "safari" : "other";
  }

  function deniedKind(refused) {
    if (refused.length === 2) return "both";
    return refused.length === 1 ? refused[0] : "";
  }

  function showDenied(kind) {
    var key = browserKey();
    $$("[data-call-denied]").forEach(function (panel) {
      panel.hidden = panel.dataset.callDenied !== kind;
      panel.querySelectorAll("[data-call-browser]").forEach(function (step) {
        step.hidden = step.dataset.callBrowser !== key;
      });
    });
  }

  function joinLabel(absent) {
    var camera = absent.indexOf("camera") !== -1;
    var microphone = absent.indexOf("microphone") !== -1;
    if (camera && microphone) return texts.join_both;
    if (camera) return texts.join_camera;
    return microphone ? texts.join_microphone : texts.join;
  }

  function deniedShown() {
    return $$("[data-call-denied]").some(function (panel) {
      return !panel.hidden;
    });
  }

  function acquire() {
    var mine = (run += 1);
    var hadDenied = deniedShown();
    release();
    say("say_checking");
    return open().then(function (outcome) {
      if (mine !== run) {
        // A newer request took over; do not keep this one's devices.
        if (outcome.stream) {
          outcome.stream.getTracks().forEach(function (track) {
            track.stop();
          });
        }
        return;
      }
      stream = outcome.stream;
      apply(outcome.problems, hadDenied);
    });
  }

  function apply(problems, hadDenied) {
    var hasCamera = tracksOf("camera").length > 0;
    var hasMicrophone = tracksOf("microphone").length > 0;
    tracksOf("microphone").forEach(function (track) {
      track.enabled = want.microphone;
    });
    preview.srcObject = hasCamera ? stream : null;
    preview.hidden = !hasCamera;
    setToggle(toggles.camera, want.camera && hasCamera);
    setToggle(toggles.microphone, want.microphone && hasMicrophone);

    var refused = problemKinds(problems, "refused");
    var missing = problemKinds(problems, "missing");
    $$("[data-call-missing]").forEach(function (note) {
      note.hidden = missing.indexOf(note.dataset.callMissing) === -1;
    });
    // Nothing found and nothing refused: the participant may plug something in and look again.
    $("[data-call-recheck]").hidden = Boolean(stream) || refused.length > 0;
    joinButton.textContent = joinLabel(refused.concat(missing));

    var kind = deniedKind(refused);
    showDenied(kind);
    if (hasMicrophone) startMeter();
    else showLevel(0, "level_off");
    list();

    if (kind) {
      var heading = $('[data-call-denied="' + kind + '"] h2');
      if (heading) heading.focus();
    } else if (hadDenied) {
      $('[data-call-state="check"] h1').focus();
    }
    if (!kind) say(stream ? "say_ready" : "say_no_devices");
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
      $("[data-call-speaker-field]").hidden = !canChoose;
    });
  }

  // ---- the level meter ------------------------------------------------------------------------
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
      var on = want.microphone;
      var value = on ? Math.min(1, Math.sqrt(sum / samples.length) * 6) : 0;
      var now = window.performance.now();
      if (value > 0.05) lastHeard = now;
      // The words change only after a short while of quiet, so they do not flicker.
      var key = !on ? "level_off" : now - lastHeard < 1500 ? "level_heard" : "level_silent";
      showLevel(value, key);
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
    soundNote.hidden = false;
    say("say_sound");
    return useSpeaker().then(function () {
      return testAudio.play().catch(function () {});
    });
  }

  // ---- what the participant does -------------------------------------------------------------------
  function toggle(kind) {
    var button = toggles[kind];
    var on = button.getAttribute("aria-pressed") !== "true";
    want[kind] = on;
    setToggle(button, on);
    say("say_" + kind + "_" + (on ? "on" : "off"));
    if (kind === "microphone") {
      if (on && tracksOf("microphone").length === 0) acquire();
      tracksOf("microphone").forEach(function (track) {
        track.enabled = on;
      });
      return;
    }
    if (on) {
      acquire();
      return;
    }
    tracksOf("camera").forEach(function (track) {
      track.stop();
      stream.removeTrack(track);
    });
    preview.srcObject = null;
    preview.hidden = true;
  }

  function begin() {
    // Made here, in the click, so the browser lets the meter and the test sound make sound.
    context();
    show("check");
    return acquire();
  }

  function join() {
    release();
    show("unavailable");
  }

  var beginButton = $("[data-call-begin]");
  $$("[data-call-retry]").forEach(function (button) {
    button.addEventListener("click", acquire);
  });
  beginButton.addEventListener("click", begin);
  joinButton.addEventListener("click", join);
  $("[data-call-test-sound]").addEventListener("click", playTestSound);
  toggles.camera.addEventListener("click", function () {
    toggle("camera");
  });
  toggles.microphone.addEventListener("click", function () {
    toggle("microphone");
  });
  ["camera", "microphone"].forEach(function (kind) {
    selects[kind].addEventListener("change", function () {
      chosen[kind] = selects[kind].value;
      acquire();
    });
  });
  selects.speaker.addEventListener("change", function () {
    chosen.speaker = selects.speaker.value;
    useSpeaker();
  });
  media.addEventListener("devicechange", function () {
    if (!$('[data-call-state="check"]').hidden) list();
  });
  window.addEventListener("pagehide", release);

  // The browser can run the call: the sentence that says what it needs is no longer true.
  $("[data-call-needs]").hidden = true;
  beginButton.hidden = false;
})();

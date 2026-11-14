/*
 * The sound of the device check on the participant's call page (ADR 0123 steps 4b and 5a).
 *
 * Owns: the level meter (the microphone's level as a bar and a text that says whether the participant
 * is heard; the words change only after a short while, and "heard" is said once per start of the
 * microphone) and the test sound (two tones played to the chosen speaker), with the audio context
 * both need. Check only: the call has no meter.
 *
 * Never: it makes no request, loads no file, stores nothing, and writes no text of its own (every
 * word comes out of `app.texts`). It changes what the page shows only through `app.go` and the
 * elements of the meter and the test sound. It needs nothing of any file loaded after it; the
 * speaker for the test sound is `app.choice.speaker` when the sound plays.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var level;
  var levelText;
  var testAudio;
  var reduceMotion;
  var audioContext = null;
  var meter = { source: null, timer: 0, frame: 0, shown: "", announced: false };

  // Made in the click of the begin button too, so the browser lets the meter and the sound sound.
  function context() {
    audioContext = audioContext || new app.AudioContextClass();
    if (audioContext.state === "suspended") audioContext.resume();
    return audioContext;
  }

  function close() {
    if (audioContext) audioContext.close().catch(function () {});
    audioContext = null;
  }

  function showLevel(value, key) {
    level.value = value;
    if (meter.shown !== key) {
      meter.shown = key;
      levelText.textContent = app.texts[key];
      // The first time sound is heard after the microphone starts, once: never while it moves.
      if (key === "level_heard" && !meter.announced) {
        meter.announced = true;
        app.go(app.state.screen, { say: "level_heard" });
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

  // The meter of the microphone's tracks.
  function startMeter(tracks) {
    stopMeter();
    meter.announced = false;
    var ctx = context();
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    meter.source = ctx.createMediaStreamSource(new MediaStream(tracks));
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

  function useSpeaker() {
    var id = app.choice.speaker;
    if (id && testAudio.setSinkId) return testAudio.setSinkId(id).catch(function () {});
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
    app.go(app.state.screen, { soundPlayed: true, say: "say_sound" });
    return useSpeaker().then(function () {
      return testAudio.play().catch(function () {});
    });
  }

  function init() {
    level = app.$("[data-call-level]");
    levelText = app.$("[data-call-level-text]");
    testAudio = app.$("[data-call-test-audio]");
    reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  }

  app.sound = {
    init: init,
    context: context,
    close: close,
    showLevel: showLevel,
    stopMeter: stopMeter,
    startMeter: startMeter,
    useSpeaker: useSpeaker,
    playTestSound: playTestSound,
  };
})();

/*
 * The controls of the participant's call beside the camera and the microphone (ADR 0123 step 5a).
 *
 * Owns: the panel of devices in the call (a disclosure: the button opens it, the button or Escape
 * closes it, and Escape returns focus to the button) and the two notices that last while they hold,
 * the recording and the connection being restored. The notices follow what the meeting tells the
 * page: each change is said once, a repeat of the same event says nothing, and a connection lost for
 * good ends the call in the failure state. The lists of devices and choosing a device are in
 * call-devices.js and call-media.js; the words and the shapes are the server's markup.
 *
 * Never: it makes no request, loads no file, stores nothing, writes no text of its own and shows
 * nothing of what the meeting sends (an event carries no text into the page). It changes what the
 * page shows only through `app.go`, and an event of a connection that is over is ignored (the
 * epoch of call-connection.js). It runs only after call-page.js has started it with `init`.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  // The panel opens and closes in the one state (`panel`); the lists are looked at again when it opens.
  function toggle() {
    if (app.state.screen !== "call") return;
    var open = !app.state.panel;
    app.go("call", { panel: open });
    if (open) app.devices.list();
  }

  function init() {
    var holder = app.$(".call-bar__devices");
    app.$("[data-call-devices-button]").addEventListener("click", toggle);
    holder.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && app.state.panel) {
        app.go("call", { panel: false, focus: "devices" });
      }
    });
  }

  // A change that the meeting reports, for this connection only. Only the call shows a notice and
  // says it; before that the state is kept without a word, and the notice shows when the call starts.
  function mark(epoch, patch) {
    if (epoch !== app.call.epoch) return;
    var screen = app.state.screen;
    if (screen !== "call") delete patch.say;
    app.go(screen, patch);
  }

  function follow(meeting, epoch) {
    var state = app.state;
    meeting.on("meeting:recording:started", function () {
      if (!state.recording) mark(epoch, { recording: true, say: "say_recording_on" });
    });
    meeting.on("meeting:recording:stopped", function () {
      if (state.recording) mark(epoch, { recording: false, say: "say_recording_off" });
    });
    meeting.on("meeting:reconnectionStarts", function () {
      if (state.connection !== "restoring") mark(epoch, { connection: "restoring", say: "say_restoring" });
    });
    meeting.on("meeting:reconnectionSuccess", function () {
      if (state.connection === "restoring") mark(epoch, { connection: "ok", say: "say_restored" });
    });
    meeting.on("meeting:reconnectionFailure", function () {
      if (epoch === app.call.epoch) app.connection.lost();
    });
  }

  app.controls = { init: init, follow: follow };
})();

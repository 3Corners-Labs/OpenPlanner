/*
 * What the meeting tells the participant's call page (ADR 0123 steps 4c and 5a).
 *
 * Owns: every registration on the meeting and the client's meetings: the staff member letting the
 * participant in, the other side's picture and sound, the meeting's end, the recording starting
 * and stopping and the connection being interrupted, restored or lost for good. Each event is tied
 * to the connection that made it (the epoch of call-connection.js), so an event of a call that is
 * over changes nothing. An event only asks for a change of the page's state; what it carries is
 * never read, so no text of outside origin reaches the page.
 *
 * Never: it makes no request, loads no file, stores nothing and writes no text of its own. It
 * changes what the page shows only through `app.go` and the calls of call-connection.js and
 * call-page.js, and it needs nothing of any file loaded after it. It runs when the connection
 * has made its meeting.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  // A change the meeting reports. Only the lobby and the call show a notice and say it; before
  // that the state is kept without a word, and the page tells it when it gets there.
  function mark(epoch, patch) {
    if (epoch !== app.call.epoch) return;
    var screen = app.state.screen;
    if (screen !== "lobby" && screen !== "call") delete patch.say;
    app.go(screen, patch);
  }

  function follow(client, meeting, epoch) {
    var state = app.state;
    var current = function () {
      return epoch === app.call.epoch;
    };
    meeting.on("meeting:self:guestAdmitted", function () {
      if (current()) app.connection.admitted(epoch);
    });
    meeting.on("media:ready", function (media) {
      if (!current()) return;
      if (media.type === "remoteVideo") app.setRemote("remote", media.stream);
      if (media.type === "remoteAudio") app.setRemote("audio", media.stream);
    });
    client.meetings.on("meeting:removed", function () {
      if (current()) app.connection.ended();
    });
    meeting.on("meeting:recording:started", function () {
      if (state.recording === "off") mark(epoch, { recording: "pending" });
    });
    meeting.on("meeting:recording:stopped", function () {
      if (state.recording === "on") mark(epoch, { recording: "off", say: "say_recording_off" });
      else if (state.recording === "pending") mark(epoch, { recording: "off" });
    });
    meeting.on("meeting:reconnectionStarts", function () {
      if (state.connection !== "restoring") mark(epoch, { connection: "restoring", say: "say_restoring" });
    });
    meeting.on("meeting:reconnectionSuccess", function () {
      if (state.connection === "restoring") mark(epoch, { connection: "ok", say: "say_restored" });
    });
    meeting.on("meeting:reconnectionFailure", function () {
      if (current()) app.connection.lost();
    });
  }

  app.events = { follow: follow };
})();

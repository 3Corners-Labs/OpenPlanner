/*
 * What the meeting tells the participant's call page (ADR 0123 steps 4c, 5a and 5b).
 *
 * Owns: every registration on the meeting and the client's meetings: the staff member letting the
 * participant in, the other side's picture and sound, the meeting's end, the recording starting
 * and stopping, the connection being interrupted, restored or lost for good, the staff member's
 * screen starting and stopping, the participant's own share ended by the meeting, what the meeting
 * offers (sharing, captions) and the captions. Each event is tied to the connection that made it
 * (the epoch of call-connection.js), so an event of a call that is over changes nothing. An event
 * only asks for a change of the page's state; what it carries is never read, so no text of outside
 * origin reaches the page, with one exception: a caption's text goes to call-captions.js, which
 * shows it as text and reads nothing else of it but who spoke.
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

  // What this meeting offers the participant now: sharing a screen if the browser can capture
  // one and the meeting allows it, captions if it has them. Read out of the meeting each time.
  function offers() {
    var meeting = app.call.meeting;
    var actions = meeting && meeting.inMeetingActions && meeting.inMeetingActions.get();
    return {
      share: Boolean(actions && actions.canShareDesktop && app.media.getDisplayMedia),
      captions: app.captions.supported(meeting),
    };
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
      if (media.type === "remoteShare") {
        app.streams.share = media.stream;
        mark(epoch, {});
      }
    });
    meeting.on("meeting:startedSharingRemote", function () {
      if (current()) app.callMedia.staffShare(true);
    });
    meeting.on("meeting:stoppedSharingRemote", function () {
      if (current()) app.callMedia.staffShare(false);
    });
    meeting.on("meeting:stoppedSharingLocal", function () {
      if (current()) app.callMedia.sharedEnded();
    });
    meeting.on("meeting:actionsUpdate", function () {
      if (!current() || app.state.screen !== "call") return;
      var now = offers();
      mark(epoch, { can: now });
      if (!now.share && state.share === "mine") app.callMedia.share(false);
    });
    meeting.on("meeting:receiveTranscription:started", function () {
      app.captions.language(epoch);
    });
    meeting.on("meeting:caption-received", function (payload) {
      app.captions.received(epoch, payload);
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
      if (state.connection === "ok") mark(epoch, { connection: "pending" });
    });
    meeting.on("meeting:reconnectionSuccess", function () {
      if (state.connection === "restoring") mark(epoch, { connection: "ok", say: "say_restored" });
      else if (state.connection === "pending") mark(epoch, { connection: "ok" });
    });
    meeting.on("meeting:reconnectionFailure", function () {
      if (current()) app.connection.lost();
    });
  }

  app.events = { follow: follow, offers: offers };
})();

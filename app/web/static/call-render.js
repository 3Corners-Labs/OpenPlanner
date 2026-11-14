/*
 * How the call's parts look, as a function of the page's state (ADR 0123 steps 5a and 5b).
 *
 * Owns: drawing the lobby's and the call's parts out of `app.state`: the pictures (the participant's
 * own, the other side's, the button for their sound), the notices, the two switches of sharing and
 * captions with the staff member's screen, the captions' lines and the bar with its panel of
 * devices. Each is one function of the state onto the elements: it sets `hidden`, pressed states
 * and text, and nothing it draws is kept anywhere else.
 *
 * Never: it decides nothing, moves no focus, changes no state, makes no request and stores
 * nothing. `render` in call-page.js is the one caller and the one place that decides what is
 * shown and the place focus goes to. Text of outside origin (a caption) is only ever set with `textContent`.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var inCall = function (state) {
    return state.screen === "lobby" || state.screen === "call";
  };

  function show(video, stream) {
    if (video.srcObject !== stream) video.srcObject = stream;
  }

  // The participant's own picture, in its own place; the other side's picture and sound in the call.
  function pictures(state) {
    var screen = state.screen;
    app.$$("[data-call-self]").forEach(function (video) {
      var here = video.dataset.callSelf === screen;
      video.hidden = !(here && state.live.camera && app.streams.self);
      show(video, here ? app.streams.self : null);
    });
    show(app.$("[data-call-remote]"), screen === "call" ? app.streams.remote : null);
    show(app.$("[data-call-remote-audio]"), screen === "call" ? app.streams.audio : null);
    app.$("[data-call-remote-off]").hidden = state.remote === "live";
    app.$("[data-call-hear]").hidden = !(screen === "call" && state.soundBlocked);
  }

  // Notices last while they hold.
  function notices(state) {
    var holding = {
      recording: inCall(state) && state.recording !== "off",
      restoring: inCall(state) && state.connection !== "ok",
      sharing: state.screen === "call" && state.share === "mine",
      theirs: state.screen === "call" && state.share === "theirs",
    };
    app.$$("[data-call-notice]").forEach(function (notice) {
      notice.hidden = !holding[notice.dataset.callNotice];
    });
  }

  // The bar's two switches (only in the call, only if the meeting offers them; the captions' stays
  // while they run), the staff member's screen in place of their picture. One share in a meeting:
  // while the staff member's is shown, the participant's switch is dimmed.
  function sharing(state) {
    var here = state.screen === "call";
    var on = { share: state.share === "mine", captions: state.captions === "on" };
    var offered = { share: state.can.share, captions: state.can.captions || state.captions !== "off" };
    app.$$("[data-call-switch]").forEach(function (button) {
      var kind = button.dataset.callSwitch;
      button.hidden = !(here && offered[kind]);
      button.setAttribute("aria-pressed", String(on[kind]));
      button.querySelector("[data-call-toggle-state]").textContent = app.texts[on[kind] ? "on" : "off"];
    });
    app.$('[data-call-switch="share"]').setAttribute("aria-disabled", String(state.share === "theirs"));
    var theirs = here && state.share === "theirs";
    app.$("[data-call-stage]").dataset.callShare = theirs ? "theirs" : "off";
    var view = app.$("[data-call-share-view]");
    view.hidden = !theirs;
    show(view, theirs ? app.streams.share : null);
  }

  function row(line) {
    var paragraph = document.createElement("p");
    paragraph.classList.add("call-caption");
    if (line.interim) paragraph.classList.add("call-caption--interim");
    var who = document.createElement("strong");
    who.classList.add("call-caption__who");
    who.textContent = line.own ? app.texts.captions_you : app.texts.captions_other;
    var words = document.createElement("span");
    words.classList.add("call-caption__text");
    words.textContent = line.text;
    paragraph.append(who, " ", words);
    return paragraph;
  }

  // The captions' lines, as text nodes only. The box scrolls inside itself, to the start of the
  // newest line (or to its foot when that line is short); it takes focus only when it can scroll,
  // and keeps the reader's place while it has focus.
  function captions(state) {
    var box = app.$("[data-call-captions]");
    var lines = app.captions.shown(state);
    var reading = document.activeElement === box;
    var kept = box.scrollTop;
    box.replaceChildren.apply(box, lines.map(row));
    box.hidden = lines.length === 0;
    if (!lines.length) {
      box.removeAttribute("tabindex");
      return;
    }
    if (box.scrollHeight > box.clientHeight) box.setAttribute("tabindex", "0");
    else box.removeAttribute("tabindex");
    var last = box.lastElementChild;
    box.scrollTop = reading ? kept : Math.min(box.scrollHeight - box.clientHeight, last.offsetTop);
  }

  // The one bar of the lobby and the call, its way out and the panel of devices.
  function bar(state) {
    app.$("[data-call-controls]").hidden = !inCall(state);
    // The way out says what it leaves: in the lobby the participant is not in the call yet.
    app.$$("[data-call-leave-label]").forEach(function (label) {
      label.hidden = label.dataset.callLeaveLabel !== state.screen;
    });
    var open = inCall(state) && state.panel;
    app.$("[data-call-devices-panel]").hidden = !open;
    app.$("[data-call-devices-button]").setAttribute("aria-expanded", String(open));
    app.$("[data-call-pick-speaker-field]").hidden = !(open && state.speaker);
  }

  app.view = { show: show, pictures: pictures, notices: notices, sharing: sharing, captions: captions, bar: bar };
})();

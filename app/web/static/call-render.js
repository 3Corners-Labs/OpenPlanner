/*
 * How the call's parts look, as a function of the page's state (ADR 0123 steps 5a and 5b).
 *
 * Owns: drawing the lobby's and the call's parts out of `app.state`: the pictures (the participant's
 * own, the other side's with its label, the button for their sound), the notices, the two switches of sharing and
 * captions with the staff member's screen, the captions' lines and the bar with its panel of
 * devices. Each is one function of the state onto the elements: it sets `hidden`, pressed states
 * and text, and nothing it draws is kept anywhere else.
 *
 * Never: it decides nothing, moves no focus, changes no state, makes no request and stores
 * nothing. It does measure: the captions' box is looked at for its layout (does it scroll, at what place is
 * the newest line), and the newest line's identity is kept on the box itself, never in the state. `render` in call-page.js is the one caller and the one place that decides what is
 * shown and the place focus goes to. Text of outside origin (a caption) is only ever set with `textContent`.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var inCall = function (state) {
    return state.screen === "lobby" || state.screen === "call";
  };

  // Who the participant talks to, as the picture's label: the name, a middle dot and the term, or
  // the term alone (the texts are the server's; the name is our server's answer, only ever text).
  function staffLabel(state) {
    return state.staffName ? app.texts.staff_named.replace("{name}", state.staffName) : app.texts.staff_term;
  }

  // The other side in the captions: the same label as on the picture (the approved design shows
  // "name · term"; the term alone while no name is known).
  function speaker(state) {
    return staffLabel(state);
  }

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
    // Who the participant talks to: the name and the team's word, or the word alone. The label is
    // text in the page and the picture's accessible name; the name is only ever set as text.
    var label = staffLabel(state);
    var tag = app.$("[data-call-remote-label]");
    if (tag.textContent !== label) tag.textContent = label;
    tag.hidden = screen !== "call";
    app.$("[data-call-remote]").setAttribute("aria-label", label);
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
    who.textContent = line.own ? app.texts.captions_you : speaker(app.state);
    var words = document.createElement("span");
    words.classList.add("call-caption__text");
    words.textContent = line.text;
    paragraph.append(who, " ", words);
    return paragraph;
  }

  // The captions' lines, as text nodes only. The box scrolls inside itself. When a new line has
  // arrived it scrolls to the start of that line (or to its foot when the line is short); on any
  // other change the reader place is kept. It takes focus only when it can scroll, and keeps it
  // while it has focus.
  function captions(state) {
    var box = app.$("[data-call-captions]");
    var lines = app.captions.shown(state);
    var kept = box.scrollTop;
    box.replaceChildren.apply(box, lines.map(row));
    box.hidden = lines.length === 0;
    if (!lines.length) {
      delete box.dataset.newest;
      box.removeAttribute("tabindex");
      return;
    }
    if (box.scrollHeight > box.clientHeight) box.setAttribute("tabindex", "0");
    else if (document.activeElement !== box) box.removeAttribute("tabindex");
    var last = lines[lines.length - 1];
    var newest = last.interim ? "interim:" + last.own : last.key; // an interim line that grows is not a new line
    var arrived = box.dataset.newest !== newest;
    box.dataset.newest = newest;
    var end = box.scrollHeight - box.clientHeight;
    box.scrollTop = arrived ? Math.min(end, box.lastElementChild.offsetTop) : Math.min(kept, end);
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

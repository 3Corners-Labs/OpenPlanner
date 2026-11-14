/*
 * The closed captions of the participant's call page (ADR 0123 point 12, build step 5b).
 *
 * Owns: whether the meeting offers captions, switching them on and off for this participant (the
 * meeting is asked to start them, in the page's language when it offers it, and the real-time link
 * they need is asked for only now, at the press), taking each caption the meeting hands out into
 * the page's state (the last few lines and one interim line, each marked as the participant's own
 * or the other side's) and drawing those lines as ordinary text. A caption is only its text: the
 * name the video platform gives a speaker is never read, so the page names the other side with its
 * own words only.
 *
 * Never: it makes no request, loads no file and stores nothing. Captions live in the page's memory
 * (`app.state.lines` and `app.state.interim`) and are gone when they are switched off or the call
 * ends; they are never logged, sent to our server, set as markup or put in a live region. It
 * changes the page only through `app.go` and its own text nodes, and it runs only when asked.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var MAX_LINES = 3; // the lines that stay in view, an interim one included; older ones leave
  var MAX_CHARS = 400; // a longer caption is cut
  var drawn = { lines: null, interim: null, on: false, count: 0 };

  function quiet(step) {
    try {
      var result = step();
      if (result && typeof result.then === "function") result.then(null, function () {});
    } catch (ignored) {
      // The captions were not running: nothing to stop.
    }
  }

  // Whether this meeting can show captions at all; the page offers the toggle only then.
  function supported(meeting) {
    return Boolean(
      meeting && typeof meeting.isTranscriptionSupported === "function" && meeting.isTranscriptionSupported(),
    );
  }

  // ---- on and off --------------------------------------------------------------------------------
  // The real-time link is asked for here, at the press, and not at the join (the page's SDK
  // configuration leaves it off); a meeting that has no such call starts the captions without it.
  function start() {
    var state = app.state;
    var epoch = app.call.epoch;
    var meeting = app.call.meeting;
    if (!state.can.captions) return Promise.resolve();
    app.go("call", { captions: "starting" });
    var failed = function () {
      if (epoch !== app.call.epoch) return;
      quiet(function () {
        return meeting.stopTranscription();
      });
      app.go(app.state.screen, { captions: "off", lines: [], interim: null, say: "say_captions_failed" });
    };
    var starting = Promise.resolve()
      .then(function () {
        return typeof meeting.updateLLMConnection === "function" ? meeting.updateLLMConnection() : null;
      })
      .then(function () {
        return meeting.startTranscription();
      });
    return app.within(starting).then(function () {
      if (epoch === app.call.epoch && app.state.captions === "starting") {
        app.go("call", { captions: "on", say: "say_captions_on" });
      }
    }, failed);
  }

  function stop() {
    var meeting = app.call.meeting;
    quiet(function () {
      return meeting.stopTranscription();
    });
    app.go("call", { captions: "off", lines: [], interim: null, say: "say_captions_off" });
  }

  function press() {
    var state = app.state;
    if (state.screen !== "call" || state.captions === "starting") return Promise.resolve();
    if (state.captions === "on") {
      stop();
      return Promise.resolve();
    }
    return start();
  }

  // The meeting started to hand out captions: in the page's language, when it offers that.
  function language(epoch) {
    var meeting = app.call.meeting;
    var tag = (document.documentElement.lang || "").slice(0, 2);
    if (epoch !== app.call.epoch || app.state.captions === "off" || !tag) return;
    Promise.resolve()
      .then(function () {
        return typeof meeting.setCaptionLanguage === "function" ? meeting.setCaptionLanguage(tag) : null;
      })
      .then(null, function () {});
  }

  // ---- what arrives ------------------------------------------------------------------------------
  // The participant's own words are those of the speaker that is this participant's own member.
  function isOwn(speaker) {
    var meeting = app.call.meeting;
    var self = meeting && (meeting.selfId || (meeting.members && meeting.members.selfId));
    if (!speaker || !self) return false;
    return [speaker.speakerId, speaker.memberId, speaker.id].indexOf(self) !== -1;
  }

  function lineOf(caption) {
    if (!caption || typeof caption.text !== "string" || !caption.text.trim()) return null;
    var text = caption.text.trim().slice(0, MAX_CHARS);
    var key = caption.id !== undefined ? "id" + caption.id : "at" + caption.timestamp + text;
    return { key: key, own: isOwn(caption.speaker), text: text, interim: false };
  }

  function listOf(value) {
    return Array.isArray(value) ? value : [];
  }

  // The newest interim line, if any; the meeting groups them by speaker.
  function interimOf(groups) {
    var found = [];
    Object.keys(groups || {}).forEach(function (name) {
      found = found.concat(groups[name] || []);
    });
    var lines = found.map(lineOf).filter(Boolean);
    var last = lines[lines.length - 1];
    if (last) last.interim = true;
    return last || null;
  }

  // A caption event: the final lines (told again with the same id, a line is replaced, not
  // doubled) and the interim line, which the next event replaces by its final text or drops.
  function received(epoch, payload) {
    var state = app.state;
    if (epoch !== app.call.epoch || state.captions === "off" || !payload) return;
    var lines = state.lines.slice();
    var known = lines.map(function (line) {
      return line.key;
    });
    listOf(payload.captions).map(lineOf).forEach(function (line) {
      if (!line) return;
      var at = known.indexOf(line.key);
      if (at === -1) {
        known.push(line.key);
        lines.push(line);
      } else {
        lines[at] = line;
      }
    });
    app.go(state.screen, { lines: lines.slice(-MAX_LINES), interim: interimOf(payload.interimCaptions) });
  }

  // ---- what is shown -------------------------------------------------------------------------------
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

  // The lines in `box`, as text nodes only; returns how many there are (the page hides an empty box).
  // The box is rebuilt only when the lines changed.
  function draw(box) {
    var state = app.state;
    var on = state.captions === "on";
    if (drawn.on === on && drawn.lines === state.lines && drawn.interim === state.interim) return drawn.count;
    var shown = on ? state.lines.slice() : [];
    if (on && state.interim) shown = shown.slice(-(MAX_LINES - 1)).concat([state.interim]);
    box.replaceChildren.apply(box, shown.map(row));
    drawn = { lines: state.lines, interim: state.interim, on: on, count: shown.length };
    return shown.length;
  }

  app.captions = { supported: supported, press: press, language: language, received: received, draw: draw };
})();

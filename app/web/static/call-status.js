/*
 * The one polite status line of the participant's call page (ADR 0123 points 10 and 13).
 *
 * Owns: the queue behind the page's single live region. It gets one sentence at a time, each kept on
 * screen for a minimum time, so a new sentence never replaces one that has not been read out: none
 * is dropped, none repeats (a sentence equal to the one waiting last is not queued twice). An empty
 * key clears region and queue. The sentence is the server's own text, read by its key out of `app.texts`.
 *
 * Never: it makes no request, loads no file, stores nothing, writes no text of its own and takes no
 * focus. It touches only the status element and needs nothing of any file loaded after it.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var SAY_MS = 1500;
  var line = null;
  var queue = [];
  var timer = 0;
  var busy = false;

  function pump() {
    if (busy || !queue.length) return;
    busy = true;
    line.textContent = "";
    var next = queue.shift();
    // Emptied first, so a sentence equal to the one before is read out again.
    window.setTimeout(function () {
      line.textContent = next;
    }, 60);
    timer = window.setTimeout(function () {
      busy = false;
      pump();
    }, SAY_MS);
  }

  function say(key) {
    line = line || app.$("[data-call-status]");
    if (!key) {
      window.clearTimeout(timer);
      queue = [];
      busy = false;
      line.textContent = "";
      return;
    }
    var text = app.texts[key] || "";
    if (text && queue[queue.length - 1] !== text) queue.push(text);
    pump();
  }

  app.say = say;
})();

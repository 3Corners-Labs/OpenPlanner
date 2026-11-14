/*
 * The one polite status line of the participant's call page (ADR 0123 points 10 and 13).
 *
 * Owns: the queue behind the page's single live region. It gets one sentence at a time, each kept on
 * screen for a minimum time, so a new sentence never replaces one that has not been read out: none
 * is dropped, none repeats (a sentence equal to the one waiting last is not queued twice). An empty
 * key clears region and queue. The sentence is the server's own text, read by its key out of `app.texts`;
 * a sentence that names the staff member (`say_staff_name`) has its `{name}` filled with the name the
 * caller hands in, as text. Such a sentence (the closed list `REPLACING`) replaces one of its own kind that is still waiting, so
 * a name that changed before the old one was read out is never read out stale: the newest only (the
 * sentence on the line at that moment was true when it was said and is read to its end).
 *
 * Never: it makes no request, loads no file, stores nothing, writes no text of its own and takes no
 * focus. It touches only the status element and needs nothing of any file loaded after it.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var SAY_MS = 1500;
  // The sentences that replace one of their own kind that is still waiting, so that only the newest
  // is read out (a name that changed before the old one was read). The replaced sentence is gone and
  // the new one goes to the end of the queue: a sentence queued between the two is still said first.
  var REPLACING = ["say_staff_name"];
  var line = null;
  var queue = [];
  var timer = 0;
  var busy = false;

  function pump() {
    if (busy || !queue.length) return;
    busy = true;
    line.textContent = "";
    var next = queue.shift().text;
    // Emptied first, so a sentence equal to the one before is read out again.
    window.setTimeout(function () {
      line.textContent = next;
    }, 60);
    timer = window.setTimeout(function () {
      busy = false;
      pump();
    }, SAY_MS);
  }

  function say(key, values) {
    line = line || app.$("[data-call-status]");
    if (!key) {
      window.clearTimeout(timer);
      queue = [];
      busy = false;
      line.textContent = "";
      return;
    }
    var text = app.texts[key] || "";
    if (text && values && typeof values.name === "string") text = text.replace("{name}", values.name);
    if (text && REPLACING.indexOf(key) !== -1) {
      queue = queue.filter(function (waiting) {
        return waiting.key !== key;
      });
    }
    var last = queue[queue.length - 1];
    if (text && !(last && last.text === text)) queue.push({ key: key, text: text });
    pump();
  }

  app.say = say;
})();

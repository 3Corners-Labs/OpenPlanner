/*
 * The chat beside the participant's call (ADR 0123 point 8, step 7c).
 *
 * Owns: the side bar of the page (`[data-call-chat]`, only in the markup of a booking that has a
 * chat): the thread as a log of messages, the labelled field with its limits said in text, sending
 * (the button, and Enter; Shift and Enter break the line), the state of each own message in words,
 * the staff member's name on theirs (our server's answer, as text), and the closed state, in which the
 * field makes way for a sentence that says why. It is shown in every state of the page after the
 * start (the check with a refused device, connecting, the lobby, the call, a failure, the end), so
 * a participant whose picture cannot be set up still reaches the staff member.
 *
 * It asks our own server only, at the address the markup names: `GET` for what is new since the
 * newest message the page holds (`since`) and the own messages it still shows as waiting (`waiting`),
 * and `POST` of `{id, text}` for a send. It has no timer: `call-poll.js`, the page's one poll, calls
 * `turn` in its turns at the interval of `embedded_call_chat_poll_seconds` and decides when to stop;
 * `wants` tells it whether this page still needs a turn (not in the start state, and not once the
 * chat is closed and read, or once the page is over and has been read once). A message is kept for a
 * retry under the identifier the page made for it, so the same text sent twice is one message. A
 * send that fails keeps the text in the field and says so in words, next to the field and in the
 * polite status line.
 *
 * Lines that did not change are not drawn again: every message has a node of its own, kept by its
 * key and changed only in the part whose words changed, so a reader's scroll place and a screen reader's
 * place in the log hold. The log is the live region: it only ever gains nodes, and the thread that
 * is read for the first time (a reload) is drawn with the region switched off, so history is not
 * read out. Everything the participant or the staff member wrote is set with `textContent`.
 *
 * Never: it loads no file, stores nothing (the thread lives in the page and is gone with it), sets
 * no inline style, builds no markup out of text, reaches no one but our own server, and moves focus
 * only to the closed sentence when the field that had focus goes away and to the field when a send
 * is refused.
 */
(function () {
  "use strict";

  var app = (window.openplannerCall = window.openplannerCall || {});

  var NEAR_END_PX = 24; // a log scrolled this close to its end follows new messages
  var OVER = { left: true, ended: true }; // the screens after which the page is over

  var chatUrl = "";
  var maxChars = 0;
  var box = null;
  var log = null;
  var form = null;
  var field = null;
  var counter = null;
  var problem = null;
  var closedBox = null;
  var closedText = null;

  var items = []; // the thread as shown: { key, id, author, text, state, time }
  var nodes = {}; // key -> the message's element
  var drawn = {}; // key -> what that element shows, to leave an unchanged line alone
  var cursor = 0; // the newest server id the page holds
  var staff = ""; // the staff member's name for the participant, "" for none
  var closed = ""; // why our server says the chat takes nothing more: "", "ended" or "staff"
  var loaded = false; // the first answer has been drawn
  var pending = null; // { id, text }: the message in flight or to retry
  var sending = false;
  var announcedClosed = false;
  var screen = "start";
  var finalRead = false; // a read has been done on a page that is over

  function enabled() {
    return Boolean(chatUrl);
  }

  function reason(state) {
    if (state.screen === "ended") return "ended";
    if (state.screen === "left") return "left";
    return closed;
  }

  // ---- the lines of the log ----------------------------------------------------------------------
  function who(item) {
    if (item.author === "participant") return app.texts.chat_you;
    return staff ? app.texts.staff_named.replace("{name}", staff) : app.texts.staff_term;
  }

  function stateWord(item) {
    return item.author === "participant" ? app.texts["chat_state_" + item.state] || "" : "";
  }

  function element(tag, className) {
    var node = document.createElement(tag);
    node.className = className;
    return node;
  }

  function build(item) {
    var node = element("div", "call-chat__message");
    node.classList.add("call-chat__message--" + item.author);
    var meta = element("p", "call-chat__meta");
    meta.append(element("strong", "call-chat__who"), " ", element("span", "call-chat__time"));
    node.append(meta, element("p", "call-chat__text"), element("p", "call-chat__state"));
    return node;
  }

  function setText(node, selector, text) {
    var part = node.querySelector(selector);
    if (part.textContent !== text) part.textContent = text;
  }

  function paint(node, item) {
    var picture = [who(item), item.time, item.text, stateWord(item)].join("\u0001");
    if (drawn[item.key] === picture) return;
    drawn[item.key] = picture;
    setText(node, ".call-chat__who", who(item));
    setText(node, ".call-chat__time", item.time);
    setText(node, ".call-chat__text", item.text);
    setText(node, ".call-chat__state", stateWord(item));
  }

  // Draws what changed and nothing else; follows the end of the log only when the reader was there
  // (or when the participant has just sent something).
  function draw(follow) {
    var wasNear = log.scrollHeight - log.scrollTop - log.clientHeight <= NEAR_END_PX;
    items.forEach(function (item, index) {
      var node = nodes[item.key];
      if (!node) {
        node = build(item);
        nodes[item.key] = node;
      }
      paint(node, item);
      if (log.children[index] !== node) log.insertBefore(node, log.children[index] || null);
    });
    if (follow || wasNear) log.scrollTop = log.scrollHeight;
  }

  // ---- what comes in --------------------------------------------------------------------------
  function known(id) {
    return items.some(function (item) {
      return item.id === id;
    });
  }

  function receive(body) {
    var added = false;
    var refused = false;
    (body.messages || []).forEach(function (message) {
      if (typeof message.id !== "number") return;
      cursor = Math.max(cursor, message.id);
      if (known(message.id)) return;
      items.push({
        key: "s" + message.id,
        id: message.id,
        author: message.author === "participant" ? "participant" : "staff",
        text: String(message.text),
        state: String(message.state),
        time: String(message.time),
      });
      added = true;
    });
    var states = body.states || {};
    items.forEach(function (item) {
      var next = states[String(item.id)];
      if (typeof next !== "string" || next === item.state) return;
      item.state = next;
      if (next === "refused") refused = true;
    });
    staff = typeof body.staff === "string" ? body.staff : "";
    closed = body.closed === "ended" || body.closed === "staff" ? body.closed : "";
    var first = !loaded;
    loaded = true;
    if (first) log.setAttribute("aria-live", "off");
    draw(first);
    if (first) {
      window.requestAnimationFrame(function () {
        log.setAttribute("aria-live", "polite");
      });
    }
    if (refused) app.say("say_chat_refused");
    sync(app.state);
    return added;
  }

  // ---- what goes out --------------------------------------------------------------------------
  function newId() {
    var bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
    return Array.prototype.map
      .call(bytes, function (byte) {
        return (byte < 16 ? "0" : "") + byte.toString(16);
      })
      .join("");
  }

  function lengthOf(text) {
    return Array.from(text).length;
  }

  function wording() {
    var rest = maxChars - lengthOf(field.value);
    var text = rest >= 0 ? app.texts.chat_left : app.texts.chat_over;
    counter.textContent = text.replace("{n}", String(Math.abs(rest)));
  }

  function clearProblem() {
    problem.hidden = true;
    problem.textContent = "";
    field.removeAttribute("aria-invalid");
    field.setAttribute("aria-describedby", ["call-chat-keys", "call-chat-left"].join(" "));
  }

  function refuse(code) {
    var text = app.texts["chat_problem_" + code];
    problem.textContent = text;
    problem.hidden = false;
    field.setAttribute("aria-invalid", "true");
    field.setAttribute(
      "aria-describedby",
      ["call-chat-keys", "call-chat-left", "call-chat-problem"].join(" "),
    );
    app.say("chat_problem_" + code);
    field.focus();
  }

  function problemOf(status, body) {
    var error = body && typeof body.error === "string" ? body.error : "";
    if (status === 400 && (error === "empty" || error === "too_long")) return error;
    if (status === 429) return "rate";
    if (status === 409 && error === "full") return "full";
    return "failed";
  }

  function sent(message) {
    var id = pending ? pending.id : newId();
    pending = null;
    if (!known(message.id)) {
      items.push({
        key: "c" + id,
        id: message.id,
        author: "participant",
        text: String(message.text),
        state: String(message.state),
        time: String(message.time),
      });
    }
    field.value = "";
    wording();
    clearProblem();
    draw(true);
  }

  function send() {
    if (sending || reason(app.state)) return;
    var text = field.value;
    if (!text.trim()) return refuse("empty");
    if (lengthOf(text) > maxChars) return refuse("too_long");
    clearProblem();
    if (!pending || pending.text !== text) pending = { id: newId(), text: text };
    sending = true;
    var status = 0;
    window
      .fetch(chatUrl, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: pending.id, text: text }),
      })
      .then(function (response) {
        status = response.status;
        return response.json().catch(function () {
          return {};
        });
      })
      .then(
        function (body) {
          sending = false;
          if (status === 200 && body.message) {
            sent(body.message);
            return;
          }
          if (status === 409 && body.error === "closed") {
            pending = null;
            closed = closed || "staff";
            sync(app.state);
            return;
          }
          refuse(problemOf(status, body));
        },
        function () {
          sending = false;
          refuse("failed");
        },
      );
  }

  // ---- the poll's part --------------------------------------------------------------------------
  // Whether the page still needs a turn: not before the start, not once the chat is closed and read,
  // and on a page that is over only until it has been read once.
  function wants(state) {
    if (!enabled() || state.screen === "start") return false;
    if (OVER[state.screen] && finalRead) return false;
    return !(closed && loaded);
  }

  function waiting() {
    return items
      .filter(function (item) {
        return item.author === "participant" && item.state === "sent" && typeof item.id === "number";
      })
      .map(function (item) {
        return item.id;
      });
  }

  // One read for the poll's turn: resolves true when it went well, false otherwise (so the poll
  // waits longer), and never rejects.
  function turn(signal) {
    var query = "?since=" + cursor;
    var ids = waiting();
    if (ids.length) query += "&waiting=" + ids.join(",");
    var address = chatUrl + query;
    return window
      .fetch(address, { method: "GET", credentials: "same-origin", cache: "no-store", signal: signal })
      .then(function (response) {
        if (!response.ok) throw new Error("refused");
        return response.json();
      })
      .then(function (body) {
        receive(body);
        if (OVER[app.state.screen]) finalRead = true;
        return true;
      })
      .catch(function () {
        return false;
      });
  }

  // ---- how the side bar looks -------------------------------------------------------------------
  function sentence(why, state) {
    var text = app.texts["chat_closed_" + why];
    var going = state.screen === "lobby" || state.screen === "call";
    return why === "staff" && going ? text + " " + app.texts.chat_closed_goes_on : text;
  }

  // Called by `render` after every change of the page's state.
  function sync(state) {
    if (!enabled() || !box) return;
    if (state.screen !== screen) {
      screen = state.screen;
      finalRead = false;
    }
    box.hidden = state.screen === "start";
    if (box.hidden) return;
    var why = reason(state);
    var wasClosed = !closedBox.hidden;
    var hadFocus = form.contains(document.activeElement);
    form.hidden = Boolean(why);
    closedBox.hidden = !why;
    if (why) {
      var words = sentence(why, state);
      if (closedText.textContent !== words) closedText.textContent = words;
      if (!wasClosed && hadFocus) closedBox.focus();
      if (!announcedClosed) {
        announcedClosed = true;
        app.say("say_chat_closed");
      }
    } else {
      announcedClosed = false;
    }
    if (loaded) draw(false);
  }

  function reset() {
    if (!enabled()) return;
    items = [];
    nodes = {};
    drawn = {};
    cursor = 0;
    staff = "";
    closed = "";
    loaded = false;
    pending = null;
    sending = false;
    announcedClosed = false;
    finalRead = false;
    screen = "start";
    log.replaceChildren();
    field.value = "";
    clearProblem();
    wording();
  }

  function init() {
    chatUrl = app.root.dataset.callChatUrl || "";
    maxChars = parseInt(app.root.dataset.callChatMax, 10) || 0;
    if (!chatUrl || !maxChars) {
      chatUrl = "";
      return;
    }
    box = app.$("[data-call-chat]");
    log = app.$("[data-call-chat-log]");
    form = app.$("[data-call-chat-form]");
    field = app.$("[data-call-chat-text]");
    counter = app.$("[data-call-chat-left]");
    problem = app.$("[data-call-chat-problem]");
    closedBox = app.$("[data-call-chat-closed]");
    closedText = app.$("[data-call-chat-closed-text]");
    wording();
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      send();
    });
    field.addEventListener("input", function () {
      wording();
      if (!problem.hidden) clearProblem();
    });
    // Enter sends; Shift and Enter break the line. A key that is part of composing a character
    // (an input method) is left alone, and so is Enter with another modifier.
    field.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      event.preventDefault();
      send();
    });
  }

  app.chat = { init: init, sync: sync, wants: wants, turn: turn, reset: reset };
})();

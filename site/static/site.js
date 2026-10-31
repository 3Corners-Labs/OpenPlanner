/* openplanner.nl (ADR 0087): progressive enhancement only. Without this file the page is
   complete. It adds the interactive booking demo, the stepping of the animated tour and the
   reveal of sections on scroll. It sets no cookie, uses no storage and sends nothing. */
(function () {
  "use strict";
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.documentElement.classList.add("is-js");

  /* ---- The animated tour ------------------------------------------------- */
  var tour = document.querySelector("[data-tour]");
  if (tour) {
    tour.classList.add("is-js");
    var scenes = tour.querySelectorAll("[data-scene]");
    var picks = tour.querySelectorAll("[data-tour-go]");
    var toggle = tour.querySelector("[data-tour-toggle]");
    var icon = tour.querySelector("[data-tour-icon]");
    var label = tour.querySelector("[data-tour-label]");
    var words = tour.querySelector("[data-tour-play]");
    var scene = tour.querySelector(".tour__scene");
    var progress = document.createElement("div");
    progress.className = "tour__progress";
    progress.innerHTML = "<i></i>";
    scene.appendChild(progress);
    var current = 0, timer = null, playing = false;

    function show(n) {
      current = (n + scenes.length) % scenes.length;
      scenes.forEach(function (s, i) { s.classList.toggle("is-active", i === current); });
      picks.forEach(function (p, i) { p.setAttribute("aria-pressed", i === current ? "true" : "false"); });
      var bar = progress.firstChild;
      bar.style.animation = "none";
      void bar.offsetWidth; // restart the progress bar
      bar.style.animation = "";
    }
    function play() {
      if (reduced) { return; }
      playing = true;
      tour.classList.add("is-playing");
      clearInterval(timer);
      timer = setInterval(function () { show(current + 1); }, 6000);
      toggle.setAttribute("aria-pressed", "true");
      icon.setAttribute("href", "#i-pause");
      label.textContent = words.getAttribute("data-tour-pause");
    }
    function pause() {
      playing = false;
      tour.classList.remove("is-playing");
      clearInterval(timer);
      toggle.setAttribute("aria-pressed", "false");
      icon.setAttribute("href", "#i-play");
      label.textContent = words.getAttribute("data-tour-play");
    }
    picks.forEach(function (p) {
      p.addEventListener("click", function () { show(Number(p.getAttribute("data-tour-go"))); if (playing) { play(); } });
    });
    toggle.hidden = reduced;
    toggle.addEventListener("click", function () { playing ? pause() : play(); });
    show(0);
    if (!reduced) {
      // Start when the tour scrolls into view, so the first scene is not missed.
      var seen = false;
      var tio = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting && !seen) { seen = true; play(); }
          if (!e.isIntersecting && playing) { pause(); seen = false; }
        });
      }, { threshold: 0.4 });
      tio.observe(tour);
    }
  }

  /* ---- The interactive booking demo ------------------------------------- */
  var demo = document.querySelector("[data-demo]");
  if (!demo) { return; }
  var t = JSON.parse(demo.getAttribute("data-i18n"));
  var frame = demo.querySelector("[data-demo-frame]");
  var panel = demo.querySelector("[data-demo-panel]");
  var steps = demo.querySelectorAll("[data-step]");
  var live = demo.querySelector("[data-demo-live]");
  frame.hidden = false;

  var state = { day: null, time: null };
  var days = nextDays(7);
  var times = ["09:00", "09:30", "10:00", "10:30", "11:00", "13:30", "14:00", "14:30", "15:00"];

  function nextDays(n) {
    var out = [], d = new Date();
    d.setHours(0, 0, 0, 0);
    for (var i = 0; i < n; i++) {
      var day = new Date(d.getTime() + i * 86400000);
      var dow = (day.getDay() + 6) % 7; // Monday = 0
      var free = dow > 4 ? 0 : ((day.getDate() * 7 + i * 3) % 8) + 1;
      out.push({ date: day, dow: dow, free: free, closed: dow > 4, today: i === 0 });
    }
    return out;
  }
  function fmt(s, vars) { return s.replace(/\{(\w+)\}/g, function (_, k) { return vars[k]; }); }
  function dayLabel(d) { return t.weekdays[d.dow] + " " + d.date.getDate() + " " + t.months[d.date.getMonth()]; }
  function setStep(name) {
    var passed = true;
    steps.forEach(function (li) {
      var is = li.getAttribute("data-step") === name;
      if (is) { li.setAttribute("aria-current", "step"); passed = false; } else { li.removeAttribute("aria-current"); }
      li.classList.toggle("is-done", passed && !is);
    });
  }
  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") { e.textContent = attrs[k]; } else if (k === "html") { e.innerHTML = attrs[k]; } else { e.setAttribute(k, attrs[k]); }
    });
    (children || []).forEach(function (c) { e.appendChild(c); });
    return e;
  }
  function render(name, focus) {
    setStep(name);
    panel.innerHTML = "";
    var h = el("h3", { text: t["step_" + name], tabindex: "-1" });
    panel.appendChild(h);
    if (name === "day") {
      var list = el("ul", { class: "demo__days" });
      days.forEach(function (d) {
        var b = el("button", { type: "button", class: "demo__day" + (d.today ? " is-today" : "") }, [
          el("small", { text: t.weekdays[d.dow] }),
          el("b", { text: String(d.date.getDate()) }),
          el("small", { text: d.closed ? t.closed : (d.free ? fmt(t.free, { n: d.free }) : t.full) })
        ]);
        b.setAttribute("aria-label", dayLabel(d) + (d.today ? ", " + t.today : "") + ", " + (d.closed ? t.closed : (d.free ? fmt(t.free, { n: d.free }) : t.full)));
        if (!d.free) { b.disabled = true; }
        b.addEventListener("click", function () { state.day = d; live.textContent = fmt(t.announce_day, { day: dayLabel(d) }); render("time"); });
        list.appendChild(el("li", {}, [b]));
      });
      panel.appendChild(list);
    } else if (name === "time") {
      panel.appendChild(el("p", { class: "demo__summary", text: dayLabel(state.day) }));
      var slots = el("ul", { class: "demo__slots" });
      times.slice(0, Math.max(3, state.day.free)).forEach(function (tm) {
        var b = el("button", { type: "button", class: "demo__slot", text: tm });
        b.addEventListener("click", function () { state.time = tm; live.textContent = fmt(t.announce_time, { time: tm }); render("form"); });
        slots.appendChild(el("li", {}, [b]));
      });
      panel.appendChild(slots);
      panel.appendChild(nav("day"));
    } else if (name === "form") {
      panel.appendChild(el("p", { class: "demo__summary", text: dayLabel(state.day) + " · " + state.time }));
      var form = el("form", { novalidate: "" });
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (!form.checkValidity()) { form.reportValidity(); return; }
        live.textContent = fmt(t.announce_done, { day: dayLabel(state.day), time: state.time });
        render("done");
      });
      [["first_name", "text", "given-name"], ["last_name", "text", "family-name"], ["email", "email", "email"]].forEach(function (f, i) {
        var id = "demo-" + f[0];
        form.appendChild(el("label", { class: "demo__field", for: id }, [
          el("span", { text: t[f[0]] }),
          el("input", { id: id, type: f[1], autocomplete: f[2], required: "", name: f[0] })
        ]));
      });
      // The honeypot of the real form (CLAUDE.md, security baseline), shown here as it is there: invisible.
      form.appendChild(el("input", { type: "text", name: "website", tabindex: "-1", autocomplete: "off", "aria-hidden": "true", class: "demo__hp" }));
      var navRow = nav("time");
      navRow.appendChild(el("button", { type: "submit", class: "btn btn--primary", text: t.submit }));
      form.appendChild(navRow);
      panel.appendChild(form);
    } else {
      var done = el("div", { class: "demo__done" }, [
        el("p", { class: "demo__summary", text: dayLabel(state.day) + " · " + state.time }),
        el("p", { text: t.done_text }),
        el("ul", {}, [t.done_meeting, t.done_task, t.done_purge].map(function (s) {
          return el("li", { html: '<svg class="icon" aria-hidden="true"><use href="#i-check"/></svg>' }, [el("span", { text: s })]);
        }))
      ]);
      h.textContent = t.done_title;
      panel.appendChild(done);
      var again = el("button", { type: "button", class: "btn", text: t.restart });
      again.addEventListener("click", function () { state = { day: null, time: null }; render("day"); });
      panel.appendChild(again);
    }
    if (focus !== false) { h.focus({ preventScroll: true }); }
  }
  function nav(backTo) {
    var row = el("div", { class: "demo__nav" });
    var back = el("button", { type: "button", class: "btn btn--ghost", text: t.back });
    back.addEventListener("click", function () { render(backTo); });
    row.appendChild(back);
    return row;
  }
  render("day", false);
})();

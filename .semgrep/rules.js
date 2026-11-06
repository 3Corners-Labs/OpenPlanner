// Test cases for rule no-script-markup-sink in rules.yml: `make semgrep` runs them.
function examples(el, text, code, frame, range) {
  // ruleid: no-script-markup-sink
  el.innerHTML = text;

  // ruleid: no-script-markup-sink
  el.outerHTML = text;

  // ruleid: no-script-markup-sink
  el.insertAdjacentHTML("beforeend", text);

  // ruleid: no-script-markup-sink
  document.write(text);

  // ruleid: no-script-markup-sink
  el.innerHTML += text;

  // ruleid: no-script-markup-sink
  frame.srcdoc = text;

  // ruleid: no-script-markup-sink
  range.createContextualFragment(text);

  // ruleid: no-script-markup-sink
  Function(code);

  // ruleid: no-script-markup-sink
  eval(code);

  // ruleid: no-script-markup-sink
  new Function(code);

  // ok: no-script-markup-sink
  el.textContent = text;

  // ok: no-script-markup-sink
  el.appendChild(document.createTextNode(text));
}

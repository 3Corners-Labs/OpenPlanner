/**
 * Tiny DOM helpers. Text always goes in as text nodes (never innerHTML), so nothing the API
 * returns can become markup inside the shadow root.
 */

/**
 * Create an element with attributes and children.
 * @param {string} tag Element name.
 * @param {Record<string, string | boolean | null | undefined>} [attributes] `true` sets an
 *   empty attribute; `false`, `null` and `undefined` leave it out.
 * @param {...(Node | string | null | false | undefined)} children Strings become text nodes.
 * @returns {HTMLElement}
 */
export function h(tag, attributes = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === true) element.setAttribute(name, '');
    else if (value !== false && value !== null && value !== undefined) element.setAttribute(name, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === false || child === undefined) continue;
    element.append(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return element;
}

/**
 * A heading at `level` (2–6), focusable for focus management after each step.
 * @param {number} level
 * @param {string} id
 * @param {string} text
 * @returns {HTMLElement}
 */
export function heading(level, id, text) {
  const tag = `h${Math.min(6, Math.max(2, level))}`;
  return h(tag, { id, tabindex: '-1', part: 'heading' }, text);
}

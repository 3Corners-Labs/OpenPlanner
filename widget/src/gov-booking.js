/**
 * <gov-booking organisation="…" group="…">: the booking flow of one group on a host website
 * (ADR 0035). One custom element with a shadow root; everything it shows comes from the widget
 * API on the origin this script was loaded from, already translated for the chosen locale.
 *
 * Attributes:
 *   organisation   the organisation's slug (required)
 *   group          the group's slug (required)
 *   lang           "nl" or "en"; else the nearest lang attribute, else the page's, else "nl"
 *   heading-level  level of the step headings, 2–6 (default 2), to fit the host's outline
 *
 * Styling: the host sets --booking-* custom properties and ::part() rules (docs/widget.md).
 * The stylesheet is a constructable stylesheet, so the host's CSP needs no 'unsafe-inline'.
 * Steps: calendar → form → done; focus moves to each step's heading and an aria-live region
 * announces availability, errors and the result.
 */
import css from './styles.css';
import { ApiError, createApi } from './api.js';
import { renderCalendar } from './calendar.js';
import { h, heading } from './dom.js';
import { renderForm } from './form.js';

const ORIGIN = new URL(import.meta.url).origin;
const LOCALES = ['nl', 'en'];
const STEP_KEYS = ['steps.time', 'steps.details', 'steps.done'];
const ERROR_TEXTS = {
  slot_full: 'error.slot_full',
  booking_limit_reached: 'error.booking_limit_reached',
  slot_unavailable: 'error.slot_unavailable',
  outside_booking_window: 'error.outside_booking_window',
  group_closed: 'error.group_closed',
  database_busy: 'error.database_busy',
  rate_limited: 'error.rate_limited',
  maintenance: 'widget.maintenance',
};

let sheet;
function stylesheet() {
  if (!sheet) {
    sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
  }
  return sheet;
}

class GovBooking extends HTMLElement {
  static observedAttributes = ['organisation', 'group', 'lang'];

  connectedCallback() {
    if (!this.shadowRoot) {
      const root = this.attachShadow({ mode: 'open' });
      root.adoptedStyleSheets = [stylesheet()];
      this.live = h('p', { class: 'visually-hidden', 'aria-live': 'polite' });
      this.body = h('div', { class: 'body' });
      this.region = h('section', { class: 'booking', part: 'root' }, this.live, this.body);
      root.append(this.region);
    }
    this.start();
  }

  attributeChangedCallback() {
    if (this.shadowRoot) this.start();
  }

  get level() {
    const level = Number.parseInt(this.getAttribute('heading-level') ?? '2', 10);
    return Number.isInteger(level) ? Math.min(6, Math.max(2, level)) : 2;
  }

  get locale() {
    const candidates = [
      this.getAttribute('lang'),
      this.parentElement?.closest('[lang]')?.getAttribute('lang'),
      document.documentElement.lang,
    ];
    for (const value of candidates) {
      const code = (value ?? '').slice(0, 2).toLowerCase();
      if (LOCALES.includes(code)) return code;
    }
    return 'nl';
  }

  t(key) {
    return this.group?.texts[key] ?? this.basic?.[key] ?? '';
  }

  announce(text) {
    // Clear first, so the same message is announced again.
    this.live.textContent = '';
    window.setTimeout(() => { this.live.textContent = text; }, 50);
  }

  show(content, focusId) {
    this.body.replaceChildren(content);
    this.region.removeAttribute('aria-busy');
    if (focusId) this.shadowRoot.getElementById(focusId)?.focus();
  }

  async start() {
    const organisation = this.getAttribute('organisation');
    const group = this.getAttribute('group');
    if (!organisation || !group) return;
    // A newer start (attributes changed meanwhile) wins; an older answer is dropped.
    const run = (this.run = (this.run ?? 0) + 1);
    this.api = createApi(ORIGIN, organisation, group, this.locale);
    this.group = null;
    this.region.setAttribute('aria-busy', 'true');
    this.body.replaceChildren();
    try {
      // The few texts needed before the group is known (loading, unavailable, retry).
      const basic = (await this.api.texts()).texts;
      if (run !== this.run) return;
      this.basic = basic;
      this.region.setAttribute('aria-label', this.t('widget.region'));
      this.body.replaceChildren(h('p', {}, this.t('widget.loading')));
      const found = await this.api.group();
      if (run !== this.run) return;
      this.group = found;
      await this.calendar();
    } catch (error) {
      if (run === this.run) this.unavailable(error);
    }
  }

  unavailable(error) {
    // Maintenance (ADR 0038) ends by itself: say so, and let the participant try again.
    const code = error instanceof ApiError ? error.code : null;
    const text = this.t(code === 'maintenance' ? 'widget.maintenance' : 'widget.unavailable');
    const content = h('div', { class: 'notice', part: 'notice' }, h('p', {}, text));
    if (code === 'network' || code === 'maintenance') {
      const retry = h('button', { type: 'button', class: 'button', part: 'button' }, this.t('widget.retry'));
      retry.addEventListener('click', () => this.start());
      content.append(h('p', {}, retry));
    }
    this.show(content);
    this.announce(text);
  }

  steps(current) {
    return h('ol', { class: 'steps', 'aria-label': this.t('steps.label') }, ...STEP_KEYS.map((key, index) => {
      const number = index + 1;
      return h('li', { 'aria-current': number === current ? 'step' : null, class: number < current ? 'step--done' : null },
        number < current ? h('span', { class: 'visually-hidden' }, `${this.t('steps.completed')}: `) : null,
        this.t(key));
    }));
  }

  closure() {
    const closure = this.group.closure;
    if (!closure) return null;
    return h('section', { class: 'notice notice--warning', 'aria-labelledby': 'closure-heading', part: 'notice' },
      heading(this.level + 1, 'closure-heading', closure.heading ?? closure.title),
      h('p', {}, h('strong', {}, closure.period)),
      ...closure.paragraphs.map((p) => h('p', {}, p)),
    );
  }

  async calendar(month, day, focus = 'title') {
    let data;
    try {
      data = await this.api.availability(month, day);
    } catch (error) {
      this.unavailable(error);
      return;
    }
    this.month = data.month.month;
    this.day = data.times?.date ?? null;
    const view = renderCalendar(data, (key) => this.t(key), this.level, {
      selectedDay: () => this.day,
      month: (target, fromGrid) => this.calendar(target, undefined, fromGrid ? 'grid' : 'month'),
      day: (date) => this.calendar(this.month, date, 'times'),
      time: (start, label) => this.form({ start, label, values: {}, errors: [] }),
    });
    const more = h('p', { class: 'more' }, h('a', { href: this.group.page_url, part: 'link' }, this.t('widget.more')));
    this.show(h('div', {},
      heading(this.level, 'title', this.group.name),
      h('p', { class: 'lead' }, this.group.intro),
      this.closure(),
      this.steps(1),
      view,
      more,
    ), focus === 'title' ? null : focus === 'times' ? 'times-heading' : focus === 'month' ? 'month-heading' : null);
    if (focus === 'grid') this.shadowRoot.querySelector('.calendar__day[tabindex="0"]')?.focus();
    if (focus === 'times') this.announce(data.times ? `${data.times.heading}: ${data.times.count}` : this.t('picker.no_times'));
  }

  form(state) {
    this.chosen = state;
    const view = renderForm(this.group, state, (key) => this.t(key), this.level, {
      back: () => this.calendar(this.month, this.day, 'times'),
      submit: (values) => this.submit(values),
    });
    this.show(h('div', {}, heading(this.level, 'form-title', this.t('book.title')), this.steps(2), view),
      state.errors.length ? null : 'form-title');
    if (state.errors.length) {
      this.shadowRoot.querySelector('.error-summary')?.focus();
      this.announce(state.errors.map((e) => e.message).join(' '));
    }
  }

  async submit(values) {
    const details = {
      start: this.chosen.start,
      first_name: values.first_name ?? '',
      last_name: values.last_name ?? '',
      email: values.email ?? '',
      answers: answers(this.group.fields, values),
    };
    if ('phone' in values) details.phone = values.phone;
    if (values[this.group.honeypot]) details.website = values[this.group.honeypot];
    try {
      const booking = await this.api.book(details);
      this.done(booking);
    } catch (error) {
      const coded = error instanceof ApiError && ['invalid_contact', 'invalid_answer'].includes(error.code);
      const field = coded ? error.field : null;
      this.form({ ...this.chosen, values, errors: [{ field, message: this.message(error, field) }] });
    }
  }

  message(error, field) {
    if (error.code === 'invalid_answer' && field) {
      const label = this.group.fields.find((f) => f.name === field)?.label;
      return label ? `${label}: ${this.t('error.invalid_answer')}` : this.t('error.invalid_answer');
    }
    const key = field ? `error.field.${field}` : ERROR_TEXTS[error.code] ?? 'error.invalid_request';
    return this.t(key);
  }

  done(booking) {
    this.show(h('div', {},
      heading(this.level, 'done-title', this.t('widget.done_title')),
      this.steps(3),
      h('div', { class: 'card done', role: 'status', part: 'notice' },
        h('p', {}, h('strong', {}, booking.label)),
        h('p', {}, booking.message),
      ),
    ), 'done-title');
  }
}

/**
 * The answers to the group's custom fields (inputs named `q_<key>`), keyed without the prefix.
 * An unticked checkbox is not in the form data and is sent as empty.
 * @param {{ name: string }[]} fields
 * @param {Record<string, string>} values
 * @returns {Record<string, string>}
 */
function answers(fields, values) {
  return Object.fromEntries(fields
    .filter((field) => field.name.startsWith('q_'))
    .map((field) => [field.name.slice(2), values[field.name] ?? '']));
}

if (!customElements.get('gov-booking')) customElements.define('gov-booking', GovBooking);

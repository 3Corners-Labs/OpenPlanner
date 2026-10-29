/**
 * Step 1: the month grid and the free times of a day (ADR 0020 behaviour inside the shadow
 * root). The grid is one tab stop with a roving tabindex: arrow keys move a day or a week,
 * Home/End go to the start or end of the week, Page Up/Down change month, Enter or Space
 * chooses a free day. Days without free times stay focusable but are aria-disabled.
 */
import { h, heading } from './dom.js';

/**
 * Render the month and the times.
 * @param {object} data The availability answer of the API.
 * @param {(key: string) => string} t Text of a catalogue key.
 * @param {number} level Heading level of the step.
 * @param {{ month: (month: string) => void, day: (date: string) => void,
 *           time: (start: string, label: string) => void }} on Callbacks.
 * @returns {HTMLElement}
 */
export function renderCalendar(data, t, level, on) {
  const { month, times, first } = data;
  const parts = [];
  if (first) {
    parts.push(
      h('div', { class: 'quick', part: 'quick-pick' },
        h('p', {}, h('strong', {}, t('picker.first')), ' ', first.label),
        h('button', { type: 'button', class: 'button', part: 'button' }, t('picker.first_choose')),
      ),
    );
    parts[0].querySelector('button').addEventListener('click', () => on.time(first.start, first.label));
  }
  parts.push(h('div', { class: 'picker' }, monthCard(month, t, level, on), timesCard(times, t, level, on)));
  return h('div', {}, ...parts);
}

function step(label, target, rel, onMonth) {
  if (!target) return h('span', { class: 'calendar__step calendar__step--off', 'aria-hidden': 'true' }, rel === 'prev' ? '‹' : '›');
  const button = h('button', { type: 'button', class: 'calendar__step', part: 'button', 'data-rel': rel },
    h('span', { 'aria-hidden': 'true' }, rel === 'prev' ? '‹' : '›'),
    h('span', { class: 'visually-hidden' }, label),
  );
  button.addEventListener('click', () => onMonth(target));
  return button;
}

function dayButton(day, selected, t) {
  const state = day.free ? t('picker.day_free') : day.later ? t('picker.day_later') : t('picker.day_full');
  const label = [day.label, day.today ? t('picker.today') : '', state].filter(Boolean).join(', ');
  const classes = ['calendar__day', day.free ? 'calendar__day--free' : day.later ? 'calendar__day--later' : 'calendar__day--none'];
  if (day.today) classes.push('calendar__day--today');
  return h('button', {
    type: 'button',
    class: classes.join(' '),
    part: day.free ? (selected ? 'day day-free day-selected' : 'day day-free') : 'day',
    'data-day': day.date,
    'aria-disabled': day.free ? null : 'true',
    'aria-current': selected ? 'date' : null,
    'aria-label': label,
    tabindex: '-1',
  }, h('span', { 'aria-hidden': 'true' }, String(day.number)));
}

function monthCard(month, t, level, on) {
  const selected = on.selectedDay();
  const rows = month.weeks.map((week) =>
    h('tr', { role: 'row' }, ...week.map((day) =>
      h('td', { role: 'gridcell', 'aria-selected': day && day.date === selected ? 'true' : 'false' },
        day ? dayButton(day, day.date === selected, t) : null),
    )),
  );
  const table = h('table', { class: 'calendar__grid', role: 'grid', 'aria-labelledby': 'month-heading', 'aria-describedby': 'calendar-legend', part: 'calendar' },
    h('thead', {}, h('tr', { role: 'row' }, ...month.weekdays.map((w) => h('th', { scope: 'col', role: 'columnheader' }, h('abbr', { title: w.name }, w.short))))),
    h('tbody', {}, ...rows),
  );
  roving(table, on, month);
  const legend = [h('li', {}, h('span', { class: 'swatch swatch--free', 'aria-hidden': 'true' }), t('picker.legend_free')),
    h('li', {}, h('span', { class: 'swatch swatch--selected', 'aria-hidden': 'true' }), t('picker.legend_selected'))];
  if (month.has_later) legend.push(h('li', {}, h('span', { class: 'swatch swatch--later', 'aria-hidden': 'true' }), t('picker.legend_later')));
  return h('section', { class: 'card calendar', 'aria-labelledby': 'month-heading' },
    h('div', { class: 'calendar__nav' },
      step(t('picker.previous_month'), month.previous, 'prev', on.month),
      heading(level + 1, 'month-heading', month.heading),
      step(t('picker.next_month'), month.next, 'next', on.month),
    ),
    table,
    h('div', { class: 'calendar__legend', id: 'calendar-legend' },
      h('ul', { class: 'legend' }, ...legend),
      h('p', {}, `${month.horizon} ${t('picker.legend')}`),
    ),
  );
}

/** One tab stop for the grid; keys move focus between the days (see the module docstring). */
function roving(table, on, month) {
  const days = [...table.querySelectorAll('.calendar__day')];
  if (!days.length) return;
  const start = days.find((d) => d.getAttribute('aria-current')) ?? days.find((d) => !d.hasAttribute('aria-disabled')) ?? days[0];
  start.tabIndex = 0;
  const column = (day) => [...day.parentElement.parentElement.children].indexOf(day.parentElement);
  const moveTo = (index) => {
    const next = days[Math.max(0, Math.min(days.length - 1, index))];
    days.forEach((d) => { d.tabIndex = d === next ? 0 : -1; });
    next.focus();
  };
  table.addEventListener('keydown', (event) => {
    const current = event.target.closest('.calendar__day');
    if (!current) return;
    const index = days.indexOf(current);
    const moves = {
      ArrowLeft: index - 1,
      ArrowRight: index + 1,
      ArrowUp: index - 7,
      ArrowDown: index + 7,
      Home: index - column(current),
      End: index + (6 - column(current)),
    };
    if (event.key in moves) {
      event.preventDefault();
      moveTo(moves[event.key]);
    } else if (event.key === 'PageUp' || event.key === 'PageDown') {
      const target = event.key === 'PageUp' ? month.previous : month.next;
      event.preventDefault();
      if (target) on.month(target, true);
    }
  });
  table.addEventListener('click', (event) => {
    const day = event.target.closest('.calendar__day');
    if (day && !day.hasAttribute('aria-disabled')) on.day(day.dataset.day);
  });
}

function timesCard(times, t, level, on) {
  if (!times) {
    return h('section', { class: 'card times', 'aria-labelledby': 'times-heading' },
      heading(level + 1, 'times-heading', t('picker.no_times_title')),
      h('p', {}, t('picker.no_times')),
    );
  }
  const groups = times.groups.map((part) => {
    const buttons = part.times.map((time) => {
      const button = h('button', { type: 'button', class: 'time', part: 'time' }, time.label);
      button.addEventListener('click', () => on.time(time.start, `${times.heading}, ${time.label}`));
      return button;
    });
    return h('fieldset', {}, h('legend', {}, part.heading), h('div', { class: 'times__list' }, ...buttons));
  });
  return h('section', { class: 'card times', 'aria-labelledby': 'times-heading' },
    heading(level + 1, 'times-heading', times.heading),
    h('p', { class: 'times__count' }, times.count),
    ...groups,
  );
}

/**
 * Step 2: the contact form. Only the inputs the API describes (first and last name, email,
 * per group phone, and the group's custom fields), each with a visible label; hints and errors
 * are linked with aria-describedby. The error summary is focused after a refused submit and its
 * links move focus to the input.
 */
import { h, heading } from './dom.js';

/**
 * Render the form for one chosen time.
 * @param {object} group The group answer of the API (fields, honeypot).
 * @param {{ label: string, values: Record<string, string>,
 *           errors: { field: string | null, message: string }[] }} state
 * @param {(key: string) => string} t Text of a catalogue key.
 * @param {number} level Heading level of the step.
 * @param {{ back: () => void, submit: (values: Record<string, string>) => void }} on
 * @returns {HTMLElement}
 */
export function renderForm(group, state, t, level, on) {
  const byField = new Map(state.errors.filter((e) => e.field).map((e) => [e.field, e.message]));
  const inputs = group.fields.map((field) => fieldBlock(field, state.values[field.name] ?? '', byField.get(field.name), t));
  const honeypot = h('div', { class: 'visually-hidden', 'aria-hidden': 'true' },
    h('label', { for: 'op-honeypot' }, t('form.honeypot')),
    h('input', { id: 'op-honeypot', name: group.honeypot, type: 'text', tabindex: '-1', autocomplete: 'off' }),
  );
  const form = h('form', { class: 'card form', novalidate: true, part: 'form' },
    ...inputs,
    honeypot,
    h('p', { class: 'hint' }, t('book.privacy')),
    h('button', { type: 'submit', class: 'button button--large', part: 'button submit' }, t('form.submit')),
  );
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form).entries());
    on.submit(values);
  });
  const change = h('button', { type: 'button', class: 'link', part: 'link' }, t('book.change_slot'));
  change.addEventListener('click', on.back);
  return h('div', {},
    state.errors.length ? summary(state.errors, t, level, form) : null,
    h('div', { class: 'form-layout' },
      h('section', { class: 'summary', 'aria-labelledby': 'selected-heading' },
        heading(level + 1, 'selected-heading', t('book.selected')),
        h('p', {}, state.label),
        h('p', {}, change),
      ),
      form,
    ),
  );
}

function fieldBlock(field, value, error, t) {
  const id = `op-${field.name}`;
  const described = [field.hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ');
  const common = {
    id,
    name: field.name,
    part: 'input',
    required: field.required,
    'aria-required': field.required ? 'true' : null,
    'aria-invalid': error ? 'true' : null,
    'aria-describedby': described || null,
  };
  const label = h('label', { for: id, part: 'label' }, field.label, field.required ? null : h('span', { class: 'optional' }, ` ${t('form.optional')}`));
  const hint = field.hint ? h('p', { class: 'hint', id: `${id}-hint` }, field.hint) : null;
  const message = error ? h('p', { class: 'error', id: `${id}-error`, part: 'error' }, error) : null;
  const classes = ['field', error ? 'field--error' : '', field.kind === 'checkbox' ? 'field--checkbox' : ''].filter(Boolean).join(' ');
  if (field.kind === 'checkbox') {
    return h('div', { class: classes }, message, h('input', { ...common, type: 'checkbox', value: 'on', checked: Boolean(value) }), label, hint);
  }
  return h('div', { class: classes }, label, hint, message, control(field, value, common, t));
}

function control(field, value, common, t) {
  if (field.kind === 'textarea') {
    return h('textarea', { ...common, rows: '5', autocomplete: field.autocomplete }, value);
  }
  if (field.kind === 'select') {
    const options = (field.options ?? []).map((option) => h('option', { value: option.value, selected: option.value === value }, option.label));
    return h('select', { ...common, autocomplete: field.autocomplete }, h('option', { value: '' }, t('form.choose')), ...options);
  }
  return h('input', {
    ...common,
    type: field.type,
    autocomplete: field.autocomplete,
    inputmode: field.inputmode ?? null,
    min: field.min ?? null,
    max: field.max ?? null,
    value,
  });
}

function summary(errors, t, level, form) {
  const items = errors.map((error) => {
    if (!error.field) return h('li', {}, error.message);
    const link = h('a', { href: `#op-${error.field}` }, error.message);
    // Fragment links do not reach into a shadow root: move focus ourselves.
    link.addEventListener('click', (event) => {
      event.preventDefault();
      form.querySelector(`#op-${error.field}`)?.focus();
    });
    return h('li', {}, link);
  });
  return h('div', { class: 'error-summary', role: 'alert', tabindex: '-1', 'aria-labelledby': 'error-heading', part: 'error-summary' },
    heading(level + 1, 'error-heading', t('widget.error_summary')),
    h('ul', {}, ...items),
  );
}

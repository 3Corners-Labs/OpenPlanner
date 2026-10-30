# OpenPlanner — conventions for building designs

OpenPlanner is a group-booking service for a Dutch government contact center. This design system is **styles only**: there are no React components in `window.OpenPlanner`. Build screens with plain JSX elements and the class names and `--booking-*` tokens below; never invent new class names when one of these fits. Visual language: NL Design System — system font, high contrast, always-visible focus ring (yellow `#ffbf47` halo), targets at least 44×44 px on citizen pages and 24×24 px in admin.

The booking widget `<gov-booking>` is **not** part of this bundle (it needs the live widget API). To show it in a design, compose its look from the citizen classes (`steps`, `calendar__grid`, `time-button`, `field`, `button`).

## Two surfaces, one stylesheet

`styles.css` holds both the citizen stylesheet (global) and the admin stylesheet (scoped under `body.admin` / `.admin`).

- **Citizen pages** (booking, reschedule, cancel): no wrapper class. Structure: `<header className="site-header"><p className="site-name">…</p></header><main id="content">…</main>`. Always light.
- **Admin portal**: put `className="admin"` on the root (`body` or the outermost div) plus `data-theme="light"` or `"dark"`. Without `.admin` you get the citizen look. Frame: `admin-shell` › `admin-topbar` (`admin-brand`, `admin-brand__mark`) + `admin-nav` + `admin-main`. Use `admin-shell--no-nav` when there is no side menu.

## Tokens (`var(--booking-*)`, defined on `:root`)

Colours: `--booking-color-text`, `-text-muted`, `-background`, `-page`, `-surface`, `-border`, `-border-strong`, `-accent` (#01689b), `-on-accent`, `-accent-hover`, `-accent-subtle`, `-focus`, `-focus-background`, `-error`, `-error-subtle`, `-success`, `-success-subtle`, `-unavailable`.
Space: `--booking-space-xs|s|m|l|xl`. Shape: `--booking-radius`, `--booking-radius-small`, `--booking-shadow`, `--booking-target`. Type: `--booking-font-family`, `--booking-font-size`, `--booking-line-height`. Widths: `--booking-max-width`, `--booking-text-width`.
The admin portal has its own palette (`--adm-bg`, `--adm-surface`, `--adm-text`, `--adm-text-muted`, `--adm-accent`, `--adm-border`, `--adm-danger`, `--adm-success`, `--adm-warning`, `--adm-radius`); use those inside `.admin`, `--booking-*` elsewhere. Write inline `style` only with these tokens, never with hex values.

## Class vocabulary

| Family | Classes |
|---|---|
| Buttons | `button`, `button--secondary`, `button--danger`, `button--large` (citizen); admin also `button--ghost`, `button--quiet`, `button--small` |
| Form field | `field` › `label` (+ `<span className="optional">`) › `hint` › `error-message` › `input`; `field--error` on the wrapper; `error-summary` at the top of a form; `actions` (citizen) / `form-actions` (admin) |
| Messages | citizen: `notice`, `notice--success`, `notice--warning`; admin: `alert`, `alert--info`, `alert--success`, `alert--error` |
| Content | `card`, `lead`, `eyebrow`, `summary`, `details`, `data-table` inside `table-scroll`, `visually-hidden`, `skip-link` |
| Booking flow | `steps` (an `<ol>`, current item `aria-current="step"`, done items `step--done`), `calendar__nav`, `calendar__grid`, `calendar__day` (`--today`, `--none`, `--later`), `times`, `times__list`, `time-button`, `legend` |
| Admin | `breadcrumbs`, `page-header`, `page-header__lead`, `page-header__actions`, `tabs`, `tabs--sub`, `card-grid`, `card__header`, `chip` (`--accent`, `--success`, `--warning`, `--danger`), `form-grid`, `form-section`, `empty-state`, `muted`, `stack` |

## Accessibility rules the design must keep

Every input has a visible `<label htmlFor>`; hints and errors are linked with `aria-describedby`; errors put `aria-invalid="true"` on the input; colour is never the only signal (the error style adds an icon and a bar). No time limits, no CAPTCHA. User-facing copy is Dutch by default.

## Example (admin)

```jsx
<div className="admin" data-theme="light">
  <main className="admin-main">
    <div className="page-header"><div>
      <h1>Wmo-loket</h1>
      <p className="page-header__lead">Openingstijden, agenda en berichten van deze groep.</p>
    </div></div>
    <div className="alert alert--success"><p>De openingstijden zijn opgeslagen.</p></div>
    <div className="card">
      <h2>Instellingen</h2>
      <p><span className="chip chip--success">Actief</span></p>
      <div className="field">
        <label htmlFor="name">Naam van de groep</label>
        <input id="name" defaultValue="Wmo-loket" />
      </div>
      <p className="form-actions">
        <button className="button">Opslaan</button>{' '}
        <button className="button button--secondary">Annuleren</button>
      </p>
    </div>
  </main>
</div>
```

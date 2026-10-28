# OpenPlanner manual / handleiding

What the admin portal can do, screen by screen. The same text appears in the portal under
**Handleiding** and behind the (i) of each input (ADR 0033). Citizens never need this manual:
their pages explain themselves.

Wat het beheerportaal kan, per scherm. Dezelfde tekst staat in het portaal onder
**Handleiding** en achter de (i) bij elk invoerveld (ADR 0033). Burgers hebben deze
handleiding niet nodig: hun pagina's leggen zichzelf uit.

| Screen / scherm | Nederlands | English |
|---|---|---|
| Group settings / groepsinstellingen | [Groepsinstellingen](nl/group-settings.md) | [Group settings](en/group-settings.md) |
| Branding / huisstijl | [Huisstijl](nl/branding.md) | [Branding](en/branding.md) |
| Rights matrix / rechtenmatrix | [Rechtenmatrix](nl/rights-matrix.md) | [Rights matrix](en/rights-matrix.md) |
| Alternative contact options / contactalternatieven | [Contactalternatieven](nl/organisation-contacts.md) | [Alternative contact options](en/organisation-contacts.md) |
| A team's contact options / contactalternatieven van een team | [Contactalternatieven van een team](nl/group-contacts.md) | [A team's contact options](en/group-contacts.md) |

## Writing a topic

- One file per screen, in `nl/` and `en/`, with the same file name. The name is the topic in
  `app/web/help.py` (`TOPICS`), which also lists the screen's routes.
- The first `#` heading is the title.
- Explain an input in its own section, with a marker on the line before the heading. The
  marker holds the input's `name`:

  ```text
  <!-- field: slot_minutes -->
  ### Duur van een gesprek
  ```

  The section ends at the next heading of the same or a higher level. Both languages mark the
  same inputs.
- Do not align table columns (`|:---|`): that needs inline styles, which the CSP blocks.
- Link to another topic with its file name (`[agenda](group-agenda.md)`); the portal turns it
  into a portal link. No raw HTML, no headings inside code blocks.
- `tests/web/test_help.py` and `tests/admin/test_help_routes.py` check all of this, and fail
  when an admin screen has no topic and is not listed in `NOT_YET_DOCUMENTED`.

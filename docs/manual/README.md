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
| Custom roles / eigen rollen | [Eigen rollen](nl/roles.md) | [Custom roles](en/roles.md) |
| Retention / bewaartermijnen | [Bewaartermijnen](nl/retention.md) | [Retention](en/retention.md) |
| Staff / medewerkers | [Medewerkers](nl/staff.md) | [Staff](en/staff.md) |
| Audit log / auditlog | [Auditlog](nl/audit.md) | [Audit log](en/audit.md) |
| Alternative contact options / contactalternatieven | [Contactalternatieven](nl/organisation-contacts.md) | [Alternative contact options](en/organisation-contacts.md) |
| Embedding on websites / insluiten op websites | [Insluiten op websites](nl/embedding.md) | [Embedding on websites](en/embedding.md) |
| A team's contact options / contactalternatieven van een team | [Contactalternatieven van een team](nl/group-contacts.md) | [A team's contact options](en/group-contacts.md) |
| Emergency closure of a team / noodsluiting van een team | [Noodsluiting van een team](nl/group-closure.md) | [Emergency closure of a team](en/group-closure.md) |
| Closure scenarios / scenario's voor een noodsluiting | [Scenario's voor een noodsluiting](nl/closure-scenarios.md) | [Scenarios for an emergency closure](en/closure-scenarios.md) |
| Group agenda / agenda van de groep | [Agenda van de groep](nl/group-agenda.md) | [Group agenda](en/group-agenda.md) |
| Maintenance mode / onderhoudsmodus | [Onderhoudsmodus](nl/maintenance.md) | [Maintenance mode](en/maintenance.md) |
| Advanced settings / geavanceerde instellingen | [Geavanceerde instellingen](nl/advanced-settings.md) | [Advanced settings](en/advanced-settings.md) |
| Organisation settings / organisatie-instellingen | [Organisatie-instellingen](nl/organisation-settings.md) | [Organisation settings](en/organisation-settings.md) |

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

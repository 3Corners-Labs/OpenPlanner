# Overview

The overview is the start page of the admin portal. It shows at a glance what needs attention
now, as a grid of **widgets**. Every widget has its own right: you only see the widgets that
belong to your role, and never more than your role may see. Below the grid are the groups you
have rights on, with their staffing shortfalls.

The page works without JavaScript. What you see is the state at the moment of loading;
**Refresh** reloads the page. A later version refreshes the widgets by itself.

The overview never shows citizens' personal data: only counts, group names and times.

## Widgets

- **My role and reach**: your roles and the first rights with the reach where they apply.
  **Open the full list** opens the page *My rights*.
- **Bookings today per group**: the number of confirmed bookings today per group within your
  reach, the busiest group first. The group name opens that group's agenda.
- **Recent configuration changes**: the latest successful changes by admins within your
  reach, with who, when and where. A row opens the whole chain in the audit log; **Open the
  full list** opens the audit log itself.

The widgets *Alerts*, *Queue across my groups*, *Running tasks* and *System status* follow in a
later version.

## Default layout per role

Each role starts with its own order: an agent sees the queue first, the Compliance and privacy
role the changes and the system status. A personal layout comes later (ADR 0062). On a tablet
at most two widgets sit side by side, on a phone one.

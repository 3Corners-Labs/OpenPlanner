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

- **Queue across my groups**: for each group with an own queue within your reach, how many
  citizens wait, how long the longest wait has been and how many staff who may work the queue
  are signed in now. A participant with an appointment counts once they are in the lobby; a
  direct call counts from the request, even before the link is opened. The group name opens
  the queue. Counts and minutes only, never names. Colours for a wait that is too long follow
  with the wait thresholds.
- **Running tasks**: the background jobs that are running now and the jobs that failed after
  their last attempt, with type, state and time. Never the content of a job or the booking it
  belongs to. If something here says *Failed*, something is stuck: tell the installation's
  administrator.
- **System status**: the running version and whether an update is available, whether
  maintenance is on, whether the background worker gave a heartbeat recently, whether
  diagnostic mode is on and which environment the installation runs in. The rows open the
  pages that know more, as far as your role may open them.

The widget *Alerts* follows in a later version.

## Choosing a view

*Bookings today per group* and *Queue across my groups* can be shown in more than one way.
Under the title are the views the widget knows: list, bar or pie for the bookings, table, bar
or line for the queue. Choosing a view reloads the page with that choice in the address (for
example `/admin/?bookings-today=bar`); the other widgets stay as they were. What you do not
choose is the first view. A fixed personal choice comes later (ADR 0062).

Every chart is a picture with a text that names all values, for those who cannot see the
picture. The values are also printed in the chart itself, and colour is never the only sign: a
pie has a legend with the value and the share. Below each chart, **Data as a table** opens the
same figures as a plain table. Above four groups the bars lie down; up to four they stand
upright. The line for the queue connects the groups, so it is not a development over time.

## Default layout per role

Each role starts with its own order: an agent sees the queue first, the Compliance and privacy
role the changes and the system status. A personal layout comes later (ADR 0062). On a tablet
at most two widgets sit side by side, on a phone one.

## Your profile menu

At the top right, your name or initials open the profile menu. It is on every admin page and
works without JavaScript. From top to bottom:

- **Your name and role**, and where the role comes from: your organisation account, so it cannot
  be changed here. **View my rights** opens the page with what your role allows and where.
- **Appearance**: System (follows your device), Light or Dark. The choice brings you back to the
  page you were on, holds for this session and survives signing in again; signing out puts it
  back to System.
- **Choose a language**: Nederlands or English. The browser remembers the choice.
- **Alerts**: whether the bar for outages shows and which alerts pop up (all, warnings and
  outages, outages only, none). **Save** keeps the choice for this session. The alerts themselves
  follow in a later version (ADR 0061); the choice is ready by then.
- **Sign out**.

On a phone the bar keeps only its icons and your initials; your name is in the menu.

## The top bar

To the right of the portal's name sit a few shortcuts, from left to right. You only see what
your role may open:

- **Status** opens *Advanced settings* with the system status (superuser and compliance).
- **Tasks** jumps to the widget *Running tasks* on this overview, with the number of running and
  failed tasks next to it. A number above zero means something is running or stuck. The shortcut
  is only there for admins who have that widget on their overview.
- **Alerts** opens the audit log from level warning up. Once the alert model exists (ADR 0061)
  the shortcut opens the list of alerts, and a number follows.
- **Manual** opens the topic of the page you are on.

On a phone the icons, the number and your initials stay; the words stay in the bar for screen
readers. The number is also part of the shortcut's name, for example *Tasks, 2 running or
failed*. A shortcut without a source for a number shows none.

## Choosing a scope

At the left of the top bar is a button with a filter icon, and on a wide screen your choice next to
it (*Scope: All organisations › All groups*). In it you choose an organisation, a group or both,
and **Show** narrows the overview to that choice: the groups at the bottom, *Bookings today per
group*, *Queue across my groups* and *Recent configuration changes*. *Running tasks*, *System
status* and *My role and reach* are platform-wide and do not change. **Show everything** removes
the choice.

The filter only shows what your role may already see. You choose from the organisations and groups
within your reach; any other value in the address is ignored. It is an ordinary form that changes
nothing. Above the lists the chosen organisation and group are links to their pages (the
organisation page only for those who manage organisations). The choice holds for this session: you
see it again when you return to the overview, and after signing in again the filter is back on
everything. While a choice is active the button has a coloured background, also when only the icon
fits on a smaller screen. For now the filter works on the overview; the other lists have their own
filters.

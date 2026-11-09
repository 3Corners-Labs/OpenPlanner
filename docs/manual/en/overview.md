# Overview

The overview is the start page of the admin portal. It shows at a glance what needs attention
now, as a grid of **widgets**. Every widget has its own right: you only see the widgets that
belong to your role, and never more than your role may see. Below the grid are the groups you
have rights on, with their staffing shortfalls.

The widgets refresh themselves: every few seconds the page asks whether anything changed and
replaces only the widget in which something did. Focus stays where it is. What changes in
*Alerts* is read out for screen reader users. Without JavaScript, or when the superuser switched
refreshing off (*Overview refreshes itself*), you see the state at the moment of loading and
**Refresh** reloads the page. How often the overview refreshes is set by the superuser
(*Overview refresh*, 5 to 60 seconds).

The overview never shows participants' personal data: only counts, group names and times.

## Widgets

- **Alerts**: what needs attention now within your reach, the most urgent first. Each alert says
  its level in words (*Outage*, *Warning*), what it is about, what is wrong and since when. An
  alert stays until the problem is solved; then it goes by itself. The title opens where you can
  act: the whole chain in the audit log, the group's queue or the system status. **All**,
  **Warnings and outages** and **Outages only** filter by level; as bar or pie you see the count
  per level. Alerts come from the storage check, the certificate, the public address, a log
  destination, deleting data after its retention period, the automatic check of an integration,
  a wait that is too long in an own queue and a worker that gives no heartbeat. You only see
  alerts your role may see: platform alerts only with the right to the system status, a queue
  alert only for those who work that queue.
- **My role and reach**: your roles and the first rights with the reach where they apply.
  **Open the full list** opens the page *My rights*.
- **Bookings today per group**: the number of confirmed bookings today per group within your
  reach, the busiest group first. The group name opens that group's agenda.
- **Recent configuration changes**: the latest successful changes by admins within your
  reach, with who, when and where. A row opens the whole chain in the audit log; **Open the
  full list** opens the audit log itself.

- **Queue across my groups**: for each group with an own queue within your reach, how many
  participants wait, how long the longest wait has been, how many appointments nobody picked up
  in time (**Not picked up**, the time set for the group) and how many staff who may work the
  queue are signed in now. A participant with an appointment counts once they are in the lobby; a
  direct call counts from the request, even before the link is opened. The group name opens
  the queue. Counts and minutes only, never names. Under the longest wait it says whether it is
  within the target, above it, or above the limit at which an alert is raised (*Target wait* and
  *Alert when the wait exceeds*, organisation settings; 5 and 8 minutes by default). The colour
  only repeats what is written. When the longest wait crosses the limit there is one alert; it
  goes when the wait is back under the limit.
- **Running tasks**: the background jobs that are running now and the jobs that failed after
  their last attempt, with type, state and time. Never the content of a job or the booking it
  belongs to. If something here says *Failed*, something is stuck: tell the installation's
  administrator.
- **System status**: the running version and whether an update is available, whether
  maintenance is on, whether the background worker gave a heartbeat recently, how much disk space is free and
  how large the database is, whether diagnostic mode is on and which environment the installation runs in. The rows open the
  pages that know more, as far as your role may open them.

## The bar for outages

An **outage** within your reach shows as a bar at the top of every admin page until it is
solved: the disk is almost full, the certificate expired or is not trusted, or the worker gives no
heartbeat. **View** opens where you can act; **Close** hides that one alert for the rest of your
session. When the same problem comes back later, or a warning becomes an outage, the bar shows
again. Closing solves nothing: the alert stays in the *Alerts* widget until the problem is gone.
Warnings are not in the bar, only in the widget.

## Pop-ups

When a new alert arrives while the overview is open, it appears at the bottom right as a pop-up.
Which alerts do so is your choice in the profile menu (warnings and outages by default). A pop-up
stays at least 8 seconds; when you point at it or move focus onto it, it stays until you leave
it. **Close** or Escape removes it at once. Pop-ups appear only on the overview; on other pages
you see outages in the bar.

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
role the changes and the system status. You can make your own layout of it (see below). On a
tablet at most two widgets sit side by side, on a phone one.

## Your own layout

At the top of the overview, next to **Refresh**, are two buttons:

- **Add widget** shows the widgets your rights allow that are not on your overview yet. **Add**
  puts one at the bottom. A widget you may not see is never offered.
- **Edit overview** shows your widgets in their order. **Up** and **Down** move a widget one
  place, **Remove** takes it off. Everything works with the keyboard; there is nothing to drag.

As soon as you change something it is your own layout; only you see it, and it stays when you
log in again. **Restore default layout** removes your own layout, so you see your role's default
again. The *Tasks* and *Alerts* shortcuts in the top bar follow your layout: remove that widget
and its shortcut goes too. If you lose the right to a widget, it disappears from your overview by itself. The number
of widgets has a maximum the superuser sets (*Most widgets on an own overview*, 12 by default). A
layout is a personal preference: it does not go into the audit log, and it holds no personal
data.

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
  outages, outages only, none). **Save** keeps the choice for this session.
- **Sign out**.

On a phone the bar keeps only its icons and your initials; your name is in the menu.

## The top bar

At the start of the bar, before the mark, the **menu button** (three lines) collapses the side
menu to its icons or expands it again. Collapsed, every entry keeps its name: it appears when
you point at the icon or move the keyboard focus to it. On a tablet the menu starts collapsed
and the button expands it; on a wider screen it starts expanded. The choice holds for this
session and survives signing in again, like the appearance. On a phone there is no side menu
and no button: the bar at the bottom has the first entries, *More* the rest.

To the right of the portal's name sit a few shortcuts, from left to right. You only see what
your role may open:

- **Status** opens *Advanced settings* with the system status (superuser and compliance).
- **Tasks** jumps to the widget *Running tasks* on this overview, with the number of running and
  failed tasks next to it. A number above zero means something is running or stuck. The shortcut
  is only there for admins who have that widget on their overview.
- **Alerts** jumps to the *Alerts* widget on this overview, with the number of open alerts within
  your reach next to it.
- **Manual** opens the topic of the page you are on.

On a phone the icons, the number and your initials stay; the words stay in the bar for screen
readers. A screen reader reads the shortcut with its number and what it means, for example
*Tasks 2 running or failed*. A shortcut without a source for a number shows none.

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
organisation page only for those who may open it). The choice holds for this session: you
see it again when you return to the overview, and after signing in again the filter is back on
everything. While a choice is active the button has a coloured background, also when only the icon
fits on a smaller screen. For now the filter works on the overview; the other lists have their own
filters.

## Groups with an emergency closure

Below the widgets are the groups in your reach. **Only groups with an active emergency
closure** narrows that list to the groups that are closed now, with their number. A closed
group carries the label **Emergency closure active**; whoever may manage the closure goes
straight to the group's **Emergency closure** tab through that label. A closure that starts
later does not count yet. The old address of the closures list leads to this filter.

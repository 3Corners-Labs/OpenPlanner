# Embedding on websites

A group always has its own page, which you can link to from your website. If you would rather
show the booking calendar right inside a page of your own website, you can use the widget.
This screen decides on which websites that is allowed. You open it with **Organisations →
organisation → Overview → Embedding**. Organisation admins and the superuser can use it, each
for their own organisations.

Two things are needed before a group appears in the widget:

1. The website is on the organisation's list here.
2. The group switches **Embed on websites** on in its [group settings](group-settings.md).

When the widget is on a website that is not on the list, it shows no appointments and nobody
can book through it. So a copied widget does not work on another website.

<!-- field: origins -->
## Websites, one per line

Enter the start of the web address on each line: the part up to and including the domain
name, such as `https://www.gemeente.nl`. That is how the browser recognises your website.

- Only addresses that start with `https://`.
- No path: `https://www.gemeente.nl/afspraak` is refused. The widget works on every page of
  an allowed website.
- No asterisks. A subdomain is a website of its own: if you want the widget on
  `https://werk.gemeente.nl` and on `https://www.gemeente.nl`, put both on the list.
- A `/` at the end, capitals or the default port `:443` are fine; they are left out.
- An empty line is skipped. If you leave every line empty and save, no website is left on the
  list and the widget works nowhere.

On a test or development installation `http://localhost` with a port is allowed too, so you
can try the widget on your own computer. In production it is refused.

Every change goes to the audit log, with the number of websites added and removed.

## Groups

Below the list you see, for each group of the organisation, whether it appears in the widget.
The group's name links to its settings, where you switch **Embed on websites** on or off.

## Code for your website

At the bottom is the code the web editors paste into a page: one line with the script and,
for each group that is switched on, an element `<gov-booking>`. The script needs to be on a
page only once, also when you show more groups.

The script has an `integrity` value. With it, the browser checks that the script has not
changed. After every OpenPlanner update that value changes: if the web editors copy the code
with `integrity`, they must take over the new value after an update, or the calendar no longer
loads. If you do not want that, leave out `integrity="…"`. The technical explanation for the
web editors is in `docs/widget.md`.

# A team's subjects and routing

A team is three things at once: the agenda a participant books in, the people who hold the
conversation and the queue the work goes to. This screen gives the team the layer that is missing
next to it: the **subject** the participant chooses and a few fixed **routing questions**, such as
the language of the conversation. You can use those choices yourself (in the agenda and the own
queue, and to show only the times when someone is available for the subject) and pass them to the
contact center as codes, so its flow chooses the right agent.

You open the screen with **team → Settings → Subjects and routing**. Organisation admins and the
superuser can change it.

Nothing on this screen is personal data: a subject and an answer to a routing question are a choice
from a fixed list your organisation or OpenPlanner wrote. Free text and the custom fields of the
[booking form](group-booking-form.md) never go to the contact center.

## Subjects

With one or more subjects the form first asks the participant what the appointment is about.
Without subjects the form is as always. The participant also sees the subjects on the team's page,
above the calendar: whoever chooses one there sees only the times of blocks that offer it, and the
form has the subject chosen already. A website that embeds the widget can choose the subject in
advance with the `subject` attribute.

A team has at most the number of subjects the superuser sets (*Subjects per group*, 20 by default).
**Up** and **Down** put them in the order of the form. A subject a block still offers cannot be
removed; remove it from the block first. A removed subject stays on the appointments that chose it,
as a code.

<!-- field: label_nl -->
### Name (Dutch)

What the participant reads, short and in plain language, for example *Verhuizing doorgeven*. At
most 80 characters.

<!-- field: label_en -->
### Name (English)

The same name in English; a participant who opens the page in English reads this one.

<!-- field: code -->
### Code

The subject's fixed name: lower-case letters, digits and hyphens, at most 40 characters, for
example `moving`. Once saved it cannot change. Bookings keep the code, reports count by it, and the
contact center receives the value you map to it below.

## Subjects per block

On a block of the **Staffing** tab and on an exception you choose which subjects the block offers.
Nothing ticked: the block offers every subject, as before. That way you can, for example, staff the
afternoon only with the agents who handle moves. The block's capacity counts for all subjects
together.

## Routing questions

OpenPlanner has a fixed list of questions whose answer is a code:

- **Preferred language**: the languages OpenPlanner shows its pages in (Dutch, English).
- **Way of contact**: with video or by phone.

Tick what the form should ask. An organisation cannot make a routing question of its own; a new
question only comes with a decision (ADR), because it must always be shown that the answer is no
personal data.

## Pass on to the contact center

Per subject and per routing question you choose the name of the variable the contact center's flow
reads, and the value OpenPlanner sends per choice. With Webex Contact Center the variable goes in the
task's `flowSettings`, next to `openplannerGroup`; create a flow variable with exactly this name.
Whatever stays empty is not sent: a subject without a value, a question without a variable.

<!-- field: variable -->
### Variable name

Letters, digits and `_`, starting with a letter, at most 64 characters, for example `subject`. The
name `openplannerGroup` is OpenPlanner's own. Emptying it and saving stops sending.

### Value sent

A code of letters, digits, `.`, `:`, `-` or `_`, at most 64 characters, for example `MOVE`. Often it
is the code itself, or the name of a skill or queue in the contact center.

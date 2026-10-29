# Scenarios for an emergency closure

A scenario is a prepared emergency closure: the notice, a heading, alternative contact options
and an image, ready for when they are needed. When closing you pick a scenario and check the
text, instead of writing and translating everything under pressure.

Open the scenarios through **Emergency closure → Manage scenarios**, for a team or for the
whole organisation.

## Whose a scenario is

- **The organisation's scenarios** are made by an organisation admin (or superuser). Every team
  of the organisation can use them, and when you close the whole organisation you pick one for
  all teams at once.
- **A team's scenarios** are made by a group admin of that team. Only that team uses them.

A group admin sees the organisation's scenarios and can use them, but cannot change or delete
them.

## Making a scenario

Start with a name and the notice in Dutch and English; a heading is optional. On the
scenario's page you then add contact options and an image. The preview at the top shows how
the closure page will look.

<!-- field: scenario_name -->
### Name

How admins recognise the scenario, for instance "Outage", "Strike" or "Building closed". At
most 80 characters. Citizens never see this name.

### Contact options

A scenario can have its own block of contact options, for instance another number during a
strike. Leave it empty and the closure gets the block that applies to the team at that moment
(the team's own block, otherwise the organisation's). Everything in this block is public: use
only general numbers and addresses.

### Image

The same rules as for the image of a closure: PNG, JPEG or WebP of at most 500 kB, with a
description in Dutch and English, or ticked as decoration only. A scenario's image is not
public: only admins see it until a closure with this scenario starts. That way a prepared
"strike" does not leak.

## Closing with a scenario

On the **Emergency closure** screen, under **Start from a scenario**, choose the scenario and
click **Use**. The form below is then filled with the scenario's notice and heading. Adjust the
text if needed, enter the start and end, and close the team.

At the moment of closing the closure takes a **copy** of the scenario: the text from the form,
the contact options and the image. If you later change or delete the scenario, the running
closure keeps showing what citizens received by email.

When you close the whole organisation, you can only pick an organisation scenario. Each team
gets its own copy and can adjust it separately afterwards.

## Examples to copy

New installations have no scenarios. You can copy and adjust these examples.

**Outage**

- Heading: Outage
- Notice (nl): Door een storing kunnen wij u op dit moment niet via video te woord staan. Uw
  afspraak gaat niet door. Kies een nieuwe tijd zodra wij weer open zijn, of neem op een andere
  manier contact met ons op.
- Notice (en): Because of an outage we cannot see you by video at the moment. Your appointment
  cannot go ahead. Choose a new time once we are open again, or contact us another way.

**Strike**

- Heading: Strike
- Notice (nl): Wegens een staking zijn wij vandaag gesloten. Uw afspraak gaat niet door. U
  krijgt bericht zodra u een nieuwe tijd kunt kiezen.
- Notice (en): We are closed today because of a strike. Your appointment cannot go ahead. We
  will let you know when you can choose a new time.

**Building closed**

- Heading: Building closed
- Notice (nl): Ons gebouw is onverwacht gesloten. Videogesprekken gaan niet door. Wij helpen u
  graag via de contactmogelijkheden hieronder.
- Notice (en): Our building is unexpectedly closed. Video calls cannot go ahead. We are glad to
  help you through the contact options below.

## What is recorded

Creating, changing and deleting a scenario, and its contact options and image, go to the audit
log with the scenario's number and counts. A closure started from a scenario names the
scenario's number. Names, notices and numbers are not in the log.

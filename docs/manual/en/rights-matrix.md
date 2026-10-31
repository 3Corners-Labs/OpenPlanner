# Rights matrix

This screen shows what each role in the admin portal may do, and where. Open it from
**Rights matrix** in the menu. Every admin can view it. What you may do yourself is under
**My rights**.

## Roles

Your organisation decides who has which role, and for which organisations or groups, in its
identity provider (in government usually PingFederate). The portal reads this when you log in.
So you cannot assign roles in the portal; ask your identity management team.

Besides the built-in roles below, the superuser can make [custom roles](roles.md), as a copy
of a built-in role and within its bounds. They appear as extra columns in the matrix.

| Role | Reach | What the role does |
|---|---|---|
| Superuser | everything | sets up the installation: organisations, integrations, holidays, updates |
| Organisation admin | own organisations and their groups | manages groups, planning, texts, reminders and bookings |
| Group admin | own groups | manages the planning, texts and bookings of the own team |
| Compliance and privacy (read-only) | own organisations and their groups | checks: reads the audit log, retention and the group settings, sees no personal data and cannot change anything |

The **Compliance and privacy** role is meant for, for example, the data protection officer,
the CISO or an auditor. In the audit log this role sees a booking's number, but not the
booking itself.

## Levels

Each permission applies at one level:

- **Portal**: something of the portal itself, such as the overview or this matrix.
- **Organisation**: something of one organisation, such as new groups or closing the whole
  organisation.
- **Group**: something of one group, such as its planning, its texts or its audit log.

## Markers

- **Superuser only**: this permission is fixed. It affects more than one organisation or
  concerns secrets, such as passwords of integrations. No other role can get it.
- **Personal data**: with this permission you see participants' data, such as name and email
  address. The Compliance and privacy role never gets it.

## Participants and integrations

Below the matrix is what participants can do without logging in: with the link in their
confirmation email they can view their booking, and move or cancel it until the change
deadline.
Below that are the rights of connected systems, such as a contact-center bot. The superuser
grants those per integration.

## Changes to roles

At the bottom of the page you see who made, changed or deleted which custom role, and when,
with the version number and which permissions went on or off. Every admin sees this history:
how roles are set up is not secret, and seeing who changed them is the point.

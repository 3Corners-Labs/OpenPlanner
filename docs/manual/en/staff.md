# Staff

This screen shows who uses the admin portal. Open it through **Staff** in the menu. For each
person you see their roles, the organisations and groups those roles apply to, and when they
last logged in, were active and logged out.

Who you see depends on your own role:

- **Organisation admin** and **Compliance and privacy**: staff of your own organisations and
  their groups.
- **Group admin**: staff of your own groups.
- **Superuser**: everyone.

Only the superuser sees superusers. If someone also works for another organisation, you do
not see that: you only see the organisations and groups you manage yourself.

## Where the data comes from

Roles, organisations and groups come from the identity provider (your organisation's login
service). OpenPlanner takes them over at every login. If the IdP team changes someone's role,
you see it here only after that person's next login. Grant or revoke rights at the identity
provider, not here.

Every change OpenPlanner notices at a login is in the audit log as **Roles or memberships
changed**, with what was added and what was removed.

## Rights not recognised

When the identity provider sends values at a login that OpenPlanner does not recognise (a typo
in the role, an unknown organisation or group), they are on the person's page under **Rights
not recognised**, with the reason and the moment. The filter **Only with unrecognised rights**
finds these people. The right values are on **Identity provider mapping**; pass the difference
on to the IdP team.

## Filters

- **Organisation**, **Group** and **Role**: only staff who belong to it.
- **Only people without a login**: staff without a login in the configured period (90 days by
  default). Use it for a periodic access review: does this person still need access?

## A person's page

Click a name. You see their access, the times and their recent activity from the audit log:
logins, logouts and changes, within your own organisations or groups.

## Times

- **Last login**: the last time the person logged in through the identity provider.
- **Last active**: the last time the person opened a page. Updated at most once every five
  minutes.
- **Last logout**: the last time the person clicked **Log out**. A session that expires on
  its own (after 8 hours) does not count as a logout. The same goes for a session that ends because it was idle
  for too long (60 minutes by default) or because a newer login replaced it.

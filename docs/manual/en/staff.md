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

## Managing staff in the bundled identity provider

If this platform uses the bundled Keycloak (test and lab installations) you manage staff here.
With an identity provider of your own, such as PingFederate, you do not see these buttons: the
IdP team changes rights there.

- **Invite staff member** (button above the list): enter email address, first and last name
  and choose the first role and place. The staff member receives an email with a link to choose
  a password. The link is valid for 7 days by default (the superuser can change this).
- **Invited, not logged in yet**: people you created who have not logged in. You can resend the
  invitation.
- On a person's page, under **Managed at the identity provider**: remove a role, add a role
  with a place, disable or enable the person, and send a password reset. A change takes effect at
  the next login.

What you may do:

- An **organisation admin** works only inside their own organisations and never grants more
  than their own rights: only the superuser can grant the superuser role.
- For a person who also works outside your organisations, roles elsewhere are left alone and
  only the superuser can disable them.
- You cannot change or disable yourself.

Every change is in the audit log, with role names and places and without names or email
addresses. When the identity provider cannot be reached the screen says so, without repeating
the provider's own message.

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

## Second factor (MFA)

Staff log in with a second factor besides their password: a code from an authenticator app
or a security key. OpenPlanner does not set this up and does not store it; the identity
provider does (ADR 0100). At login OpenPlanner asks the provider for it and checks that the
provider says it was used. If it was not, the login is refused with the message *your
account shows no second factor*. Set up multi-factor authentication at your identity provider
(with the bundled Keycloak: the first login asks for an authenticator app) and sign in again.
Still refused? Ask the IdP team; the refusal is in the audit log as `mfa_required`, without
any personal data.

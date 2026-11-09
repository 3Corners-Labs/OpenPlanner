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

The email address on a person's page is the address the identity provider gave at their last
login, kept only when the provider says it is verified. The usage digest (*Reports*) is sent
to it. It is replaced or cleared at every login and deleted when the person is erased.

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
- **User name**: the staff member logs in as `firstname.lastname@organisation.openplanner.internal`
  (a superuser as `name@openplanner.internal`). You may choose the part before the @ yourself:
  lower-case letters, digits, dots and hyphens. When the proposed name is taken it gets `-2`,
  `-3` and so on. The user name cannot be changed afterwards; the email address remains the
  address for the invitation, the password reset and calendar invitations, and also works to
  log in. The superuser sets the suffix (*User name suffix*); a change applies to new accounts
  only.
- **Invited, not logged in yet**: people you created who have not logged in. You can resend the
  invitation.
  The list and a person's page show the **user name** next to the **e-mail address**: either
  logs in, and mail goes to the address. Give new staff their user name when their address
  belongs to an identity provider of the organisation's own.
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

A yellow notice that the bundled identity provider is **not fully connected** means Keycloak
runs but the server lacks a setting; the notice names which. The server administrator fixes it
(runbook, *Bundled identity provider*); until then the buttons are missing. The superuser also
sees the state on **System status**, under *Staff management*.

## Filters

- **Organisation**, **Group** and **Role**: only staff who belong to it.
- **Login service**: only staff who log in through this identity provider (*Platform*, or the
  key of an organisation's own provider). The column of the same name shows it per person; the
  filter appears once there is more than one login service.
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

## End a staff member's sessions

A staff member's page has the button **End all sessions**. Organisation admins and the
superuser see it (permission `staff.manage`). It works for every staff member you see in
the overview; for anyone outside it, it is refused and the attempt is in the audit log.

A session belongs to the person, not to an organisation. If you end all sessions of someone who
works for two organisations, what they use for the other organisation ends too. That is
intended: ending protects and gives nobody more rights. The staff member has to log in again.
Use it, for example, when someone has been switched off at the identity provider: otherwise
their session keeps working until it goes idle (60 minutes by default). The audit log has an
event with you, the staff member and the number of sessions. Everyone can see and end their own
sessions on [My sessions](my-sessions.md).

## Suspend, reinstate and erase a staff member

Under **Access** on a staff member's page you can **suspend** the person (permission
`staff.manage`, organisation admins and the superuser). Suspending blocks the admin portal for
that person whatever the identity provider still says about their rights, and ends all their
sessions at once. A login is refused with the message *your access is suspended*, and the
attempt is in the audit log. **Lift suspension** lets the person log in again; their rights
then come from the identity provider afresh. You cannot suspend yourself, and only the
superuser can suspend a superuser. Every step is an audit event (`staff.suspended`,
`staff.reinstated`) with the administrator and the person, without a name.

The superuser can also **erase** a suspended person's data (permission `staff.erase`): the
name, email address, roles and memberships go and the page then says *Erased staff member*. The audit log keeps
pointing to that erased person. This cannot be undone. A person who logs in again afterwards
starts a fresh record.

Erasing also happens by itself. A person who has not logged in for 365 days (the superuser
sets this in *Advanced settings*, 90 to 1825 days: *Erase staff data after*) is erased by the
worker, once a day, as a system event `staff.erased`. Someone who comes back before that
moment is kept.

## Second factor (MFA)

Staff log in with a second factor besides their password: a code from an authenticator app
or a security key. OpenPlanner does not set this up and does not store it; the identity
provider does (ADR 0100). At login OpenPlanner asks the provider for it and checks that the
provider says it was used. If it was not, the login is refused with the message *your
account shows no second factor*. Set up multi-factor authentication at your identity provider
(with the bundled Keycloak: the first login asks for an authenticator app) and sign in again.
Still refused? Ask the IdP team; the refusal is in the audit log as `mfa_required`, without
any personal data.

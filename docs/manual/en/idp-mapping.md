# Identity provider mapping

This screen shows which values the identity provider (your organisation's login service)
must send to give someone a role in OpenPlanner. Open it through **Identity provider
mapping** in the menu. The screen is read-only: the IdP team grants and revokes rights, in the
identity provider. Here you see what it must send and what came in.

## One value per role per organisation or group

Each value has the form `prefix:role:organisation` or `prefix:role:organisation/group`, for
example `openplanner:org_admin:gem-a` or `openplanner:group_admin:gem-a/burgerzaken`. The
superuser has one value without a target: `openplanner:superuser`. A role applies only to the
target in the value: someone who is organisation admin in organisation A and read-only in
organisation B never gets A's rights in B.

The screen shows, per organisation and per group, the exact value per role, how many staff
members held that value at their last login and when the last of them logged in. A value
nobody holds any more stands out.

## Source of the rights

The top says where the rights come from:

- **Three claims**: the older mapping with separate claims for roles, organisations and
  groups.
- **Shadow mode**: the three claims still apply; the entitlement claim is compared at every
  login and a difference goes to the audit log. This lets the IdP team prove the new mapping
  before the switch.
- **Entitlement claim**: only the values on this screen apply.

The installation's administrator makes that choice on the host; the superuser sets the prefix
and the claim name in the login settings.

## IdP value of an organisation or group

The identity provider names an organisation or group by a fixed **IdP value**, not by its name
or web address. A new organisation or group starts with the code of its web address as IdP
value. The superuser changes an organisation's value on that organisation's page; an
organisation admin changes a group's value on the **Settings** tab of that group. The form
warns how many staff members lose access until the identity provider sends the new value.

## Unrecognised values

The bottom lists the values with the prefix that came in at a login but gave no right, with
the reason: a typo in the role, an unknown organisation or group, a group that does not belong
to the named organisation, or a target that does not fit the role's reach. Values naming an
unknown organisation are shown to the superuser only. Pass such a value on to the IdP team;
the staff member's page (**Staff**) shows the same.

## Download

**Download as text** gives the same list as a text file, one value per line, with a comment
line per organisation and group, for the IdP team. Every download is in the audit log.

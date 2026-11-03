# My rights

This screen shows what you may do in the admin portal and where. Open it through **My rights** in
the menu. Every admin who can log in can open it; no permission beyond logging in is needed. It
only shows your own rights. To see what every role may do, open the
[Rights matrix](rights-matrix.md).

The page has two parts, chosen at the top (*Parts of My rights*): **My rights** (this screen) and
**My sessions**, where you see where you are logged in and can end other sessions (see
[My sessions](my-sessions.md)).

## Your roles

The card **Roles** lists your roles as the portal knows them. They come from your organisation's
identity provider, read when you log in, so you cannot change them here; ask your identity
management team. A built-in role may have a name chosen by your organisation. The shipped name
then follows in brackets, so you can always tell which role is meant. A custom role shows its own
name (see [Custom roles](roles.md)). If you have no roles, the page says so.

Under **Rights per organisation or group** you see where each role applies, as short codes: the
role, then the organisation, and for a group role the organisation and the group, for example
`org_admin:demo`. The superuser has no organisation.

## Your permissions

The table at the bottom lists what you may do, in two columns:

- **Permission**: for example *Manage staffing and staffing exceptions*.
- **Applies to**: where. *The admin portal* for rights that are not about one organisation or
  group, *All organisations and groups*, or the names of the organisations and the groups where
  you have the permission.

The table is computed from the same matrix as the menu and the buttons, so a button you do not
see belongs to a permission that is not in this list. Some permissions need another one: the
rights to manage the staffing of a group, for instance, only work together with the right to
view that group.

## Rights not recognised

If your identity provider sent values at your last login that this installation does not
recognise, the card **Rights not recognised** appears. It lists the value, the reason and when it
was received. These values give you no rights. Pass them on to the identity provider team; your
page under [Staff](staff.md) shows them as well. The card is not there when nothing was refused.

## What this screen does not do

You cannot change anything here. Roles are assigned in the identity provider, not in the portal.
The superuser can make custom roles within the bounds of a built-in role; see [Custom roles](roles.md).

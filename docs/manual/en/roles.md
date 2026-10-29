# Custom roles

On this screen the superuser makes a role to fit a team, for example a team lead who may
change the opening hours but not the texts. Open it with **Adjust roles** on the
[rights matrix](rights-matrix.md). Only the superuser sees this screen. Every admin sees in
the rights matrix which custom roles exist and who changed them.

## Making a role

A custom role always starts as a copy of a built-in role. Under **New role**, choose the
built-in role closest to what you need and choose **Make a new role**. You get a form with
that role's permissions already ticked. Then switch permissions on or off.

| Copy of | Reach | What can be added |
|---|---|---|
| Organisation admin | the person's organisations and their groups | anything without a lock |
| Group admin | the person's groups | anything at group level without a lock, such as group settings |
| Compliance and privacy | the person's organisations and their groups | reading only, never personal data |

The reach is fixed once the role is made. A custom role can never do everything, like the
superuser.

<!-- field: name_nl -->
### Name

Give the role a name in Dutch and in English, as admins see it in the matrix, under
**My rights** and at the top of every page. At most 80 characters.

<!-- field: claim_value -->
### Value in the identity provider

Your organisation decides who holds a role in its identity provider (in government usually
PingFederate). A custom role therefore gets a value of its own, for example
`openplanner-team-lead`. Ask the team that runs the identity provider to assign that value to
the right people. No spaces or commas, at most 128 characters.

The value may not equal that of a built-in role (such as the superuser) or of another custom
role. As long as the identity provider does not send the value, nobody holds the role. A new
or changed value works after the next login.

<!-- field: permissions -->
### Permissions

Only the permissions this role may get are in the list. Below it is what cannot be chosen,
with the reason:

- **Superuser only**: this permission is fixed, for example integrations, organisations and
  adjusting roles itself.
- **Outside the reach**: a permission of a whole organisation, such as creating groups,
  cannot be in a role that works per group.
- **Changes something** (only for a copy of Compliance and privacy): that role only reads.

Some permissions need another one; it says so. Whoever may change the planning, for example,
must also be able to view the group. The overview, **My rights** and the rights matrix belong
to every admin. On **Save** the portal says what is missing.

A permission marked **Personal data** shows citizens' data. A copy of Compliance and privacy
can never get it.

## Changing and deleting

A change of permissions works from the next page someone opens; nobody has to log in again.
Every change gets a new version number. If someone else changed the role in the meantime, the
portal says so and shows you the new version first.

If you delete a role, whoever held it keeps no rights from it. Their other roles keep
working. Afterwards, ask the identity provider team to remove the value.

## Record

Making, changing and deleting are recorded with the time, the version, who did it and which
permissions went on or off. That history is at the bottom of the rights matrix, for every
admin, and in the superuser's audit log.

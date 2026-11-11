# Skills and capacity

This screen holds two lists of an organisation: the **skills** and the **capacity pools**. A
skill says which expertise is needed to handle a request, such as intake or legal advice. A
capacity pool is the time that flows draw on, together with the skills that every counted member
of staff of the pool holds. You open the screen with **Organisations → organisation → Overview →
Skills and capacity**.

Everyone who may open the organisation can read the two lists. The form to add something and the
links to change and archive are there only for those who hold that right: managing skills is
reserved for organisation admins and the superuser (each for their own organisations), and so is
managing capacity pools. Before you change anything, the portal may ask you to log in again. The
old address of the capacity pools sends you on to this screen.

## Skills

A skill grants no rights and no access to data: rights decide what someone may see, skills decide
which requests someone may pick up.

The list shows each skill with its name in your language (the other language underneath), its
code, how many members of staff hold it and where it is used: in how many capacity pools and
flows. Skills you archived are shown with the tick **Archived skills only**. An organisation can
have a limited number of skills that are not archived; the superuser sets that maximum (Advanced
settings, Staff).

### Adding a skill

<!-- field: skill_code -->
#### Code

The code is how the skill is recognised in reports and in configuration: lower-case letters,
digits and hyphens, at most 40 characters, for example `intake` or `legal-advice`. It must be
unused in your organisation. You cannot change the code after you add the skill; the page then
shows it as fixed.

<!-- field: label_nl -->
#### Name in Dutch

The name members of staff see when their portal is in Dutch. At most 80 characters.

<!-- field: label_en -->
#### Name in English

The name members of staff see when their portal is in English. At most 80 characters. Both names
are required, and you can change them at any time.

### Archiving a skill

**Archive** opens a page that says what happens, and asks you to confirm. After archiving, no
new requirement and no new grant can use the skill. Requests, requirements and grants that
already have it keep it, and work that was already accepted does not change. An archived skill
does not count towards the maximum and can no longer be changed. There is no delete.

### Giving skills to members of staff

That is done on the page of the member of staff: **Staff → person → Skills**. A change applies
from the next request that is picked up or started. Every change on this screen and on the
person's page goes to the [audit log](audit.md) with the skill codes, never with a name.

## Capacity pools

For each pool the list shows the skills every counted member of staff holds, and the flows that
draw on it. If flows of other services also draw on a pool and you may not see those services,
the list says "and N flows of other services" and nothing more.

### Kinds of pools

- **Existing**: the staffing of a service itself, unchanged. These pools were made for the
  services that already existed. They are only shown here, and only to those who may see that
  service; you cannot change or archive them.
- **Own pool**: a pool you made that one flow draws on.
- **Shared pool**: a pool you made that more than one flow draws on.

Pools you archived are shown with the tick **Archived capacity pools only**. An organisation can
have a limited number of own and shared pools that are not archived; existing pools do not
count. The superuser sets that maximum.

### Mind the counting

A pool promises that every counted member of staff has all skills of the pool. Do not count the
same person in two pools at the same time: the portal cannot check that for you yet. How many
members of staff work in a pool per time block is not set here yet; that comes once flows use the
pool.

### Adding and changing a capacity pool

<!-- field: pool_name -->
#### Name of the pool

The name by which you recognise the pool, at most 80 characters.

Below the name you tick the skills that every counted member of staff holds. Only skills that
are not archived can be ticked; an archived skill the pool already guarantees stays shown, marked
archived, so you can untick it. A flow that draws on the pool may only require skills that are
ticked here.

### Archiving a capacity pool

**Archive** opens a page that says what happens, and asks you to confirm. A pool that a flow
still draws on, in a draft or a published revision, cannot be archived: the page then names the
flows. Take the pool out of those flows first. An archived pool can no longer be changed. Every
change goes to the [audit log](audit.md).

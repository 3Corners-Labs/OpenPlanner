# Skills

A skill says which expertise is needed to handle a request, such as intake or legal advice.
A skill grants no rights and no access to data: rights decide what someone may see, skills
decide which requests someone may pick up. You open the catalogue with **Organisations →
organisation → Overview → Skills**. Organisation admins and the superuser can use it, each for
their own organisations. Before you change anything, the portal may ask you to log in again.

The list shows each skill with its name in your language (the other language underneath), its
code, how many members of staff hold it and where it is used: in how many capacity pools and
flows. Skills you archived are behind **Show archived skills**.

## Adding a skill

<!-- field: skill_code -->
### Code

The code is how the skill is recognised in reports and in configuration: lower-case letters,
digits and hyphens, at most 40 characters, for example `intake` or `legal-advice`. It must be
unused in your organisation. You cannot change the code after you add the skill; the page then
shows it as fixed.

<!-- field: label_nl -->
### Name in Dutch

The name members of staff see when their portal is in Dutch. At most 80 characters.

<!-- field: label_en -->
### Name in English

The name members of staff see when their portal is in English. At most 80 characters. Both names
are required, and you can change them at any time.

## Archiving a skill

**Archive** opens a page that says what happens, and asks you to confirm. After archiving, no
new requirement and no new grant can use the skill. Requests, requirements and grants that
already have it keep it, and work that was already accepted does not change. There is no delete:
you can always find an archived skill under **Show archived skills**.

## Giving skills to members of staff

That is done on the page of the member of staff: **Staff → person → Skills**. A change applies
from the next request that is picked up or started. Every change on this screen and on the
person's page goes to the [audit log](audit.md) with the skill codes, never with a name.

# Capacity

A capacity pool is the time that flows draw on, together with the skills that every counted
member of staff of the pool holds. You open the pools with **Organisations → organisation →
Overview → Capacity**. Organisation admins and the superuser can use it, each for their own
organisations. Before you change anything, the portal may ask you to log in again.

## Kinds of pools

- **Existing**: the staffing of a service itself, unchanged. These pools were made for the
  services that already existed. They are only shown here; you cannot change or archive them.
- **Own**: a pool you made that one flow draws on.
- **Shared**: a pool you made that more than one flow draws on.

For each pool the list shows the skills every counted member of staff holds, and the flows that
draw on it. If flows of other services also draw on a pool and you may not see those services,
the list says "and N flows of other services" and nothing more.

## Counting

A pool promises that every counted member of staff has all skills of the pool. Do not count the
same person in two pools at the same time: the portal cannot check that for you yet. How many
members of staff work in a pool per time block is not set here yet; that comes once flows use the
pool.

## Adding and changing a pool

<!-- field: pool_name -->
### Name of the pool

The name by which you recognise the pool, at most 80 characters.

Below the name you tick the skills that every counted member of staff holds. Only skills that
are not archived can be ticked; an archived skill the pool already guarantees stays shown, marked
archived, so you can untick it. A flow that draws on the pool may only require skills that are
ticked here.

## Archiving a pool

**Archive** opens a page that says what happens, and asks you to confirm. A pool that a flow
still draws on, in a draft or a published revision, cannot be archived: the page then names the
flows. Take the pool out of those flows first. Pools you archived are behind **Show archived
pools**. Every change goes to the [audit log](audit.md).

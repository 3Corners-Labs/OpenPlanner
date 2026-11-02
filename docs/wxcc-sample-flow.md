# Sample Webex Contact Center flow

OpenPlanner sends every booking to Webex Contact Center (WxCC) as a **work item** (ADR 0018,
0082). A flow in Flow Designer decides what happens to it: which queue, which priority, and
what the agent sees. This page describes a sample flow that does all of that, how to generate
it for your groups and how to import it. The runbook (section 3, "WxCC work items") lists the
Control Hub setup around it: channel, asset, schema, entry point and queues.

## What the flow does

```mermaid
flowchart LR
    start([NewContact]) --> kind{Case<br/>kind}
    kind -- probe --> endProbe([End Flow])
    kind -- direct --> prio[Set Contact Priority<br/>P1]
    kind -- default / error --> pop
    prio -- success / failure --> pop[Screen Pop<br/>meetingLink]
    pop --> group{Case<br/>openplannerGroup}
    group -- "routing value 1" --> q1[Queue Contact<br/>queue 1]
    group -- "routing value 2" --> q2[Queue Contact<br/>queue 2]
    group -- default / error --> qd[Queue Contact<br/>fallback queue]
    q1 --> done([End Flow])
    q2 --> done
    qd --> done
```

| Step | Activity | Why |
|---|---|---|
| Case on `kind` | `case-statement` on the work item's `kind` | `probe` is the group check's probe work item (ADR 0082): it ends before any queue, so no agent sees it. `direct` is a direct call (ADR 0072). Everything else (`scheduled`) goes on. |
| Set Contact Priority | `set-contact-priority`, static P1 | a participant who calls now is waiting; a scheduled appointment is not yet. Optional: delete the activity if every work item should have the same priority. |
| Screen Pop | `screen-pop` of `meetingLink`, opened outside the desktop | the agent opens the Webex meeting in one click (in the Webex app, signed in: cohost). |
| Case on `openplannerGroup` | `case-statement` on the flow variable `openplannerGroup` | OpenPlanner fills this variable with the group's routing value (`flowSettings`); one branch per group. |
| Queue Contact | `queue-contact` on a Work Item queue per branch | the default branch catches a routing value without a branch, so no work item is lost. |

The flow declares one variable, `openplannerGroup` (String). It reads `kind` and `meetingLink`
from the work item data, which Flow Designer offers on the start node as
`NewContact.MediaPayload.workItemData` (Cisco's Work Item guide, "Flow Support"). The flow
holds routing values and queue names only, never participant data.

The flow passes OpenPlanner's own flow check (**Check integration** and `wxcc-config-check`)
without warnings: routing variable, a branch per group, queues of type Work Item named after
the routing value, the probe branch and the Screen Pop (`tests/adapters/test_wxcc_sample_flow.py`).

## Generating it for your groups

On the host, in the `web` container:

```sh
docker compose exec web python -m app.cli wxcc-sample-flow --group aangifte \
  --queue aangifte=ORG_videoafspraak_aangifte_01 --fallback-queue ORG_videoafspraak_overig \
  > openplanner_aangifte.json
```

- `--group` names one group (its web address). The flow gets a branch for its routing value
  and for every other group with the same **WxCC business address**: one asset, one entry
  point and one flow for all of them, the layout the runbook recommends.
- `--queue VALUE=QUEUE` (repeatable) sets the queue of a routing value: the queue's name as
  it appears in Control Hub. Without it the queue is named after the routing value itself,
  which follows the naming rule of `wxcc-config-check` but must then exist under that name.
- `--fallback-queue` sets the queue of the default branch; without it, the first branch's
  queue.

The command reads the groups from the database only; it calls nothing at Webex. The flow is
named `openplanner_<group>`; rename it on import if that name is taken.

## Importing it

1. Control Hub → **Contact Center** → **Customer experience** → **Flows** → **Import**, and
   choose the generated file.
2. Open the flow. Check that each **Queue Contact** shows the queue you meant (Flow Designer
   looks the queues up by name) and that the Case branches show your routing values.
3. In the flow's **Global Flow Properties**, check that `openplannerGroup` may be set from
   outside (flow settings), then **Validate** and **Publish**.
4. **Channels** → your Work Item entry point → set this flow and its version label (Live).
5. In OpenPlanner, press **Check integration** on the group's settings: the flow lines and the
   probe line should all be OK.

## What is verified, and what still needs a live check

Checked on 2026-11-02 against the lab organisation (eu2), without saving anything:

- The format is Flow Designer's FlowV2 (`schemaVersion` 2.0), the same format the
  configuration API exports (`v2/flows/{id}:export`) and OpenPlanner's flow check reads.
- Flow Designer's dry-run validator (`v2/flows:validate`) accepts every activity and edge
  except the Case branches: it only knows a Case's fixed `error` port. The branches follow
  the form Cisco's own exported flows store (`menuLinks` numbered `0`, `1`, … with the values
  next to them, edges on `0`, `1`, … and `default`).

Still to confirm on the first import:

- that **Import** in the Flows screen takes the FlowV2 file (the API import
  `v2/flows:import` does; if the screen refuses it, report it so the sample can follow);
- that `NewContact.MediaPayload.workItemData.kind` resolves. **Check integration** shows it:
  when the probe line is OK, the flow ended the probe and the `kind` branch works. When it
  says the probe was not ended, the expression did not resolve and the probe went on to a
  queue (an agent may see it): adjust the path in both Case `kind` and the Screen Pop, which
  reads `meetingLink` the same way.

## Changing it

Edit the flow in Flow Designer as you like; the check only needs the variable
`openplannerGroup`, a Case branch per routing value, Work Item queues, a branch on `probe` and a
Screen Pop. To add a group later, add a branch and a queue, or generate the flow again and
import it under a new name.

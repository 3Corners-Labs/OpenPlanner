# Queue

In a group with **OpenPlanner's own queue**, agents pick up video appointments on this screen
instead of in the contact center. You open it from the group's **Queue** tab. Agents, group
admins and organisation admins of the group can see it. A group that works with the contact
center has no such tab.

## What you see

Today's appointments that start soon (see **Visible in the queue** in the group settings) or
are still going on. For each appointment: the time, the participant's first name, the
appointment reference and the status:

- **Not started yet**: the participant has not joined the call yet.
- **Participant waiting**: the participant is in the call and waits for an agent.
- **Picked up by a colleague** or **Picked up by you**.
- **Not picked up**: nobody picked the appointment up within the time set for the group.

Direct calls are in the same list, with **Direct call** below the time. You never see the
participant's email address or phone number.

The status also says how the video link is doing: **Video link being prepared** while the
call is still being created, and **Video link failed** when that did not work. Nothing there
means the link is ready.

The list refreshes itself every few seconds (the administrator sets the interval). You do not
need to reload; **Refresh** does it by hand as well. A new or waiting participant is read out
by the screen reader.

## Picking up an appointment

Choose **Pick up next**. You get the participant who has waited longest; if nobody is waiting,
the appointment that starts first. That way every participant gets the first free agent.

You then see the button **Join the call**. Only you see that link, so two colleagues never start
the same call at once. If a colleague was just ahead of you, you see "Already picked up by a
colleague" and can pick up the next one.

The page of the picked-up appointment shows where the participant is (not in the call yet,
waiting in the lobby, call in progress) and refreshes itself. The participant, in turn, sees
at once that an agent has accepted the call.

If the video link failed, this page says so, with a link to the appointment's timeline for
whoever may see it. The participant sees the same message and the advice to book an
appointment. Release the appointment.

If **Agents may choose an appointment** is on for the group, every free appointment also has a
**Pick up** button.

## Releasing

If the call does not work, for example because of a technical problem, choose **Release**. The
appointment goes back into the queue. A group admin can also release a colleague's appointment,
for example when that colleague had to leave.

Picking up and releasing are in the audit log, with your name and the appointment reference.

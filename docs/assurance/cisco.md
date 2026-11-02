# Assurance checklist: Cisco (Webex and Webex Contact Center)

For the client's integration administrator, who holds the contract with Cisco and asks Cisco for
the documents. OpenPlanner starts Webex meetings and creates Webex Contact Center (WxCC) tasks,
but the media and signalling servers, the TURN service and the DTLS and SRTP stack are Cisco's.
The seven ASVS 5.0.0 requirements of chapter V17 therefore cannot be proven from this
repository. They are answered by Cisco documents, collected through the client. How to use this
file: `docs/assurance/README.md`. Requirement texts are the OWASP ASVS 5.0.0 (CC BY-SA 4.0); this
file gives the ID and a short description in our own words.

The retention, regions, sub-processors and deletion questions about Cisco as a recipient of
personal data are in the processor template of `docs/assurance/dpia-input.md` (one block per
recipient); they are not repeated here, so each requirement has one home.

## What Cisco receives from OpenPlanner

Taken from `docs/dataflow.md` (*Video meetings and WxCC work items*, *Outbound connections and
the hop to web*); give Cisco's data protection contact this list, and keep it current when the
dataflow changes.

| Flow | Cisco service | OpenPlanner sends | Source |
|---|---|---|---|
| Meeting created for a group | Webex Meetings API | Title with the group name, start, end, time zone, the host account of the group | `docs/dataflow.md` |
| Task created for a call | WxCC Tasks API | Booking reference, first name, start time as text, group name, meeting link, kind; routing variables (ADR 0081) | `docs/dataflow.md`; ADR 0007, ADR 0081 |
| Task cancelled or ended | WxCC Tasks API | Booking reference and kind, or the task id only | `docs/dataflow.md` |
| Trial meeting | Webex Meetings API | Group name and times; deleted at Webex minutes after it closes (ADR 0071) | `docs/dataflow.md` |
| Status reads | Webex Meetings API | Meeting id and the host address; the answer's participant list is reduced to counts and times in memory (ADR 0010) | `docs/dataflow.md` |
| SMS reminder, where the organisation uses Webex Connect as its SMS provider (ADR 0028) | Webex Connect SMS API | Phone number, and in the text the first name, date, time, team and the management link | `docs/dataflow.md` *SMS reminders* |
| Webhooks back | Webex, WxCC to OpenPlanner | Signed events; verified, not stored beyond hashed keys | `docs/webhooks.md` |

Last names and email addresses of participants are not sent to Cisco by these flows (per the
dataflow document); the phone number goes to Cisco only through the SMS provider row. Participants
join the Webex meeting themselves, in their browser or app, and Cisco then processes what they
enter there; that is Cisco's own processing under the client's contract.

## Documents to request from Cisco, per requirement

| ID | Asks for | How to show it | Owner | Reference |
|---|---|---|---|---|
| V17.1.1 | The TURN relay refuses to forward to special-purpose addresses (internal networks, loopback, broadcast), for IPv4 and IPv6 | Cisco statement or the Webex security white paper section on media relay; a pentest summary if Cisco shares one | Client integration administrator, with Cisco | Not an OpenPlanner component; no Keycloak counterpart |
| V17.2.1 | The key of the DTLS certificate is managed and protected under a documented key-management policy | Cisco's key-management statement (for example in the ISO 27001 or SOC 2 report scope) | Client integration administrator, with Cisco | As V17.1.1 |
| V17.2.2 | The media server supports only approved DTLS cipher suites and a secure SRTP protection profile | Webex security white paper (media encryption chapter), or a Cisco statement of the suites | Client integration administrator, with Cisco | As V17.1.1 |
| V17.2.3 | SRTP authentication is checked at the media server, so injected RTP cannot disturb or alter audio and video | Cisco statement or white paper | Client integration administrator, with Cisco | As V17.1.1 |
| V17.2.4 | The media server keeps working when it receives malformed SRTP packets | Cisco statement, SOC 2 or penetration-test summary | Client integration administrator, with Cisco | As V17.1.1 |
| V17.3.1 | The signalling server keeps serving legitimate messages during a flood, with rate limiting at the signalling level | Cisco statement or availability section of the white paper | Client integration administrator, with Cisco | As V17.1.1 |
| V17.3.2 | The signalling server keeps serving legitimate messages when it receives malformed ones | Cisco statement, SOC 2 or penetration-test summary | Client integration administrator, with Cisco | As V17.1.1 |

Suggested documents for the request letter: the Webex security white paper, the Webex Meetings
privacy data sheet, the WxCC data sheet, Cisco's ISO 27001 certificate and latest SOC 2 report,
and the statement on EU data residency for the client's Webex and WxCC organisations. One
document may answer several rows; write the page or section in the evidence reference.

## Settings the client confirms in Control Hub

OpenPlanner does not set these on the Cisco side, and the repository holds no proof of their
current values. The integration administrator confirms each with a screenshot and the date.

| Setting | What to confirm | Why it matters |
|---|---|---|
| Recording | Off for the Webex site, or allowed only where the client decided | Recordings would hold the call content beyond the retention OpenPlanner promises |
| Transcription and AI summaries | Off, or decided and documented | Same |
| Region and data residency | The Webex and WxCC organisations are in the region the DPIA assumes | The processor template asks for it |
| Site policy for guests, lobby and entry before the host | As in the runbook, *Webex site settings (Control Hub)* | The group check (*Koppeling controleren*) reports when Webex refuses |
| Service App scopes | Exactly the scopes in the runbook, *Webex Service App*, no more | Least privilege on the OpenPlanner side of the contract |
| WxCC routing configuration | Entry point, queue and schema keys as `wxcc-config-check` reports them | See the runbook, *Checking the WxCC routing configuration* |

## Sign-off

| Owner | Name | Date | Evidence reference |
|---|---|---|---|
| Client integration administrator | | | |
| Cisco contact (document supplier) | | | |

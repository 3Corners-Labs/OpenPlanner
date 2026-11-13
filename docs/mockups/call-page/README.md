# Mock-ups of the call page (ADR 0123 step 3)

The design of the participant's call in our own page, as the product owner approved it on
2026-11-13 (ADR 0123 point 15: the picture comes first, designed before it is built). Sixteen
states, each in two organisations' themes: Dutch for the organisation *Acme*, English for the
organisation *Demo*. The phone screen exists once.

| # | State | Dutch, theme Acme | English, theme Demo |
|---|---|---|---|
| 1 | Start: the appointment and one button to begin | [`01-intro.jpg`](01-intro.jpg) | [`01-intro-en-demo-theme.jpg`](01-intro-en-demo-theme.jpg) |
| 2 | Device check: own picture, level meter, test sound, device choice, and the line that an external video service carries the call | [`02-device-check.jpg`](02-device-check.jpg) | [`02-device-check-en-demo-theme.jpg`](02-device-check-en-demo-theme.jpg) |
| 3 | The browser refuses the camera: what to click, joining without it, the chat still there | [`13-camera-refused.jpg`](13-camera-refused.jpg) | [`13-camera-refused-en-demo-theme.jpg`](13-camera-refused-en-demo-theme.jpg) |
| 4 | Connecting | [`09-connecting.jpg`](09-connecting.jpg) | [`09-connecting-en-demo-theme.jpg`](09-connecting-en-demo-theme.jpg) |
| 5 | Connecting, a new attempt by itself after a failed one | [`10-connecting-retry.jpg`](10-connecting-retry.jpg) | [`10-connecting-retry-en-demo-theme.jpg`](10-connecting-retry-en-demo-theme.jpg) |
| 6 | Waiting for the staff member, with the chat | [`03-lobby-with-chat.jpg`](03-lobby-with-chat.jpg) | [`03-lobby-with-chat-en-demo-theme.jpg`](03-lobby-with-chat-en-demo-theme.jpg) |
| 7 | In the call, with captions, the recording notice and the chat | [`04-in-call-captions-chat.jpg`](04-in-call-captions-chat.jpg) | [`04-in-call-captions-chat-en-demo-theme.jpg`](04-in-call-captions-chat-en-demo-theme.jpg) |
| 8 | In the call, without captions | [`05-in-call-nl-acme-theme.jpg`](05-in-call-nl-acme-theme.jpg) | [`05-in-call-en-demo-theme.jpg`](05-in-call-en-demo-theme.jpg) |
| 9 | In the call, for a service without chat | [`08-in-call-no-chat.jpg`](08-in-call-no-chat.jpg) | [`08-in-call-no-chat-en-demo-theme.jpg`](08-in-call-no-chat-en-demo-theme.jpg) |
| 10 | The participant shares a screen: a lasting notice with a stop button | [`11-sharing-by-participant.jpg`](11-sharing-by-participant.jpg) | [`11-sharing-by-participant-en-demo-theme.jpg`](11-sharing-by-participant-en-demo-theme.jpg) |
| 11 | The staff member shares a screen: the screen large, the camera small | [`12-sharing-by-staff.jpg`](12-sharing-by-staff.jpg) | [`12-sharing-by-staff-en-demo-theme.jpg`](12-sharing-by-staff-en-demo-theme.jpg) |
| 12 | A chat message that did not arrive, with a way to try again | [`16-chat-message-not-sent.jpg`](16-chat-message-not-sent.jpg) | [`16-chat-message-not-sent-en-demo-theme.jpg`](16-chat-message-not-sent-en-demo-theme.jpg) |
| 13 | The chat closed while the call goes on | [`15-chat-closed-call-goes-on.jpg`](15-chat-closed-call-goes-on.jpg) | [`15-chat-closed-call-goes-on-en-demo-theme.jpg`](15-chat-closed-call-goes-on-en-demo-theme.jpg) |
| 14 | The picture cannot be set up; the chat keeps working | [`06-video-failed-chat-still-works.jpg`](06-video-failed-chat-still-works.jpg) | [`06-video-failed-chat-still-works-en-demo-theme.jpg`](06-video-failed-chat-still-works-en-demo-theme.jpg) |
| 15 | The call has ended; the chat is closed and can still be read | [`14-call-ended.jpg`](14-call-ended.jpg) | [`14-call-ended-en-demo-theme.jpg`](14-call-ended-en-demo-theme.jpg) |
| 16 | In the call on a phone, dark appearance | [`07-phone-dark-in-call.jpg`](07-phone-dark-in-call.jpg) | |

## What the approval covers

The layout of every state above, the control bar, the place of the chat beside the picture, the
two fixed lines (an external video service carries the call, next to the join button; no
identity numbers in the chat, above the first message), and the staff member's name as a first
name with the team's own word for staff, never a surname.

Three changes were named with the approval, and the build makes them:

1. **The ended state.** The sentence about deletion says what the retention rule says: the
   personal data of the call, the chat included, is deleted 24 hours after the end of the
   appointment. The thank-you line is reworded for a call rather than a visit.
2. **The control bar stays in view.** On a low window the second row of controls fell below the
   edge when captions and the recording notice were both shown; the bar is always visible.
3. **A closed chat has no input field.** The dashed, disabled field of these pictures makes way
   for the line that says the chat is closed (ADR 0123 point 8).

## What these pictures are not

- **Not drawn:** a refused microphone, both camera and microphone refused, and the states of
  steps 9 to 16 on a phone. The page handles the first two the same way as the refused camera.
- **Not the build.** They come from a prototype outside this repository; the green patterns are
  the browser's test picture, the names and the form on the shared screen are made up.
- **Open with the product owner:** whether the name shown is a screen name kept with the staff
  member's record by default (with the team's word alone when there is none), or the account's
  name with the screen name as an organisation's choice. The pictures show the first.

The supplier of the video service is named nowhere in these pictures, as ADR 0123 point 14
requires of the page.

# Updates

On this screen the superuser installs a new version of OpenPlanner or goes back to the
previous one. Open it with **Updates** in the menu. Only the superuser sees it. The work itself
is done by the updater on the server: it checks the signature of the new version, makes a
backup first and puts the previous version back itself if something goes wrong. This screen
only files the request and shows what the updater reports.

When this installation does not look for new versions (the host setting `UPDATE_REPOSITORY` is
empty), the screen says so.

## Version

The first card shows:

- **Running now**: the version that runs. A development build is not a release and is offered
  no updates.
- **Commit**: the commit the version was built from, with a link when it is known.
- **Channel**: *Releases*, or *Release candidates (every merge to main) and releases*. A chip
  says when the installation **updates itself**.
- **Latest version**: the newest version found, with a link to its notes.
- **Last checked**: when the server last looked for a new version. When that failed, a chip
  gives the code.

The channel, automatic updating and the interval between two checks are not set here but by
the superuser in **Advanced settings**, topic Updates (`UPDATE_CHANNEL`, `UPDATE_AUTO_APPLY`,
`UPDATE_CHECK_MINUTES`; see [Advanced settings](advanced-settings.md)).

<!-- field: check_now -->
### Check now

The server checks for a new version by itself at the interval shown under the button (every 60
minutes by default). **Check now** asks the worker to check ahead of time; the page then shows
*Check requested* until the worker has done so. The button is not available while a check is
already requested.

## Updating

When a newer version is available, a card says so with its release notes. Two things to know:

- The booking pages are unavailable for a few seconds during the update. Choose a quiet moment.
- An update or restore switches maintenance mode on before the backup is made and off again
  when it has succeeded or has been rolled back. When it fails completely, maintenance mode
  stays on (see [Maintenance mode](maintenance.md)).

<!-- field: target -->
### Choosing a version

Several newer versions can be on offer, the newest first. Each shows its notes, whether it is a
release candidate and when it was published. **Update to {version} now** installs the newest;
**Install this version** installs the one you choose. Only newer versions are offered: going
back is only possible with *Restore the previous version*. You can only file one request at a
time; while a request is open, a second is refused.

The notes have one line per change, with the title of the change. A title starts with a type:
`feat` is a new feature, `fix` a repair, `docs` design or documentation. A line of type `docs`,
such as `docs(adr): ...`, carries the label *design or documentation, nothing built*: it
describes a decision, a design or a manual, not a screen or feature that already works. When a
version consists of such lines only, a notice above the notes says that nothing changes in how
OpenPlanner works. Updating to such a version is possible but brings no new feature. The screen
never says this of a version without notes or with shortened notes.

## The open request

After you request an update the card **Open request** shows the updater's steps: waiting for
the updater, downloading the version, checking the signature, backing up the database and
starting and checking the new version. For a restore the steps are backing up, taking the
database back and starting. Each step is *not yet*, *in progress* or *done*. Should something
go wrong, the updater adds a step for rolling back to the previous version and the backup.

The list updates itself: the updater looks for requests every minute and reports every step,
and the page picks that up without you reloading. The page announces the current step for
screen readers. Without scripts, **Refresh status** reloads the page.

- **Withdraw request** is offered as long as the updater has not picked the request up.
- When no updater has picked the request up after 10 minutes, a warning says to check that the
  updater is installed on the server (see the runbook).

When the request has ended, the card shows the outcome: *Succeeded*, *Rolled back*, *Failed* or
*Withdrawn*, and for a rollback or failure the reason: the signature is not valid, the image was
not found, the backup failed, the database could not be taken back, the new version did not
become healthy, or the requested version is not running afterwards. When the restore fails too,
follow the runbook.

## Restore the previous version

After a successful update a card offers to go back to the version it replaced. Data entered
since the update is kept. This is possible until 24 hours after the update. After that, or
without a recent update, the card is not shown.

## The banner on every page

On every admin page a banner reports that a newer version is available, that a request is
running (with the current step), or the latest outcome. Choose **Go to updates** to come here.
**Remind me in 60 minutes** hides the banner for 60 minutes in your current session only.

## History

The table lists every request and outcome with the time, what happened (update or restore
requested, succeeded, failed or rolled back, request withdrawn), from which version to which,
and by whom: a person, the system (automatic updates), the updater on the server, or the server
itself outside the portal. When the version was changed outside the portal (by the installer or
by hand on the server), a notice says so. The same events are in the audit log; a link goes
to the update events there.

# launchd jobs (macOS)

The macOS equivalent of the Windows Task Scheduler entries.

- `com.canvas.brief.plist` — `canvas-brief --force` daily at 07:30.
- `com.canvas.backup.plist` — `canvas-backup` every Sunday at 20:00.

Logs go to `~/Library/Logs/canvas-brief.log` and `~/Library/Logs/canvas-backup.log`.

No at-logon job: launchd fires a calendar job that was missed while the Mac
was asleep as soon as it wakes, so the Windows "run at logon, skip if today's
brief exists" trick is not needed. (A job missed while fully powered off is
not replayed; run `canvas-brief` by hand that day.)

The plists carry `__REPO__` and `__HOME__` placeholders so nobody's paths are
committed. Install from the repo root (once):

    for f in ui/launchd/com.canvas.*.plist; do
      sed "s|__REPO__|$(pwd)|g; s|__HOME__|$HOME|g" "$f" > ~/Library/LaunchAgents/$(basename "$f")
    done
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.canvas.brief.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.canvas.backup.plist

Run once now to test / inspect / remove:

    launchctl kickstart -k gui/$(id -u)/com.canvas.brief
    launchctl kickstart -k gui/$(id -u)/com.canvas.backup
    launchctl print gui/$(id -u)/com.canvas.brief
    launchctl bootout gui/$(id -u)/com.canvas.brief

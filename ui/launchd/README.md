# launchd jobs (macOS)

The macOS equivalent of the Windows Task Scheduler entries. Absolute paths
are baked in; edit them if the repo lives elsewhere.

- `com.hulms.brief.plist` — `hulms-brief --force` daily at 07:30.
- `com.hulms.backup.plist` — `hulms-backup` every Sunday at 20:00.

Logs go to `~/Library/Logs/hulms-brief.log` and `~/Library/Logs/hulms-backup.log`.

No at-logon job: launchd fires a calendar job that was missed while the Mac
was asleep as soon as it wakes, so the Windows "run at logon, skip if today's
brief exists" trick is not needed. (A job missed while fully powered off is
not replayed; run `hulms-brief` by hand that day.)

Install (once):

    cp hulms-ui/launchd/com.hulms.*.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hulms.brief.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hulms.backup.plist

Run once now to test / inspect / remove:

    launchctl kickstart -k gui/$(id -u)/com.hulms.brief
    launchctl kickstart -k gui/$(id -u)/com.hulms.backup
    launchctl print gui/$(id -u)/com.hulms.brief
    launchctl bootout gui/$(id -u)/com.hulms.brief

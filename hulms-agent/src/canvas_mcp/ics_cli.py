"""Print the merged HULMS calendar (Canvas deadlines + plan events) as ICS.

The UI serves this output at /api/hulms.ics for phone calendar subscription.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from .agenda import collect_agenda, local_tz
from .core.client import cleanup_http_client
from .core.config import validate_config
from .core.db import list_plan_events, recent_briefs
from .core.ics import build_ics

AGENDA_DAYS = 90


async def run() -> int:
    # Windows pipes default to the ANSI code page; the feed must be UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", newline="")
    start = datetime.now(timezone.utc) - timedelta(days=7)
    agenda = await collect_agenda(start, start + timedelta(days=AGENDA_DAYS))
    if isinstance(agenda, dict):
        # Feed must stay servable: emit plan events even if Canvas is down.
        print(f"agenda unavailable: {agenda.get('error')}", file=sys.stderr)
        agenda = []
    events = list_plan_events()
    sys.stdout.write(build_ics(agenda, events, local_tz(), briefs=recent_briefs(2)))
    return 0


def main() -> None:
    if not validate_config():
        sys.exit(1)

    async def _run() -> int:
        try:
            return await run()
        finally:
            await cleanup_http_client()

    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

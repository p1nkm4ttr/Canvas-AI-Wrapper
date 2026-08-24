"""Print the course list as JSON — feeds the local UI's course sidebar."""

import asyncio
import json
import sys

from .core.client import cleanup_http_client
from .core.config import validate_config
from .core.db import cached_fetch_all


async def run() -> int:
    courses: list[dict] = []
    for state in ("active", "completed"):
        batch = await cached_fetch_all(
            "/courses",
            {"enrollment_state": state, "include[]": "term", "per_page": 100},
            max_age_seconds=6 * 3600,
        )
        if isinstance(batch, dict):
            print(json.dumps({"error": batch.get("error")}), file=sys.stdout)
            return 1
        for c in batch:
            courses.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "code": c.get("course_code"),
                "term": ((c.get("term") or {}).get("name") or "").strip(),
                "state": state,
            })
    print(json.dumps({"courses": courses}))
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

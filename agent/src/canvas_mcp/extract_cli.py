"""Bulk extraction CLI: index a whole course's files into the SQLite cache.

Usage:
    hulms-extract "Data Structures"     # module files + the flat files listing
    hulms-extract 4361 --modules-only   # only files placed in modules

Thin wrapper over core/indexing.py — the index_course_files MCP tool runs the
same code. Concluded-course access may be withdrawn; extracting early is the
hedge.
"""

import argparse
import asyncio
import sys

from .core.cache import resolve_course
from .core.client import cleanup_http_client
from .core.config import validate_config
from .core.db import extraction_coverage
from .core.indexing import index_course


async def run(identifier: str, include_all_files: bool) -> int:
    resolved = await resolve_course(identifier)
    if resolved is None:
        print(f"No course matches '{identifier}'.", file=sys.stderr)
        return 1
    course_id, course_name = resolved
    print(f"Extracting: {course_name} (id {course_id})")

    def progress(n: int, total: int, status: str, name: str) -> None:
        print(f"  [{n}/{total}] {status:<15} {name[:60]}", file=sys.stderr)

    result = await index_course(course_id, include_all_files, progress)
    for note in result["notes"]:
        print(f"  note: {note}", file=sys.stderr)
    print("\nDone:", ", ".join(f"{k}={v}" for k, v in sorted(result["counts"].items())))
    cov = extraction_coverage(int(course_id))
    print("Course cache now holds:", ", ".join(f"{k}={v}" for k, v in sorted(cov.items())))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a course's files into the local text cache")
    parser.add_argument("course", help="course id, code, or name substring")
    parser.add_argument("--modules-only", action="store_true",
                        help="skip the flat files listing; only files placed in modules")
    args = parser.parse_args()

    if not validate_config():
        sys.exit(1)

    async def _run() -> int:
        try:
            return await run(args.course, not args.modules_only)
        finally:
            await cleanup_http_client()

    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

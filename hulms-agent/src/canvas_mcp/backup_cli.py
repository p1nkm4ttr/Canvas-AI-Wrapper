"""Backup: everything git does not protect, in one zip.

Covers hulms.db (extracted course corpus, retrieval ledger, plan events,
briefs — the corpus is the hedge against concluded-course access being
withdrawn) and the spaces/ folders (memory, plans, dropped syllabi).

Deliberately EXCLUDED: .env — the Canvas token never goes into an archive
that might travel. The code itself lives in git and needs no backup here.

Usage:
    hulms-backup                # to <project root>/backups/
    hulms-backup D:\\usb        # to a chosen directory (USB, synced folder)
    hulms-backup --keep 20     # retention (default: last 10 kept)
"""

import argparse
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from .core import config
from .core.db import db_path


def _spaces_root() -> Path:
    return (config.REPO_ROOT.parent / "spaces").resolve()


def _default_dest() -> Path:
    return (config.REPO_ROOT.parent / "backups").resolve()


def create_backup(dest_dir: Path) -> Path:
    """Write one timestamped zip; returns its path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"hulms-backup-{stamp}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        # SQLite via its backup API — a plain file copy of a live database
        # (the MCP server may be mid-write) can capture a torn state.
        source_db = Path(db_path())
        if source_db.exists():
            snapshot = dest_dir / f".db-snapshot-{stamp}.tmp"
            src = sqlite3.connect(source_db)
            try:
                dst = sqlite3.connect(snapshot)
                with dst:
                    src.backup(dst)
                dst.close()
            finally:
                src.close()
            zf.write(snapshot, "hulms.db")
            snapshot.unlink()

        spaces = _spaces_root()
        if spaces.is_dir():
            for f in sorted(spaces.rglob("*")):
                if f.is_file():
                    zf.write(f, Path("spaces") / f.relative_to(spaces))

    return target


def prune_backups(dest_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups; returns what was removed."""
    backups = sorted(dest_dir.glob("hulms-backup-*.zip"))
    doomed = backups[:-keep] if keep > 0 else []
    for f in doomed:
        f.unlink()
    return doomed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up hulms.db and spaces/ (never the token)"
    )
    parser.add_argument("dest", nargs="?", help="destination directory (default: <project>/backups)")
    parser.add_argument("--keep", type=int, default=10,
                        help="how many backups to retain at the destination (default 10)")
    args = parser.parse_args()

    dest = Path(args.dest).resolve() if args.dest else _default_dest()
    try:
        target = create_backup(dest)
    except OSError as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        sys.exit(1)

    size_mb = target.stat().st_size / 1e6
    with zipfile.ZipFile(target) as zf:
        n = len(zf.namelist())
    removed = prune_backups(dest, args.keep)

    print(f"Backed up {n} files ({size_mb:.1f} MB) -> {target}")
    if removed:
        print(f"Pruned {len(removed)} old backup(s); keeping the newest {args.keep}.")


if __name__ == "__main__":
    main()

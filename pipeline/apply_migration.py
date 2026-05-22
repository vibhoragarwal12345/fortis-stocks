"""
Migration runner -- executes a .sql file against the Supabase Postgres
instance using the DATABASE_URL from .env.local. Use this instead of
copy-pasting into the Supabase web SQL editor.

The connection runs in autocommit mode so the whole file applies as a single
script (CREATE TABLE IF NOT EXISTS etc are individually idempotent already;
wrapping in one transaction would just block partial recovery).

Usage:
    python pipeline/apply_migration.py supabase/migrations/030_backtest_picks.sql
    python pipeline/apply_migration.py --all          # apply every *.sql in order
    python pipeline/apply_migration.py --sql "SELECT 1"  # ad-hoc query
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in .env.local", file=sys.stderr)
    sys.exit(2)

import psycopg  # noqa: E402


def apply(sql: str, label: str) -> None:
    print(f"\n--- applying: {label} ---")
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            # Drain any RETURNING / NOTICE-style output for visibility.
            try:
                while True:
                    if cur.description:
                        rows = cur.fetchall()
                        for r in rows[:20]:
                            print(" ", r)
                        if len(rows) > 20:
                            print(f"  ... ({len(rows) - 20} more)")
                    if not cur.nextset():
                        break
            except psycopg.ProgrammingError:
                pass
    print(f"--- ok: {label} ---")


def apply_file(path: Path) -> None:
    apply(path.read_text(encoding="utf-8"), path.name)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    if args[0] == "--all":
        for f in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
            apply_file(f)
    elif args[0] == "--sql":
        apply(args[1], "ad-hoc")
    else:
        for arg in args:
            apply_file(Path(arg) if Path(arg).is_absolute() else ROOT / arg)


if __name__ == "__main__":
    main()

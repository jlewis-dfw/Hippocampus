#!/usr/bin/env python3
"""Create the next numbered migration file in db/migrations/ from a short template.

  scripts/new_migration.py add_tags_table        -> db/migrations/0005_add_tags_table.sql

The number is the highest existing one plus one (the runner requires 0001..N without gaps). The name is lower-case words joined
by underscores. The template follows docs/Database/migrations.md: additive, idempotent where possible, no down-migration. After
editing it, run scripts/migrate_check.py scratch (or scripts/check.sh) to apply every migration from zero.
Exit status: 0 created, 2 refused.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent / "db" / "migrations"
TEMPLATE = """-- {name}
-- Additive only: new columns need defaults and nothing is dropped, so an older image keeps working against this schema.
-- There are no down-migrations; to undo, restore a backup (scripts/backup.py) or write a new forward migration.

-- Qualify every table as hippocampus.<name>, as the earlier migrations do. List new columns in scripts/migrate_check.py EXPECTED.

"""


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"[a-z][a-z0-9_]{1,60}", sys.argv[1]):
        print(__doc__, file=sys.stderr)
        return 2
    numbers = [int(m.group(1)) for f in FOLDER.glob("*.sql") if (m := re.match(r"(\d{4})_", f.name))]
    path = FOLDER / f"{max(numbers, default=0) + 1:04d}_{sys.argv[1]}.sql"
    path.write_text(TEMPLATE.format(name=path.name))
    print(f"created {path.relative_to(FOLDER.parent.parent).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

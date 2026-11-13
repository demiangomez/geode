#!/usr/bin/env python3
"""PostToolUse hook: advisory-only reminder when an edit touches a file whose
change usually needs a matching knowledge-base update. Never blocks — always exits 0.
"""
import json
import sys

WATCHED = {
    "geode/dbConnection.py": "DB schema/migrations — consider updating knowledge/architecture/database-schema.md if this adds/renames a table or migration.",
    "pyproject.toml": "Dependency declaration — environment.yml may now be out of sync (see knowledge/known-issues.md).",
    "environment.yml": "Dependency declaration — pyproject.toml may now be out of sync (see knowledge/known-issues.md).",
    "setup.py": "CLI script list — consider updating knowledge/architecture/cli-reference-map.md if a script was added/removed.",
    "database/gnss_data_dump.sql": "This is a pg_dump snapshot, not a migration source — see knowledge/architecture/database-schema.md. Don't hand-edit it to represent a schema change.",
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return 0

    for suffix, note in WATCHED.items():
        if file_path.endswith(suffix):
            print(f"[knowledge-base reminder] {suffix}: {note}")
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())

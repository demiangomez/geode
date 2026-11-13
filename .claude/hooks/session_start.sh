#!/usr/bin/env bash
# SessionStart hook: surface branch/working-tree state and point at the knowledge base
# before any file exploration happens. Advisory only — never fails the session.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 0

if git rev-parse --git-dir > /dev/null 2>&1; then
  branch="$(git branch --show-current 2>/dev/null)"
  dirty="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  echo "GeoDE repo: branch '${branch:-detached}', ${dirty} changed file(s)."
  if [ "${branch}" != "master" ] && [ -n "${branch}" ]; then
    echo "Not on master — if you're relying on claims in knowledge/STATUS.md or knowledge/architecture/*.md about what's 'currently' implemented, verify against this branch's actual diff first."
  fi
fi

if [ -f knowledge/INDEX.md ]; then
  echo "Project knowledge base: knowledge/INDEX.md (architecture docs, STATUS.md, known-issues.md, decisions/, checkpoints/). Read it before re-deriving architecture from scratch."
fi

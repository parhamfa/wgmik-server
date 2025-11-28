#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# If not a git repo yet, init it
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git init
fi

# Stage everything (respecting .gitignore)
git add -A

# If nothing to commit, exit quietly
if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

msg="wip $(date '+%Y-%m-%d %H:%M:%S')"
git commit -m "$msg"

# Push if origin exists, otherwise just leave local commit
if git remote get-url origin >/dev/null 2>&1; then
  git push origin HEAD
else
  echo "Committed locally (no origin remote configured yet)."
fi



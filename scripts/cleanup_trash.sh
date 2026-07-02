#!/usr/bin/env bash
# One-shot physical cleanup. The Cowork sandbox cannot delete files on this
# mount, so junk was quarantined instead. Run this once from the repo root:
#   bash scripts/cleanup_trash.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Removing quarantined junk (.trash-to-delete, 19MB)..."
rm -rf .trash-to-delete

echo "Removing stale git lock artifacts..."
find .git -name '*.stale' -delete 2>/dev/null || true
find . -path ./node_modules -prune -o -name '*.lock.stale' -print0 2>/dev/null | xargs -0 rm -f || true

echo "Removing scattered .DS_Store and __pycache__..."
find . -name '.DS_Store' -not -path '*/node_modules/*' -delete
find . -name '__pycache__' -type d -not -path '*/node_modules/*' -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true

echo "Done. Repo is physically clean."

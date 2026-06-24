#!/usr/bin/env bash
# Remove macOS AppleDouble (._*) sidecar files under the repo.
# Safe to run anytime; skips nothing except unreadable paths.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
removed=0

while IFS= read -r -d '' f; do
  rm -f "$f"
  removed=$((removed + 1))
done < <(find "$ROOT" -name '._*' -type f -print0 2>/dev/null)

if [[ "$removed" -eq 0 ]]; then
  echo "No AppleDouble (._*) files found."
else
  echo "Removed $removed AppleDouble (._*) file(s)."
fi

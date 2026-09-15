#!/usr/bin/env bash
set -euo pipefail

# Do not run two crawls at the same time.
exec 9>/tmp/crawl-sport.lock
flock -n 9 || exit 0

cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

git pull --ff-only origin main
python3 crawler.py
git add sport.m3u8

if ! git diff --cached --quiet; then
  git commit -m "Auto update sport.m3u8"
  git push origin main
fi

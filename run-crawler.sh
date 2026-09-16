#!/usr/bin/env bash
set -euo pipefail

# Do not run two crawls at the same time.
exec 9>/tmp/crawl-sport.lock
flock -n 9 || exit 0

cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Optional secrets/configuration for Telegram notifications. Keep this file on
# the VPS only; it is deliberately not committed to the repository.
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
elif [[ -f /etc/crawl-sport.env ]]; then
  set -a
  . /etc/crawl-sport.env
  set +a
fi

git pull --ff-only origin main
python3 crawler.py
git add sport.m3u8

if ! git diff --cached --quiet; then
  git commit -m "Auto update sport.m3u8"
  git push origin main
fi

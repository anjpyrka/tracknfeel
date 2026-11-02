#!/usr/bin/env bash
# Rebuild the encrypted bundles from everything in inbox/ and publish.
set -e
cd "$(dirname "$0")"
python3 pipeline/build.py
git add -A docs
git commit -m "data: rebuild $(date +%Y-%m-%d)" || true
git push

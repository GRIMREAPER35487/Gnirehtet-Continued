#!/usr/bin/env bash
# Gnirehtet Continued by Synthos - Auto-Recovery Watchdog Launcher
# Licensed under Apache 2.0

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

# Run Python watchdog supervisor with forwarded arguments
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$DIR/gnirehtet_watchdog.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$DIR/gnirehtet_watchdog.py" "$@"
else
    echo "Error: Python 3 is required to run the Gnirehtet supervisor." >&2
    exit 1
fi

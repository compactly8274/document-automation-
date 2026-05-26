#!/usr/bin/env bash
# Usage: ./log.sh "message"
# Appends a manual entry to the homelab changelog.
# Requires the homedocs container to be running.

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 \"message\"" >&2
    echo "Example: $0 \"Switched Ollama to gemma3:12b for better reasoning\"" >&2
    exit 1
fi

docker exec homedocs python -m homedocs log "$*"

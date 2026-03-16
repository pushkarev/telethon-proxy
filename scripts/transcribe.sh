#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv-stt/bin/activate
exec python scripts/transcribe_audio.py "$@"

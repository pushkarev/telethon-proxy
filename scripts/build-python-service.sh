#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DIST_DIR="$ROOT_DIR/dist/background"
BUILD_ROOT="$ROOT_DIR/build/pyinstaller"
SPEC_DIR="$BUILD_ROOT/spec"
WORK_DIR="$BUILD_ROOT/work"
PYTHON_BIN="${PYINSTALLER_PYTHON:-$ROOT_DIR/.venv-build/bin/python}"

mkdir -p "$DIST_DIR" "$SPEC_DIR" "$WORK_DIR"
rm -rf "$DIST_DIR/telethon-proxy-service"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Missing build Python at $PYTHON_BIN" >&2
  echo "Create it with: python3 -m venv .venv-build && .venv-build/bin/pip install -r telegram-project/requirements.txt pyinstaller" >&2
  exit 1
fi

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name telethon-proxy-service \
  --distpath "$DIST_DIR" \
  --workpath "$WORK_DIR" \
  --specpath "$SPEC_DIR" \
  --paths "$ROOT_DIR/telegram-project" \
  --add-data "$ROOT_DIR/telegram-project/webui:webui" \
  "$ROOT_DIR/telegram-project/proxy_service.py"

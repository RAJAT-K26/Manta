#!/usr/bin/env bash
# Build a single-file Manta binary in ./dist/manta
# Run from the project root inside your activated venv.

set -e

if ! command -v pyinstaller >/dev/null 2>&1; then
  echo "installing pyinstaller…"
  pip install pyinstaller
fi

rm -rf build dist
pyinstaller manta.spec

echo
echo "✓ built: dist/manta"
echo "  test it:    ./dist/manta"
echo "  share it:   zip the file, send to friends"

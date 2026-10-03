#!/usr/bin/env bash
# Slakebind setup: install -> verify -> exit. Never starts a server, never
# needs sudo, and never touches anything outside this project.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "slakebind: '$PYTHON' was not found. Install Python 3.8+ or set PYTHON=/path/to/python3." >&2
  exit 1
fi

# 1. Upgrade packaging tooling first: an old pip silently fails modern
#    editable installs. Best effort, because Slakebind itself needs nothing.
"$PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 \
  || echo "slakebind: note - could not refresh pip/setuptools/wheel (offline?); continuing."

# 2. Put the 'slakebind' console script on PATH. The file also runs standalone,
#    so a failure here is not fatal - we say so and carry on.
if ! "$PYTHON" -m pip install --no-build-isolation -e . >/dev/null 2>&1; then
  echo "slakebind: note - editable install skipped; run the tool as '$PYTHON slakebind.py ...'."
fi

# 3. Self-check: the tool must import and run with the standard library alone.
"$PYTHON" -c "import slakebind"
"$PYTHON" slakebind.py --version >/dev/null
"$PYTHON" slakebind.py ratio --hide 8.5kg --tannin 340g >/dev/null

echo "Slakebind is ready."
echo "  try:  $PYTHON slakebind.py --help"
echo "  demo: bash demo.sh"
echo "  tests: $PYTHON -m pytest"

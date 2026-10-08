#!/usr/bin/env sh
# One-click start on macOS / Linux: creates the virtualenv on first run, installs, opens the browser.
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "Python 3.11-3.13 is required"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "First run: creating .venv and installing packages. This takes a few minutes."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "The first background removal downloads a ~180 MB model once; that upload takes longer."
( sleep 2; (xdg-open http://127.0.0.1:8000 || open http://127.0.0.1:8000) >/dev/null 2>&1 ) &
exec .venv/bin/python -m uvicorn main:app --port 8000

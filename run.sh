#!/usr/bin/env sh
# One-click start on macOS / Linux: finds Python 3.11-3.13, creates the virtualenv on first run,
# installs, opens the browser. On a Mac you can also double-click run.command in Finder.
set -e
cd "$(dirname "$0")"

# The python3 that ships with macOS is often 3.9: look for a recent one first.
ok_version() { "$1" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; }
PY=""
for c in ${PYTHON:-} python3.13 python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && ok_version "$c"; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11, 3.12 or 3.13 is required."
  if [ "$(uname)" = "Darwin" ]; then
    echo "  macOS: install it from https://www.python.org/downloads/macos/"
    echo "         or with Homebrew:  brew install python@3.12"
  else
    echo "  Linux: install python3.12 and python3.12-venv with your package manager."
  fi
  echo "Then run this again (or set PYTHON=/path/to/python3.12)."
  exit 1
fi

# A .venv made by another Python (or copied from another machine) is rebuilt.
if [ -x .venv/bin/python ] && ! ok_version .venv/bin/python; then rm -rf .venv; fi
if [ ! -x .venv/bin/python ]; then
  echo "First run: creating .venv with $($PY --version) and installing packages. This takes a few minutes."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "The first background removal downloads a ~180 MB model once; that upload takes longer."
( sleep 2; (open http://127.0.0.1:8000 || xdg-open http://127.0.0.1:8000) >/dev/null 2>&1 ) &
exec .venv/bin/python -m uvicorn main:app --port 8000

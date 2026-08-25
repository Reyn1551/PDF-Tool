#!/usr/bin/env bash
# ============================================================
#  PDF Tool - quick launcher (double-click / run directly)
#  Usage:  ./run.sh         or double-click it in your file manager
# ============================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY=python3

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install it first: sudo apt install python3 python3-tk" >&2
    exit 1
fi

# Check core dependencies; auto-install if missing (runs only once).
if ! "$PY" -c "import fitz" >/dev/null 2>&1; then
    echo ">> Installing dependencies (PyMuPDF) ..."
    "$PY" -m pip install --user -r requirements.txt \
        || "$PY" -m pip install --break-system-packages --user -r requirements.txt \
        || { echo "Automatic dependency installation failed. Run manually:"; \
             echo "   $PY -m pip install --user -r requirements.txt"; exit 1; }
fi

# tkinter is required for the GUI
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    echo "ERROR: the tkinter module is missing." >&2
    echo "Install it first:  sudo apt install python3-tk" >&2
    exit 1
fi

exec "$PY" pdf_tool.py "$@"

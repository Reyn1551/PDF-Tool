#!/usr/bin/env bash
# ============================================================
#  PDF Tool — peluncur cepat (double-click / jalankan langsung)
#  Pakai:  ./run.sh          atau double-click di file manager
# ============================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY=python3

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "ERROR: python3 tidak ditemukan. Pasang dulu: sudo apt install python3 python3-tk" >&2
    exit 1
fi

# Cek dependensi inti; pasang otomatis bila belum ada (hanya sekali).
if ! "$PY" -c "import fitz" >/dev/null 2>&1; then
    echo ">> Memasang dependensi (PyMuPDF) …"
    "$PY" -m pip install --user -r requirements.txt \
        || "$PY" -m pip install --break-system-packages --user -r requirements.txt \
        || { echo "Gagal memasang dependensi otomatis. Jalankan manual:"; \
             echo "   $PY -m pip install --user -r requirements.txt"; exit 1; }
fi

# tkinter wajib ada untuk GUI
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    echo "ERROR: modul tkinter tidak ada." >&2
    echo "Pasang dulu:  sudo apt install python3-tk" >&2
    exit 1
fi

exec "$PY" pdf_tool.py "$@"

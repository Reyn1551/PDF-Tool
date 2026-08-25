#!/usr/bin/env bash
# ============================================================
#  PDF Tool - optional application-shortcut installer
#  Adds "PDF Tool" to the Linux application menu + icon.
#  Usage:  ./install.sh       (uninstall: ./install.sh --remove)
# ============================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

APP_DIR="$(pwd)"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons"
DESKTOP_FILE="${DESKTOP_DIR}/pdf-tool.desktop"
ICON_FILE="${ICON_DIR}/pdf-tool.png"

if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$DESKTOP_FILE" "$ICON_FILE"
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    echo "PDF Tool removed from the application menu."
    exit 0
fi

mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
[[ -f assets/icon.png ]] && cp -f assets/icon.png "$ICON_FILE"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=PDF Tool
GenericName=PDF Editor & Converter
Comment=Merge, split, compress, convert, edit, protect PDF — layaknya iLovePDF
Exec=${APP_DIR}/run.sh
Icon=${ICON_FILE}
Terminal=false
Categories=Office;Graphics;Utility;
Keywords=pdf;merge;split;compress;convert;edit;
StartupWMClass=Pdf-tool
EOF

chmod +x run.sh pdf_tool.py 2>/dev/null || true
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "✔ PDF Tool installed into the application menu."
echo "   Launcher : ${APP_DIR}/run.sh"
echo "   Shortcut : ${DESKTOP_FILE}"
echo "   Open via the application menu (search 'PDF Tool') or run ./run.sh"

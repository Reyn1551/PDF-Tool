#!/usr/bin/env bash
# Build PDF Tool web for a target (default: this machine, linux/amd64).
#
# Produces a single static binary at bin/pdftoolweb that links the prebuilt
# PDFium shared library via an rpath, so it runs with no LD_LIBRARY_PATH.
#
# Requirements on the build host:
#   - Go 1.22+ (1.27 recommended)
#   - A C toolchain (gcc) for cgo
#   - PDFium prebuilt binaries (downloaded automatically if absent)
#   - Ghostscript (gs) — required at RUNTIME, not build time
#
# Env overrides:
#   PDFIUM_DIR   where to put PDFium (default: $HOME/pdfium)
#   PDFIUM_TAG   pdfium-binaries release tag (default: chromium/8125)
#   GOFLAGS      extra go flags
#   CGO_CFLAGS / CGO_LDFLAGS
set -euo pipefail

cd "$(dirname "$0")"

PDFIUM_DIR="${PDFIUM_DIR:-$HOME/pdfium}"
PDFIUM_TAG="${PDFIUM_TAG:-chromium/8125}"
GO="${GO:-go}"

# ---- locate go ---------------------------------------------------------
if ! command -v "$GO" >/dev/null 2>&1; then
  if [ -x "$HOME/go/bin/go" ]; then GO="$HOME/go/bin/go"; export PATH="$HOME/go/bin:$PATH"
  else echo "error: go not found (install Go 1.22+)" >&2; exit 1; fi
fi
echo "==> go: $($GO version)"

# ---- PDFium ------------------------------------------------------------
if [ ! -f "$PDFIUM_DIR/lib/libpdfium.so" ]; then
  echo "==> downloading PDFium ($PDFIUM_TAG)"
  mkdir -p "$PDFIUM_DIR"
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) asset="pdfium-linux-x64.tgz" ;;
    aarch64|arm64) asset="pdfium-linux-arm64.tgz" ;;
    *) echo "error: unsupported arch $arch" >&2; exit 1 ;;
  esac
  url="https://github.com/bblanchon/pdfium-binaries/releases/download/$PDFIUM_TAG/$asset"
  echo "    $url"
  curl -fsSL "$url" -o /tmp/pdfium.tgz
  tar -xzf /tmp/pdfium.tgz -C "$PDFIUM_DIR"
  rm -f /tmp/pdfium.tgz
fi
[ -f "$PDFIUM_DIR/lib/libpdfium.so" ] || { echo "error: libpdfium.so missing in $PDFIUM_DIR" >&2; exit 1; }

# pkg-config file so go-pdfium's cgo finds it
mkdir -p "$PDFIUM_DIR/pkgconfig"
cat > "$PDFIUM_DIR/pkgconfig/pdfium.pc" <<EOF
prefix=$PDFIUM_DIR
libdir=\${prefix}/lib
includedir=\${prefix}/include

Name: PDFium
Description: PDFium
Version: 8125
Requires:
Libs: -L\${libdir} -lpdfium
Cflags: -I\${includedir}
EOF

export PKG_CONFIG_PATH="$PDFIUM_DIR/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

# ---- build -------------------------------------------------------------
echo "==> building"
mkdir -p bin
"$GO" build -tags pdfium_experimental \
  -ldflags "-s -w -r $PDFIUM_DIR/lib" \
  -o bin/pdftoolweb .

echo "==> done: $(pwd)/bin/pdftoolweb"
ls -la bin/pdftoolweb
echo
echo "Run it with:"
echo "  ./bin/pdftoolweb -addr :8080"
echo "It finds libpdfium via rpath ($PDFIUM_DIR/lib); no LD_LIBRARY_PATH needed."
echo "Ghostscript (gs) must be installed for compress/protect/unlock."

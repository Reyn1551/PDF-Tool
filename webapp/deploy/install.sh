#!/usr/bin/env bash
# PDF Tool web — one-line installer for Debian/Ubuntu VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/web/webapp/deploy/install.sh | sudo bash
#
# Options (env vars):
#   PDFTOOL_REPO=owner/repo   GitHub repo (default: OWNER/REPO)
#   VERSION=v1.2.3            release tag (default: latest)
#   PORT=8080                 listen port (default: 8080)
#   PREFIX=/opt/pdftool       install dir
#
# What it does: installs Ghostscript, downloads the prebuilt release tarball
# (binary + libpdfium.so), creates a service user, installs + starts a systemd
# unit, and prints the URL. Re-run to upgrade; `--uninstall` removes it.
set -euo pipefail

PDFTOOL_REPO="${PDFTOOL_REPO:-OWNER/REPO}"
VERSION="${VERSION:-latest}"
PORT="${PORT:-8080}"
PREFIX="${PREFIX:-/opt/pdftool}"
SERVICE=pdftoolweb
GH="https://github.com/${PDFTOOL_REPO}"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

# ---------- root ----------
if [ "$(id -u)" -ne 0 ]; then
  # When piped from curl, stdin is the script and cannot be re-read, so
  # re-exec is only safe when we were started from a file.
  if [ -f "$0" ] && command -v sudo >/dev/null 2>&1; then
    info "re-running with sudo"
    exec sudo -E bash "$0" "$@"
  fi
  die "run as root, e.g.: curl -fsSL <install.sh-url> | sudo bash"
fi

# ---------- uninstall ----------
if [ "${1:-}" = "--uninstall" ]; then
  info "stopping + removing $SERVICE"
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE}.service"
  systemctl daemon-reload || true
  rm -rf "$PREFIX"
  id pdftool >/dev/null 2>&1 && userdel pdftool 2>/dev/null || true
  info "removed. (Ghostscript was left installed)"
  exit 0
fi

# ---------- deps ----------
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar  >/dev/null 2>&1 || die "tar is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required (this installer targets Debian/Ubuntu VPS)"

if ! command -v gs >/dev/null 2>&1; then
  info "installing Ghostscript (needed for compress/protect/unlock)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ghostscript >/dev/null
else
  info "Ghostscript already installed"
fi

# ---------- arch ----------
case "$(uname -m)" in
  x86_64|amd64) TRIPLE=amd64 ;;
  aarch64|arm64) TRIPLE=arm64 ;;
  *) die "unsupported architecture: $(uname -m)" ;;
esac

# ---------- download release ----------
if [ "$VERSION" = "latest" ]; then
  URL="${GH}/releases/latest/download/pdftoolweb-linux-${TRIPLE}.tar.gz"
else
  URL="${GH}/releases/download/${VERSION}/pdftoolweb-linux-${TRIPLE}.tar.gz"
fi
info "downloading $URL"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fL --retry 3 --retry-delay 2 "$URL" -o "$tmp/pdftool.tar.gz" \
  || die "download failed — check that a release exists for ${VERSION} (${TRIPLE})"

info "extracting to $PREFIX"
mkdir -p "$PREFIX"
tar -xzf "$tmp/pdftool.tar.gz" -C "$PREFIX" --strip-components=1
test -x "$PREFIX/bin/pdftoolweb" || die "tarball is missing bin/pdftoolweb"
test -f "$PREFIX/lib/libpdfium.so" || warn "libpdfium.so missing; the binary may not start"

# ---------- service user ----------
if ! id pdftool >/dev/null 2>&1; then
  info "creating service user 'pdftool'"
  useradd -r -s /usr/sbin/nologin -d "$PREFIX" pdftool
fi
chown -R pdftool:pdftool "$PREFIX"

# ---------- systemd ----------
info "installing systemd service"
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=PDF Tool web (Go + PDFium + Ghostscript)
After=network.target

[Service]
Type=simple
User=pdftool
Group=pdftool
WorkingDirectory=${PREFIX}
ExecStart=${PREFIX}/bin/pdftoolweb -addr 127.0.0.1:${PORT}
Restart=on-failure
RestartSec=2
MemoryMax=768M
MemoryHigh=640M
CPUQuota=100%
TasksMax=64
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=${PREFIX}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE"

# ---------- health check ----------
info "waiting for the service to come up"
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  info "OK — PDF Tool is running"
else
  warn "service did not answer on :${PORT}; check: journalctl -u ${SERVICE} -n 50"
fi

cat <<EOF

  PDF Tool web is installed.

    Local:   http://127.0.0.1:${PORT}
    Public:  http://<your-server-ip>:${PORT}   (open the firewall/security group)

  Commands:
    systemctl status ${SERVICE}
    journalctl -u ${SERVICE} -f
    sudo bash $0 --uninstall

  For HTTPS, put nginx in front (see deploy/pdftoolweb.nginx).

EOF

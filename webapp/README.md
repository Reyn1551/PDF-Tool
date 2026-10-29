# PDF Tool — Web

A fast, single-binary web PDF tool built for a **1 vCPU / 1 GB RAM VPS**.

- **Go 1.27** backend, stdlib `net/http` (no heavy framework)
- **[PDFium](https://pdfium.googlesource.com/pdfium/)** (via [go-pdfium](https://github.com/klippa-app/go-pdfium)) for open / render / text / merge / split / rotate / reorder / redact / watermark
- **Ghostscript** for compress + encrypt/decrypt (the two things PDFium can't do)
- **Vanilla-JS SPA** with a virtualized, lazy-loading thumbnail grid — no build step, no framework

One process is the whole app. PDFium is serialized to a single worker (correct on 1 vCPU, and it keeps memory flat). Thumbnails live in a byte-budgeted LRU so the footprint stays bounded.

## Privacy

This app is designed to be safe to self-host for untrusted users:

- **Zero third-party requests.** The SPA loads only `/style.css`, `/app.js`, and
  same-origin `/api/*`. No analytics, no CDNs, no external fonts. A Content-Security-Policy
  header enforces this (`default-src 'self'`) so a future regression cannot silently
  add one.
- **Documents never leave the server.** Processing is in-memory PDFium plus a local
  Ghostscript process; temp files live in `PrivateTmp` (systemd) or the container
  filesystem and are deleted when the request finishes.
- **Sessions are ephemeral.** Idle sessions are swept after 30 minutes (PDFium
  handle closed, bytes dropped). Nothing is written to disk or a database.
- **No cookies, no accounts, no logs of document content.** Session ids are random
  64-bit values; the server never learns who you are beyond the rate-limit counter
  (per-IP upload cap, in memory, expires hourly).
- **Same-origin only.** No CORS headers are sent, so a hostile page in another tab
  cannot drive the API.

## Features

| Category | Tools |
|---|---|
| **Organize** | Delete pages · Rotate · Reverse order · Split (extract range) · Move selected |
| **Security** | Protect (password) · Unlock · Watermark (text) · Redact (white-out) |
| **Optimize** | Compress (3 quality levels) |
| **Export** | Extract text · Export images (PNG/JPG) · Download edited PDF |

## Quick start (this machine)

```bash
./build.sh          # downloads PDFium if missing, builds bin/pdftoolweb
./bin/pdftoolweb -addr :8080
# open http://localhost:8080
```

`build.sh` needs Go 1.22+, a C toolchain (cgo), and network access to fetch the
prebuilt PDFium. **Ghostscript (`gs`) must be installed at runtime** for
compress / protect / unlock:

```bash
sudo apt install ghostscript
```

The binary links PDFium through an **rpath**, so it runs with **no**
`LD_LIBRARY_PATH`.

## Makefile

```bash
make build   # build bin/pdftoolweb
make run     # build + run on :8080
make test    # go test ./...
make clean
```

## API

All mutating ops return the updated session summary
(`{id, name, pages, size, version}`). `version` increments on every edit so the
client can invalidate stale thumbnails.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + cache stats |
| POST | `/api/upload` | multipart `file` → new session (per-IP rate limited) |
| GET | `/api/session/{id}` | session info + metadata |
| DELETE | `/api/session/{id}` | close + drop session |
| GET | `/api/session/{id}/page/{n}/thumb?w=` | PNG thumbnail (cached) |
| GET | `/api/session/{id}/page/{n}/full?w=` | full-page PNG |
| GET | `/api/session/{id}/page/{n}/text` | page text |
| GET | `/api/session/{id}/metadata` | info dictionary |
| POST | `/api/session/{id}/delete` | `{pages:[i]}` |
| POST | `/api/session/{id}/rotate` | `{pages:[i], deg}` |
| POST | `/api/session/{id}/reorder` | `{order:[...]}` |
| POST | `/api/session/{id}/reverse` | `{}` |
| POST | `/api/session/{id}/split` | `{start,end}` → new session |
| POST | `/api/session/{id}/merge` | `{ids:[...]}` → merged into first |
| POST | `/api/session/{id}/watermark` | `{text,wRatio,hRatio,pos}` or multipart image |
| POST | `/api/session/{id}/redact` | `{rects:[{page,x,y,w,h}]}` (0–1, top-left) |
| POST | `/api/session/{id}/protect` | `{user,owner}` |
| POST | `/api/session/{id}/unlock` | `{password}` |
| POST | `/api/session/{id}/compress` | `{level:0\|1\|2}` |
| GET | `/api/session/{id}/download` | edited PDF |
| GET | `/api/session/{id}/export/text` | full text |
| GET | `/api/session/{id}/export/images?w=&fmt=` | per-page images (data URLs) |

## Installing it (pick one)

Installation is layered so every user has a path with **zero build steps**.

| You are… | Use | Effort |
|---|---|---|
| Just want to use a PDF tool | a hosted instance (no install) | none |
| Self-hosting on a VPS, not technical | **one-line installer** | 1 command |
| Self-hosting, prefer containers | **Docker Compose** | 1 command |
| Developer / want to hack on it | build from source | 2 commands |
| Desktop user | the Tkinter app + AppImage | double-click |

### 1. One-line installer (easiest for a VPS)

On a fresh Debian/Ubuntu server:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/web/webapp/deploy/install.sh | sudo bash
```

It installs Ghostscript, downloads the prebuilt binary + `libpdfium.so`, creates
a `pdftool` user, installs a `systemd` unit (768 MB cap), starts it, and prints
the URL. Upgrade by re-running it; remove with:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/web/webapp/deploy/install.sh | sudo bash -s -- --uninstall
```

### 2. Docker Compose (no dependencies on the host)

```bash
cd webapp
docker compose up -d          # uses the prebuilt GHCR image
# open http://localhost:8080
```

Or build the image yourself: `docker build -t pdftoolweb . && docker run -d -p 8080:8080 --memory=768m pdftoolweb`.

The image is multi-arch (`linux/amd64`, `linux/arm64`) and already contains
Ghostscript + PDFium.

### 3. Build from source

```bash
sudo apt install -y golang gcc pkg-config ghostscript   # build + runtime deps
./build.sh          # downloads PDFium automatically, builds bin/pdftoolweb
./bin/pdftoolweb -addr :8080
```

`build.sh` produces a **relocatable** layout when `RELOCATABLE=1` is set
(`bin/pdftoolweb` + `lib/libpdfium.so` with an `$ORIGIN/../lib` rpath) — that is
exactly what the release tarballs ship, so it runs from any directory with no
`LD_LIBRARY_PATH`.

### 4. Reverse proxy + TLS

Put nginx in front for HTTPS and a 160 MB upload limit:

```bash
sudo cp deploy/pdftoolweb.nginx /etc/nginx/sites-available/pdftoolweb
sudo ln -s /etc/nginx/sites-available/pdftoolweb /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Release pipeline

`.github/workflows/release.yml` builds, on a `v*` tag:

- `pdftoolweb-linux-amd64.tar.gz` and `pdftoolweb-linux-arm64.tar.gz`
  (binary + `libpdfium.so` + `deploy/`), published as GitHub Release assets;
- a multi-arch image at `ghcr.io/OWNER/REPO`.

So users never need Go, a compiler, PDFium, or Ghostscript installed to get a
working binary — they download one tarball or pull one image.

## Why this fits 1 GB

- **One PDFium context total.** PDFium isn't safe for concurrent use of one
  context, and on 1 vCPU there's no second core to overlap on — so all PDFium
  calls serialize through a single worker. That's correct *and* keeps memory
  flat (no per-request context churn).
- **Bounded thumbnail cache.** Thumbnails are the only unbounded artifact; they
  live in an LRU with a 64 MB byte budget and are evicted as you scroll.
- **Bounded sessions.** Each open document is one PDFium handle; the store caps
  concurrent sessions (503 past the limit) and sweeps anything idle for 30 minutes.
- **Ghostscript is spawned per compress/protect/unlock** and exits, so it only
  uses memory during that operation.
- **`GOMAXPROCS=1`** in the container keeps the Go scheduler from oversubscribing.

## Project layout

```
webapp/
├── main.go                 entrypoint (embeds web/, starts server)
├── web/                    SPA (index.html, app.js, style.css)
├── internal/
│   ├── pdfiumx/            single-worker PDFium wrapper
│   ├── ops/                high-level ops (PDFium + Ghostscript)
│   ├── store/              sessions + byte-budgeted thumbnail LRU
│   └── server/             HTTP handlers + SPA serving
├── deploy/                 systemd unit + nginx config
├── build.sh                one-shot build (fetches PDFium)
├── Makefile
└── Dockerfile
```

## Notes & limits

- **Redaction is white-out** (opaque rectangle + flatten). It visually and
  structurally bakes the covered area into the page content. For full
  content-stream redaction (removing the underlying text objects) that's a
  larger PDFium operation and is not wired up here.
- **Watermark text** uses an embedded DejaVu Sans font; image watermarks accept
  any decodable image (PNG transparency is preserved).
- **Merge** imports pages between documents; very large merges are memory-bound
  by the destination document size.

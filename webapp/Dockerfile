# PDF Tool web — Dockerfile
#
# Multi-stage: build with Go + cgo + PDFium, ship a slim runtime with
# Ghostscript (needed for compress/protect/unlock) and the PDFium shared lib.
#
#   docker build -t pdftoolweb .
#   docker run -d -p 8080:8080 --name pdftoolweb pdftoolweb
#
# For a 1 GB box, cap memory:
#   docker run -d -p 8080:8080 --memory=768m --memory-swap=768m pdftoolweb

# ---------- build ----------
FROM golang:1.27-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Download prebuilt PDFium (glibc x64) + write a pkg-config file.
ARG PDFIUM_TAG=chromium/8125
RUN set -eux; \
    curl -fsSL "https://github.com/bblanchon/pdfium-binaries/releases/download/${PDFIUM_TAG}/pdfium-linux-x64.tgz" -o /tmp/pdfium.tgz; \
    mkdir -p /opt/pdfium; \
    tar -xzf /tmp/pdfium.tgz -C /opt/pdfium; \
    rm -f /tmp/pdfium.tgz; \
    mkdir -p /opt/pdfium/pkgconfig; \
    printf 'prefix=/opt/pdfium\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n\nName: PDFium\nDescription: PDFium\nVersion: 8125\nRequires:\nLibs: -L${libdir} -lpdfium\nCflags: -I${includedir}\n' \
        > /opt/pdfium/pkgconfig/pdfium.pc

ENV PKG_CONFIG_PATH=/opt/pdfium/pkgconfig

# Copy source and build.
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 go build -tags pdfium_experimental \
        -ldflags "-s -w -r /opt/pdfium/lib" \
        -o /out/pdftoolweb .

# ---------- runtime ----------
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ghostscript \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 10001 pdftool

# PDFium shared library (rpath points here).
COPY --from=build /opt/pdfium/lib/libpdfium.so /opt/pdfium/lib/libpdfium.so
COPY --from=build /out/pdftoolweb /usr/local/bin/pdftoolweb

ENV ADDR=:8080
EXPOSE 8080
USER pdftool
# 1 vCPU: GOMAXPROCS=1 keeps the Go scheduler from oversubscribing.
ENV GOMAXPROCS=1
ENTRYPOINT ["pdftoolweb"]

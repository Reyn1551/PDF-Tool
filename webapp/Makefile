# PDF Tool web — Makefile
#
# Targets:
#   make build      build bin/pdftoolweb (needs Go + PDFium + gs)
#   make run        build then run on :8080
#   make test       run the Go test suite
#   make clean      remove build artifacts
#
# PDFIUM_DIR defaults to $HOME/pdfium. build.sh downloads it if missing.

PDFIUM_DIR ?= $(HOME)/pdfium
export PKG_CONFIG_PATH := $(PDFIUM_DIR)/pkgconfig$(PKG_CONFIG_PATH:+:$(PKG_CONFIG_PATH))
GO ?= go
TAGS := pdfium_experimental
LDFLAGS := -s -w -r $(PDFIUM_DIR)/lib

.PHONY: build run test clean

build:
	@command -v $(GO) >/dev/null 2>&1 || { echo "go not found"; exit 1; }
	@test -f $(PDFIUM_DIR)/lib/libpdfium.so || ./build.sh
	@mkdir -p bin
	$(GO) build -tags $(TAGS) -ldflags "$(LDFLAGS)" -o bin/pdftoolweb .
	@echo "built bin/pdftoolweb"

run: build
	./bin/pdftoolweb -addr :8080

test:
	$(GO) test -tags $(TAGS) ./...

clean:
	rm -rf bin

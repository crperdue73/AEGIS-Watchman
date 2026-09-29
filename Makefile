# agentids — build targets

BINARY_COLLECTOR := agentids-collector
BINARY_CENTRAL   := agentids-central
BINARY_MANIFEST  := agentids-manifest
GO              ?= go
PYTHON          ?= python3

# Build metadata, stamped into the binaries via -ldflags. Defaults are honest
# when the tree is not a git checkout (CI archives, vendored copies).
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT  ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE    ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)
PKG     := github.com/CRPerdue/agentids/internal/buildinfo
LDFLAGS := -X $(PKG).Version=$(VERSION) -X $(PKG).Commit=$(COMMIT) -X $(PKG).Date=$(DATE)

.PHONY: all build build-collector build-central build-manifest test lint check-links clean fmt vet version

all: build

build: build-collector build-central build-manifest

build-collector:
	$(GO) build -ldflags "$(LDFLAGS)" -o bin/$(BINARY_COLLECTOR) ./cmd/collector

build-central:
	$(GO) build -ldflags "$(LDFLAGS)" -o bin/$(BINARY_CENTRAL) ./cmd/central

build-manifest:
	$(GO) build -ldflags "$(LDFLAGS)" -o bin/$(BINARY_MANIFEST) ./cmd/manifest

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

check-links:
	$(PYTHON) scripts/check-links.py

lint: vet check-links
	@echo "add golangci-lint when the tree has code"

fmt:
	$(GO) fmt ./...

version:
	@echo "version: $(VERSION)  commit: $(COMMIT)  date: $(DATE)"
	@echo "ldflags: $(LDFLAGS)"

clean:
	rm -rf bin/

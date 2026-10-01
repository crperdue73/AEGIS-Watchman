# agentids — build targets

BINARY_COLLECTOR := agentids-collector
BINARY_CENTRAL   := agentids-central
BINARY_MANIFEST  := agentids-manifest
BINARY_RULES     := agentids-rules
GO              ?= go
PYTHON          ?= python3

# Build metadata, stamped into the binaries via -ldflags. Defaults are honest
# when the tree is not a git checkout (CI archives, vendored copies).
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT  ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE    ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)
PKG     := github.com/CRPerdue/agentids/internal/buildinfo
LDFLAGS := -X $(PKG).Version=$(VERSION) -X $(PKG).Commit=$(COMMIT) -X $(PKG).Date=$(DATE)

.PHONY: all build build-collector build-central build-manifest build-rules test lint check-links clean fmt vet version pin ebpf-preflight quickstart-check

all: build

build: build-collector build-central build-manifest build-rules

build-collector:
	$(GO) build -ldflags "$(LDFLAGS)" -o bin/$(BINARY_COLLECTOR) ./cmd/collector

build-central:
	$(GO) build -ldflags "$(LDFLAGS)" -o bin/$(BINARY_CENTRAL) ./cmd/central

build-manifest:
	$(GO) build -ldflags "$(LDFLAGS)" -o bin/$(BINARY_MANIFEST) ./cmd/manifest

build-rules:
	$(GO) build -ldflags "$(LDFLAGS)" -o bin/$(BINARY_RULES) ./cmd/rules

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...

check-links:
	$(PYTHON) scripts/check-links.py

lint: vet check-links
	@echo "add golangci-lint when the tree has code"

# Report whether this host can build/load the optional eBPF file.access source.
# Read-only; needs no toolchain to run. See docs/deploy-collector.md §2.
ebpf-preflight:
	./scripts/ebpf-preflight.sh

# Run the commands docs/quickstart.md tells a stranger to run, on this checkout.
# Catches a doc that stopped being true — incl. a referenced file that is present
# but untracked, which is invisible until someone clones. See scripts/quickstart-check.sh.
quickstart-check:
	./scripts/quickstart-check.sh

# Re-pin the shipped rule packs. Run after editing rules/ and commit the result;
# CI fails if rules/packs.pins drifts from the packs.
pin: build-rules
	./bin/$(BINARY_RULES) pin rules/ > rules/packs.pins
	@echo "wrote rules/packs.pins"

fmt:
	$(GO) fmt ./...

version:
	@echo "version: $(VERSION)  commit: $(COMMIT)  date: $(DATE)"
	@echo "ldflags: $(LDFLAGS)"

clean:
	rm -rf bin/

# watchman — build targets

BINARY_COLLECTOR := watchman-collector
BINARY_CENTRAL   := watchman-central
BINARY_MANIFEST  := watchman-manifest
BINARY_RULES     := watchman-rules
GO              ?= go
PYTHON          ?= python3

# Build metadata, stamped into the binaries via -ldflags. Defaults are honest
# when the tree is not a git checkout (CI archives, vendored copies).
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT  ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE    ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)
PKG     := github.com/crperdue73/AEGIS-Watchman/internal/buildinfo
LDFLAGS := -X $(PKG).Version=$(VERSION) -X $(PKG).Commit=$(COMMIT) -X $(PKG).Date=$(DATE)

.PHONY: all build build-collector build-central build-manifest build-rules test lint check-links clean fmt vet version pin pin-check ebpf-preflight quickstart-check container-check publish-scan fuzz deps-report

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

lint: vet check-links pin-check
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

# Build deploy/docker/Dockerfile and assert every claim the container docs make
# about the image (nonroot uid, baked rule pack, the shadow caveat, static
# binaries). Needs a Docker daemon. See scripts/container-check.sh.
container-check:
	./scripts/container-check.sh

# Fail if any tracked file carries a credential, an operator home path, or
# private infrastructure — the repo is public, the work was done in private.
# See scripts/publish-scan.sh.
publish-scan:
	./scripts/publish-scan.sh

# Run every Go fuzz target for a bounded time each (FUZZTIME, default 10s).
# Untrusted input — auditd lines, docker events, community rule packs — must
# never panic the detector. See scripts/fuzz.sh.
fuzz:
	./scripts/fuzz.sh

# List the modules compiled into the binaries, with their licenses, and fail on
# a copyleft or unrecognised one (a release blocker for an Apache-2.0 project).
# See scripts/deps-report.sh.
deps-report:
	./scripts/deps-report.sh

# Re-pin the shipped rule packs. Run after editing rules/ and commit the result;
# CI fails if rules/packs.pins drifts from the packs.
pin: build-rules
	./bin/$(BINARY_RULES) pin rules/ > rules/packs.pins
	@echo "wrote rules/packs.pins"

# Fail if the shipped pin no longer matches the packs (the check CI runs). Kept
# as its own target and wired into `lint` so a local "all gates green" claim
# actually covers it: the pin drifted once (a rename rewrote a byte inside a
# pack but not its hash line) precisely because CI was the only place it ran.
pin-check: build-rules
	@tmp=$$(mktemp); ./bin/$(BINARY_RULES) pin rules/ > $$tmp; \
	  if ! diff -u rules/packs.pins $$tmp; then \
	    echo "ERROR: rules/packs.pins is stale — run 'make pin' and commit the result."; \
	    rm -f $$tmp; exit 1; \
	  fi; rm -f $$tmp; echo "pin-check: ok"

fmt:
	$(GO) fmt ./...

version:
	@echo "version: $(VERSION)  commit: $(COMMIT)  date: $(DATE)"
	@echo "ldflags: $(LDFLAGS)"

clean:
	rm -rf bin/

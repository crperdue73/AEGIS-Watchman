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

.PHONY: all suitecheck gate-selfcheck pkgcheck pkgcheck-selfcheck ci-parity-selfcheck packcheck packcheck-selfcheck packcover packcover-selfcheck fpprobe fpprobe-selfcheck subjectprobe subjectprobe-selfcheck build build-collector build-central build-manifest build-rules test test-race lint check-links license-check ci-parity-check clean fmt vet version pin pin-check ebpf-preflight quickstart-check container-check publish-scan publish-audit publish-scan-check preflight clone-check fuzz deps-report cut-release-check

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

# The suite under the race detector. The central service, the collector poll
# loop, and the durable spool are concurrent; a data race there is an
# intermittent failure in a *detector*, which is worse than a loud one. CI and
# preflight both run this target, so a green local run means what a green CI run
# means. Verified to pass on this tree before it was wired in.
test-race:
	$(GO) test -race ./...

vet:
	$(GO) vet ./...

check-links:
	$(PYTHON) scripts/check-links.py

# The compile errors a missing toolchain cannot catch: duplicate top-level
# identifiers per package and self-import paths that don't resolve case-exactly
# (which build fine on a case-insensitive filesystem and fail on Linux).
# Written 2026-10-22 after shipping exactly those two defects into
# internal/rules/argv_semantics_test.go and finding them only by reading.
# It is NOT a compiler: see the header in scripts/pkgcheck.py for what it cannot
# see. Runs without Go, so it is the one build-shaped gate that works while the
# toolchain is absent.
pkgcheck:
	$(PYTHON) scripts/pkgcheck.py

# Proves pkgcheck can FAIL, on 8 synthetic trees, before anyone trusts it to pass.
# A checker nobody has watched reject anything is a sentence, not a gate.
pkgcheck-selfcheck:
	./scripts/pkgcheck_test.sh

# Fail unless LICENSE is exactly the canonical Apache-2.0 text (modulo the
# copyright line). The first public push shipped a mangled copy that GitHub
# read as NOASSERTION, so the repo did not actually publish as Apache-2.0.
# See scripts/license-check.sh.
license-check:
	./scripts/license-check.sh

lint: vet check-links pkgcheck license-check pin-check ci-parity-check
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

# Exercise scripts/cut-release.sh (the release cut) end to end in a throwaway
# clone: the clean cut lands, and every refusal fires. Run in preflight (the cut
# is the release action, so the verdict that gates a release exercises it too);
# hermetic — it clones HEAD and pushes to a temp bare remote, never this repo.
# See scripts/cut-release-check.sh.
cut-release-check:
	./scripts/cut-release-check.sh

# Assert `make preflight` still runs every step CI runs (see
# scripts/ci-parity-check.sh); wired into `lint`, so both CI and a local
# preflight run it and the promise cannot drift silently.
ci-parity-check:
	./scripts/ci-parity-check.sh

# Proves ci-parity-check can FAIL, on 4 throwaway repos — including the case that
# fooled it: a CI file whose only step is the guard itself. A guard that counts
# itself as coverage is a guard that cannot notice its own removal.
ci-parity-selfcheck:
	./scripts/ci-parity-selfcheck.sh

# Every glob in the shipped pack must be able to FIRE. Found 4 dead globs on
# 2026-10-23 (*/etc/cron* never matches /etc/cron.d/evil — path.Match '*' does
# not cross '/'). No Go toolchain needed. This does not gate `lint` yet: the
# pack still has those 4 dead globs, and a gate that always fails is furniture.
packcheck:
	$(PYTHON) scripts/packcheck.py

packcheck-selfcheck:
	./scripts/packcheck_test.sh

# packcheck asks "is every glob fired by some sample?" — too weak. A rule fires if
# ANY glob matches, so it can look covered while the globs naming its claimed
# mechanisms are dead. packcover asks "WHICH glob caught it, and is that the one
# that should have?" (2026-10-23: 6 of 13 claims misattributed or missed.)
packcover:
	$(PYTHON) scripts/packcover.py

packcover-selfcheck:
	./scripts/packcover_test.sh

# packcheck = "does every glob fire?", packcover = "does the RIGHT glob fire?",
# fpprobe = "does it fire on ORDINARY COMMANDS?" The last decides whether an
# operator keeps reading. 2026-10-23: 9/9 attacks caught, 4/21 benign lines hit.
fpprobe:
	$(PYTHON) scripts/fpprobe.py

fpprobe-selfcheck:
	./scripts/fpprobe_test.sh

# The fourth question, and the one the Go engine cannot answer about itself:
# for each rule that fires, did the match land on the COMMAND or on an ARGUMENT?
# object-only rules describe what a command is holding, not what it is doing —
# `find / -name id_rsa` is a search, `cat /root/.ssh/id_rsa` is the theft, same
# glob. Report only; rule semantics stay the pack owner's call.
subjectprobe:
	$(PYTHON) scripts/subjectprobe.py

subjectprobe-selfcheck:
	./scripts/subjectprobe_test.sh

# Run ALL 26 scripts/*_test.{py,sh} in one command and fail if any reports a failure,
# prints no verdicts, or returns non-zero. Found 2026-10-25: nothing ran them together,
# and pkgcheck_test.sh reports 8 misbehaved while exiting 0 when invoked with the wrong
# shell — so an exit status is not a verdict for these suites. Also the one test-shaped
# gate that works while the Go toolchain is absent. See scripts/suitecheck.sh.
suitecheck:
	./scripts/suitecheck.sh

# Beat 707: the harness that validates the gate, as its own target.
# suitecheck.sh EXCLUDES suitecheck_test.sh (recursion guard, by design), which means M8 -- the control
# that runs this gate once unmutated and requires the real suite/check counts -- was RED and unwatched
# from about beat 653 to beat 706 (~50 beats) while every log line read "gate green". Nothing was wrong
# with the gate; what rotted was the meta-check, because it only ran when someone remembered.
# This target does not make it scheduled -- that is a box-level change and Dad's call. It makes it
# NAMED and cheap to invoke, which is the difference between a habit and something the next person
# can find. Slow (~7 min: it copies the tree twice and runs the full gate in the copy).
gate-selfcheck:
	./scripts/suitecheck_test.sh

# Build deploy/docker/Dockerfile and assert every claim the container docs make
# about the image (nonroot uid, baked rule pack, the shadow caveat, static
# binaries). Needs a Docker daemon. See scripts/container-check.sh.
container-check:
	./scripts/container-check.sh

# Run every CI job's local-of-record, in one command, stopping at the first
# failure. The single verdict for "push-ready" — see scripts/preflight.sh.
preflight:
	./scripts/preflight.sh

# Build, test, and demo the *committed* tree from a fresh clone — what CI's
# clean checkout sees, which the working-tree preflight cannot (a file present
# but untracked is invisible there). See scripts/clone-check.sh.
clone-check:
	./scripts/clone-check.sh

# Fail if any tracked file carries a credential, an operator home path, or
# private infrastructure — the repo is public, the work was done in private.
# See scripts/publish-scan.sh.
publish-scan:
	./scripts/publish-scan.sh

# Audit the *whole* reachable history for the same classes publish-scan gates on
# (informational, always exit 0). A value committed once and later removed, or a
# file that was `git rm`-ed, stays public in history even when the tracked tree
# at HEAD is clean — the tree scan cannot see either. Not wired into preflight:
# those hits are already public, so remediating them is a human decision, not a
# build failure. See scripts/publish-scan.sh --public-audit.
publish-audit:
	./scripts/publish-scan.sh --public-audit

# Exercise publish-scan's history scope in a throwaway repo — a secret committed
# then removed must still fail the gate, and already-public content must warn,
# not fail. See scripts/publish-scan-check.sh.
publish-scan-check:
	./scripts/publish-scan-check.sh

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

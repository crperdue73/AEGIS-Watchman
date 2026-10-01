#!/bin/sh
# Run every CI job's local-of-record, in order, stopping at the first failure.
#
# CI is the promise this repo makes to a stranger (.github/workflows/ci.yml).
# This runs the *same targets*, on this host, in one command — so "push-ready"
# is a single verdict instead of eight separate claims made from memory. It
# exists because the pin drifted into a red first CI run precisely while every
# local claim was individually true.
#
# Needs Docker (the container job) and module downloads (the deps job); that is
# the same requirement CI has. Fuzz time is bounded by FUZZTIME (default 5s).
set -eu

cd "$(dirname "$0")/.."

# The dev host documents Go at /usr/local/go/bin off PATH (see TOOLS.md); be
# tolerant so the gate works where a stranger's environment already has it.
if ! command -v go >/dev/null 2>&1 && [ -x /usr/local/go/bin/go ]; then
    PATH="$PATH:/usr/local/go/bin"
    export PATH
fi

say() { printf '\n== %s\n' "$*"; }

say "build"
make build >/dev/null

say "lint (vet + check-links + pin-check)"
make lint

say "vet (sqlite build)"
go vet -tags sqlite ./...

say "test (default)"
go test ./...

say "test (sqlite)"
go test -tags sqlite ./...

say "demo (the definition-of-done walkthrough)"
./demo/run.sh >/dev/null

say "quickstart"
make quickstart-check

say "container"
make container-check

say "publish scan"
make publish-scan

say "deps"
make deps-report

say "fuzz (FUZZTIME=${FUZZTIME:-5s})"
FUZZTIME="${FUZZTIME:-5s}" make fuzz

printf '\npreflight: PASS — every CI job target is green on this host\n'

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
# The last step is `clone-check`: CI runs on a clean checkout, so this gate
# verifies the committed tree from a fresh clone too — see scripts/clone-check.sh
# for why the working tree alone cannot prove that.
#
# Beyond the CI jobs it mirrors, it also runs the release-tooling self-check
# (`make cut-release-check`). The cut (`scripts/cut-release.sh`) is the release
# action itself; preflight is the release verdict. Running the cut's guard here
# is what keeps it from being an orphan — a documented script that nothing
# invokes cannot catch a broken refusal until a real cut, on the public repo,
# at the worst moment (the same class `publish-scan-check` exists to close).
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

say "lint (vet + check-links + pkgcheck + license + pin-check)"
make lint

# Proves the pkgcheck alarm can still fire. Not part of `lint` on purpose: it
# builds 8 throwaway modules, and a lint pass should stay a lint pass. CI runs
# it in the lint job, so preflight runs it here — ci-parity-check enforces that.
say "pkgcheck self-check (the alarm can still fail)"
make pkgcheck-selfcheck

# The guard that CI covers what preflight covers. It runs LAST on purpose: it
# should read the finished preflight, and it must be reachable by name or it
# fails itself — the guard is not allowed to count itself as coverage.
# packcheck/packcover/fpprobe are DELIBERATELY not gates: packcheck reports 4 dead
# globs, packcover 6 misattributed claims, fpprobe 4 unexpected fires on ordinary
# commands. Their instruments are red against the pack as it stands, and a gate that
# always fails is furniture. But their SELF-CHECKS are green and always should be —
# they prove the alarm can still fire. So run only the self-checks here: the fire
# alarm stays untriaged, and the proof that the alarm works stops being a thing
# nobody has run since the afternoon it was written. Found 2026-10-24 by
# orphanprobe.py: all four targets existed, all four passed, none was chained.
say "rule-pack alarm self-checks (packcheck/packcover/fpprobe/subjectprobe)"
make packcheck-selfcheck
make packcover-selfcheck
make fpprobe-selfcheck
make subjectprobe-selfcheck

say "ci parity (every CI step is reachable from here)"
make ci-parity-check

say "ci parity self-check (the parity guard can still fail)"
make ci-parity-selfcheck

# The script self-checks: 26 suites, 470 checks, no Go and no make needed. Runs here
# because `go test ./...` above is the only other place most of them could be reached,
# and it cannot reach them at all. Found 2026-10-25: nothing in the tree ran them
# together, and one of them (pkgcheck_test.sh) prints 8 failures and exits 0 when
# invoked by the wrong shell. suitecheck reads the verdict, not just the status.
say "script self-checks (every scripts/*_test, 26 suites)"
./scripts/suitecheck.sh

say "vet (sqlite build)"
go vet -tags sqlite ./...

say "test (default)"
go test ./...

say "test (sqlite)"
go test -tags sqlite ./...

say "test (race)"
make test-race

say "demo (the definition-of-done walkthrough)"
./demo/run.sh >/dev/null

say "quickstart"
make quickstart-check

say "container"
make container-check

say "publish scan"
make publish-scan

say "publish scan (history scope)"
make publish-scan-check

say "release cut (the cut script, end to end)"
make cut-release-check

say "deps"
# CI's deps job runs this before `make deps-report`; run it here too, so the
# local step is the same step — and the module cache the license read needs is
# populated rather than assumed.
go mod download
make deps-report

say "fuzz (FUZZTIME=${FUZZTIME:-5s})"
FUZZTIME="${FUZZTIME:-5s}" make fuzz

say "clone (the artifact, from a fresh checkout)"
./scripts/clone-check.sh

printf '\npreflight: PASS — every CI job target is green on this host\n'

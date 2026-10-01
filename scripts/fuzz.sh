#!/usr/bin/env bash
# fuzz — run every Go fuzz target in the tree for a bounded time each.
#
# Why this exists. Every stream this tool reads is attacker-influenced: auditd
# lines carry argv an attacker chose, `docker events` is JSON off a socket, and a
# rule pack can be written by someone else (the BRIEF's Phase 3 is a Snort-style
# community pack model). A parser or matcher that panics on crafted input is a
# denial of service against the *detector* — the cheapest way to turn detection
# off is to crash the collector, and the operator's first sign would be a
# restart, not an alert. Fuzzing asserts the only contract these must have on
# hostile input: return, with a value or an error, never panic.
#
# Targets are discovered, not listed, so a new `func FuzzX` is covered the moment
# it is written. A crash writes its reproducer to testdata/fuzz/, which then runs
# under an ordinary `go test` — so `make test` re-checks every crasher found.
#
# Usage: scripts/fuzz.sh            (FUZZTIME per target, default 10s)
#        FUZZTIME=30s scripts/fuzz.sh
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

fuzztime="${FUZZTIME:-10s}"
total=0 failed=0

for pkg in $(go list ./...); do
  targets="$(go test "$pkg" -list '^Fuzz' 2>/dev/null | grep '^Fuzz' || true)"
  [[ -z "$targets" ]] && continue
  for t in $targets; do
    total=$((total + 1))
    printf '== %s.%s (%s) ==\n' "${pkg##*/}" "$t" "$fuzztime"
    if ! go test "$pkg" -run='^$' -fuzz="^${t}\$" -fuzztime="$fuzztime"; then
      failed=$((failed + 1))
    fi
  done
done

echo
if [[ "$failed" -gt 0 ]]; then
  echo "fuzz: $failed of $total target(s) FAILED — a reproducer is in testdata/fuzz/"
  exit 1
fi
echo "fuzz: all $total target(s) survived $fuzztime — no panic on hostile input"

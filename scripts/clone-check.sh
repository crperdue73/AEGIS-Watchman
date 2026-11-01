#!/usr/bin/env bash
# clone-check — build, test, and demo the *committed tree* from a fresh clone.
#
# Why this exists. Every CI job starts from `actions/checkout`: a clean tree of
# tracked files only. `make preflight` runs in the working tree, where a file
# that is present but *untracked* looks identical to a tracked one — so the
# local gate could not see the defect that bit once already (heartbeat 76: the
# quickstart died on a fresh clone because `examples/*.local.yaml` and the demo's
# auditd fixture were present locally but never committed; CI caught it, local
# preflight could not). This step closes the gap: clone HEAD to a temp dir and
# build, test, and demo there — the artifact a stranger actually gets.
#
# The clone excludes the local-only process files (WORKLOG.md, BRIEF.md, held
# out via .git/info/exclude); their absence is asserted, because a leak here is
# a leak into the public repo.
#
# Shares the Go module and build caches, so no network is needed on a warm host.
# Usage: scripts/clone-check.sh        (exit 0 = the committed tree works)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

# The dev host documents Go at /usr/local/go/bin off PATH (see TOOLS.md).
if ! command -v go >/dev/null 2>&1 && [ -x /usr/local/go/bin/go ]; then
    PATH="$PATH:/usr/local/go/bin"
    export PATH
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== clone HEAD =="
git clone -q "$repo" "$tmp/check"
cd "$tmp/check"

# Beat 631: this said "clean: local-only docs stayed out" over an unstated list. A reader could not
# tell whether two files were checked or twenty -- a clean result wider than its evidence. The list is
# named and counted so the claim has a reach.
local_only="WORKLOG.md BRIEF.md"
n_local=0
for f in $local_only; do
    if [ -e "$f" ]; then
        echo "clone-check: FAIL: $f leaked into the clone (it must stay out of the repo)" >&2
        exit 1
    fi
    n_local=$((n_local + 1))
done
echo "   clean: all $n_local local-only doc(s) stayed out ($local_only)"

echo "== build =="
make build >/dev/null
for b in watchman-collector watchman-central watchman-manifest watchman-rules; do
    [ -x "bin/$b" ] || { echo "clone-check: FAIL: make build did not produce bin/$b" >&2; exit 1; }
done

echo "== test =="
go test ./... >/dev/null

echo "== demo =="
( cd demo && ./run.sh >/dev/null )

echo
echo "clone-check: PASS — the committed tree builds, tests, and demos from a fresh clone"

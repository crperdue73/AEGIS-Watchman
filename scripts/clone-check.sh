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
# The clone excludes the local-only process file (WORKLOG.md, held out via
# .git/info/exclude); its absence is asserted, because a leak here is a leak into
# the public repo.
#
# BEAT 836: `BRIEF.md` was in this list and it was WRONG. Beat 740 measured that
# BRIEF.md is tracked at HEAD and is clean product material (the public Carbonato
# brief + build spec) — the assertion was corrected then, in the publish docs and
# in preflight, but not here, so this step has been red on a correct repo ever
# since. A guard that argues with the shipped tree is not a guard; it is a stale
# opinion with an exit code.
#
# Shares the Go module and build caches, so no network is needed on a warm host.
# Usage: scripts/clone-check.sh        (exit 0 = the committed tree works)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

# BEAT 836: this check clones HEAD, so it needs a real repository. The authored
# tree lost its .git in the 2026-10-08 migration, and the check died with
# `fatal: repository '...' does not exist` -- a red that described the host, not
# the product. Honor an explicit source, then the durable clone beside the
# authored tree, then this tree; and if none of them is a repo, say so in one
# line instead of letting `git clone` hallucinate an indictment.
if [[ -n "${CLONE_CHECK_SRC:-}" ]]; then
    repo="$(cd "$CLONE_CHECK_SRC" && pwd)"
elif ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    if git -C "$here/../../watchman-git" rev-parse --git-dir >/dev/null 2>&1; then
        repo="$(cd "$here/../../watchman-git" && pwd)"
    fi
fi
git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 \
    || { echo "clone-check: SKIPPED -- no git repository at $repo (set CLONE_CHECK_SRC to one)"; exit 0; }

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
local_only="WORKLOG.md"
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

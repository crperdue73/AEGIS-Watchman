#!/usr/bin/env bash
# cut-release-check — exercise scripts/cut-release.sh end to end, in a throwaway
# clone, so the release rules it enforces are themselves checked.
#
# hb116 shipped the cut script because the release rules lived only in prose.
# That makes the script load-bearing on the release path — and it had no test. A
# bug in a refusal would show up at a real cut, at the worst moment, on the
# public repo. So the guard gets a guard, the same way the quickstart does
# (scripts/quickstart-check.sh): this runs the actual commands and fails if any
# of them stops behaving.
#
# It never touches this repository: everything happens in a clone of HEAD under
# a temp dir, with a throwaway bare remote. The clone's tags and remotes are its
# own.
#
# Usage: scripts/cut-release-check.sh      (exit 0 = the cut script behaves)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

fail() { echo "cut-release-check: FAIL: $*" >&2; exit 1; }

# The check clones this repo and pushes a branch to a scratch remote. Git refuses
# to push a *shallow* branch ("shallow update not allowed"), so a shallow checkout
# cannot run this check at all. Say so plainly instead of failing with the cryptic
# push error — and note that CI checks out with fetch-depth: 0 for this reason
# (see .github/workflows/ci.yml).
if [ "$(git -C "$repo" rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
    fail "this is a shallow clone; the check needs real history — run 'git fetch --unshallow'"
fi

echo "== clone HEAD to a scratch dir =="
git clone -q "$repo" "$tmp/work"
cd "$tmp/work"
git remote remove origin
git init -q --bare "$tmp/scratch.git"
git remote add scratch "$tmp/scratch.git"
git push -q scratch main

run() { ./scripts/cut-release.sh "$@"; }
expect_refuse() { # expect_refuse <expected-substring> <args...>
    local want="$1"; shift
    local out
    if out="$(run "$@" 2>&1)"; then
        fail "expected a refusal ($want), but it exited 0: $*"
    fi
    grep -qF "$want" <<<"$out" || fail "refused for the wrong reason (wanted '$want'): $out"
}

echo "== dry run: prints the plan, changes nothing =="
out="$(run 0.9.9 scratch)"
grep -qF "DRY RUN" <<<"$out" || fail "dry run did not report DRY RUN"
grep -qF "git push scratch main" <<<"$out" || fail "dry run plan is missing the push step"
git rev-parse -q --verify refs/tags/0.9.9 >/dev/null && fail "dry run created a tag"

echo "== refusal: not a bare release number =="
expect_refuse "not a bare release number" v0.9.9 scratch

echo "== refusal: version already exists locally =="
git tag 6.6.6
expect_refuse "already exists locally" --go --no-preflight 6.6.6 scratch
git tag -d 6.6.6 >/dev/null

echo "== refusal: version already exists on the remote =="
git push -q scratch HEAD:refs/tags/7.7.7
expect_refuse "already exists on" --go --no-preflight 7.7.7 scratch

echo "== refusal: dirty tracked tree =="
echo "# local edit that must not ship" >>README.md
expect_refuse "dirty" --go --no-preflight 5.5.5 scratch
git checkout -q -- README.md

echo "== refusal: not on main =="
git checkout -q -b not-main
expect_refuse "not 'main'" --go --no-preflight 4.4.4 scratch
git checkout -q main

echo "== a clean cut: main and the tag land, the tag peels to the pushed tip =="
# Delete the remote branch first so the cut really pushes it (not a no-op).
git push -q scratch :refs/heads/main
run --go --no-preflight 3.3.3 scratch >/dev/null || fail "a clean cut exited non-zero"
tip="$(git rev-parse HEAD)"
[ "$(git ls-remote scratch refs/heads/main | cut -f1)" = "$tip" ] || fail "pushed main is not HEAD"
[ "$(git rev-parse '3.3.3^{commit}')" = "$tip" ] || fail "the tag does not peel to the pushed tip"
[ -n "$(git ls-remote --tags scratch refs/tags/3.3.3)" ] || fail "the tag was not pushed to the remote"

echo
echo "cut-release-check: PASS — the cut script performs the cut and refuses every known mistake"

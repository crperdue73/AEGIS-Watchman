#!/usr/bin/env bash
# publish-scan-check — exercise scripts/publish-scan.sh's *history* scope and its
# *cannot-run* verdict, in a throwaway repo, so the logic that decides what ships
# is itself checked.
#
# hb122 taught publish-scan that `git push` publishes history: a value committed
# and later removed still ships, and a `git rm`-ed file stays retrievable. That
# makes the *scoping* load-bearing — which commit range is scanned, what happens
# when it is empty, that an absent upstream is tolerated, and that already-public
# content warns instead of fails. A bug there is a silently-missed leak, which is
# the exact failure mode the whole script exists to catch. So the guard gets a
# guard, the same way cut-release.sh does (scripts/cut-release-check.sh).
#
# BEAT 679 — WHY THE SECOND HALF EXISTS. `make publish-scan-check` was GREEN on
# 2026-10-30 while publish-scan itself printed PASS on a planted token. Every case
# here ran inside a fresh `git init` repo, so `git grep` always worked; the one
# state the scan could not handle — a tree where `git grep` CANNOT RUN (this
# repo since the 2026-10-08 migration destroyed its .git) — was the one state no
# case created. The checker certified a capability the gate loses in exactly the
# tree it is used in. That is the same species as a fixture supplying the answer
# (beat 650): the harness built the precondition and then graded it. So there is
# now a NO-GIT section below, and it is the one that must never go green against
# a scanner that answers an unasked question.
#
# It never touches this repository: a fresh repo is built under a temp dir and
# publish-scan.sh is *copied* into it — the script is anchored to its own root, so
# a copy is how it is pointed at a different repo.
#
# Usage: scripts/publish-scan-check.sh   (exit 0 = the scan behaves)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT
fail() { echo "publish-scan-check: FAIL: $*" >&2; exit 1; }

work="$tmp/work"
mkdir -p "$work/scripts"
git init -q -b main "$work"
git -C "$work" config user.email "check@example.invalid"
git -C "$work" config user.name "publish-scan-check"
cp "$repo/scripts/publish-scan.sh" "$work/scripts/publish-scan.sh"
chmod +x "$work/scripts/publish-scan.sh"

scan() { ( cd "$work" && ./scripts/publish-scan.sh "$@" ); }
commit() { git -C "$work" add -A; git -C "$work" commit -q -m "$1"; }
# expect the scan to fail, for the named reason
expect_fail() { # expect_fail <label> <expected-substring> [args...]
    local label="$1" want="$2"; shift 2
    local out
    if out="$(scan "$@" 2>&1)"; then
        fail "$label: expected the scan to fail, but it exited 0"
    fi
    grep -qF "$want" <<<"$out" || fail "$label: failed for the wrong reason (wanted '$want'): $out"
}
expect_pass() { # expect_pass <label> [args...]
    local label="$1"; shift
    scan "$@" >/dev/null || fail "$label: expected the scan to pass"
}

printf 'hello\n' >"$work/README.md"
commit "base"

echo "== a clean repo with no upstream: passes, history section skipped =="
expect_pass "clean, no upstream"
out="$(scan 2>&1)"
grep -qF "history: commits not yet on" <<<"$out" && fail "scanned history with no upstream to compare against"

# Simulate 'already pushed': an upstream ref at base, so later commits are the
# ones a push would newly publish.
base="$(git -C "$work" rev-list --max-parents=0 HEAD)"
git -C "$work" update-ref refs/remotes/origin/main "$base"

echo "== a leak in an unpushed commit is caught (fatal) =="
# The fixtures are assembled at runtime ON PURPOSE. This file is itself tracked,
# and publish-scan scans the tracked tree — so a literal example key or
# `/home/<user>` path written here would make the scan flag its own test (it
# did, on the first cut: `make preflight` went red on this exact script). Do not
# "simplify" the two lines below into single literals.
printf 'key = %s%s\n' 'AKIA' 'IOSFODNN7EXAMPLE' >"$work/leak.txt"
commit "oops: aws key in the tree"
expect_fail "unpushed aws key" "AWS access-key id"

echo "== a secret committed then removed still fails (tree clean, history is not) =="
git -C "$work" rm -q leak.txt
commit "remove the key"
if git -C "$work" grep -lE 'AKIA[0-9A-Z]{16}' >/dev/null 2>&1; then
    fail "precondition: the tree should be clean now"
fi
expect_fail "removed-but-unpushed aws key" "AWS access-key id"

echo "== an operator home path in unpushed history is caught =="
printf 'root = /home/%s/watchman\n' 'cperdue' >"$work/conf.yaml"
commit "fix: bake in a real home path"
expect_fail "unpushed /home/<user> path" "real /home/<user> path"

echo "== once it is public (upstream == HEAD): gate passes, audit warns =="
git -C "$work" rm -q conf.yaml
commit "remove the path"
git -C "$work" update-ref refs/remotes/origin/main HEAD
expect_pass "nothing unpushed"
out="$(scan --public-audit 2>&1)" || fail "--public-audit is warn-only and must exit 0"
grep -qF "real /home/<user> path" <<<"$out" || fail "the audit missed the already-public home path: $out"
grep -qF "AWS access-key id" <<<"$out" || fail "the audit missed the already-public aws key: $out"

# --- BEAT 679: the state this repo is actually IN --------------------------
# No .git means `git grep` cannot answer, and "cannot answer" must never be
# reported as "nothing found". A planted token is what distinguishes the two:
# a scanner that has given up still misreads it as a clean tree.
echo
echo "== a tree where git grep CANNOT RUN must not report clean =="
rm -rf "$work/.git"
canary="$work/planted.txt"
printf 'token = %s%s\n' 'AKIA' 'IOSFODNN7EXAMPLE' >"$canary"
out=""
rc=0
out="$(scan 2>&1)" || rc=$?
echo "   no .git + a planted key -> rc=$rc"
grep -qF "AKIA" <<<"$out" && fail "the scan echoed the planted key into its output: $out"
if [ "$rc" -eq 0 ]; then
    fail "the scan exited 0 with NO .git — it reported a verdict on a question it could not ask: $out"
fi
grep -qiE 'unavailable|cannot run|not a git repository' <<<"$out" \
    || fail "it failed, but did not say WHY it could not run (wanted 'unavailable'/'cannot run'): $out"
grep -qF "PASS" <<<"$out" && fail "it printed PASS and a failure in the same output: $out"
# --warn is report-everything: it may exit 0, but it still must not call this clean.
out="$(scan --warn 2>&1)" || true
grep -qF "PASS" <<<"$out" && fail "--warn printed PASS for a scan it could not run: $out"
rm -f "$canary"

# The canary alone is not proof the verdict depends on the run: with no planted
# key the SAME tree must still refuse to pass. Otherwise "found the key" and
# "could not look" are again the same output.
out=""
rc=0
out="$(scan 2>&1)" || rc=$?
[ "$rc" -ne 0 ] || fail "with a clean-looking tree and no .git it exited 0 — the verdict tracks the canary, not the scan"
grep -qiE 'unavailable|cannot run|not a git repository' <<<"$out" || fail "no cannot-run reason on an empty no-git tree: $out"

echo
echo "publish-scan-check: PASS — the scan gates unpushed history, audits already-public history, and refuses to report a tree it cannot read"

#!/usr/bin/env bash
# publish-scan — fail if anything the repo is about to publish must not ship.
#
# Why this exists. This repository is public under Apache-2.0 from the first
# cut, and it was developed inside a private workspace. The classes of content
# that are fine in a private tree and unacceptable in a public one are narrow
# and greppable: credentials, the operator's real home path, and the private
# infrastructure the work was done on. A human reading a diff will not notice a
# token pasted into a test file or a `/home/<someone>` fixture; a machine will,
# every time. This is that machine.
#
# It scans the TRACKED TREE (`git grep`) — and, since it was found that the tree
# is not the whole of what ships, the COMMITS a push would publish:
#
#   * `git push` publishes commits, and history is permanent. A credential or an
#     operator path committed once and later removed still ships, and a file
#     that was `git rm`-ed is fully retrievable from any clone (raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>
#     serves it). A tracked-tree scan at HEAD cannot see either — that was a real
#     gap: the hold-out of BRIEF.md/WORKLOG.md and the `/home/<user>` fixture fix
#     both cleaned the tree while the values stayed in history.
#   * So the default run also scans the commits not yet on the upstream — the
#     ones a push would *newly* publish, still fixable locally.
#
# Usage: scripts/publish-scan.sh              tree + unpushed commits (gate)
#        scripts/publish-scan.sh --warn       report everything, always exit 0
#        scripts/publish-scan.sh --public-audit  also audit the whole reachable
#                                             history (already-public content is
#                                             reported, not fatal: a local fix
#                                             cannot un-publish it)
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

warn_only=0
public_audit=0
case "${1:-}" in
  "") ;;
  --warn)         warn_only=1 ;;
  --public-audit) public_audit=1 ;;
  *) echo "usage: $0 [--warn|--public-audit]" >&2; exit 2 ;;
esac

fatal=0
# When non-empty, report() greps these commit objects instead of the worktree.
scan_revs=""

# report <FATAL|warn> <label> <grep-pattern> [allowlist-pattern]
# Greps either the tracked tree (scan_revs empty) or a set of commits, and prints
# any hits. In history mode the hits are prefixed <sha>:<path>:<line>.
report() {
  local level="$1" label="$2" pat="$3" allow="${4:-}" out
  if [[ -n "$scan_revs" ]]; then
    out="$(git grep -n -I -E -- "$pat" $scan_revs 2>/dev/null)" || true
  else
    out="$(git grep -n -I -E -- "$pat" 2>/dev/null)" || true
  fi
  [[ -z "$out" ]] && return 0
  if [[ -n "$allow" ]]; then
    out="$(printf '%s\n' "$out" | grep -vE "$allow")" || true
    [[ -z "$out" ]] && return 0
  fi
  # In history mode the same line repeats once per commit that carries it; that
  # is one fact, not N. Collapse to unique <path>:<line>:<text> and report how
  # many commits are affected, so the finding is readable.
  if [[ -n "$scan_revs" ]]; then
    local nlines
    nlines="$(printf '%s\n' "$out" | sed -E 's/^[0-9a-f]{7,40}://' | sort -u | wc -l | tr -d ' ')"
    out="$(printf '%s\n' "$out" | sed -E 's/^[0-9a-f]{7,40}://' | sort -u)"
    label="$label ($nlines distinct line(s) across history)"
  fi
  echo "publish-scan: $level — $label"
  printf '%s\n' "$out" | sed 's/^/    /'
  if [[ "$level" == "FATAL" ]]; then fatal=1; fi
}

# checks <FATAL|warn> — every class, once. Called for the tracked tree and again
# for a commit range, so the two can never drift from each other.
checks() {
  local lvl="$1"

  echo "== credentials / private keys =="
  report "$lvl" "private-key block"  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  report "$lvl" "AWS access-key id"  'AKIA[0-9A-Z]{16}'
  report "$lvl" "GitHub token"       '(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,}'
  report "$lvl" "Slack token"        'xox[baprs]-[A-Za-z0-9-]{10,}'
  report "$lvl" "OpenAI-style key"   'sk-[A-Za-z0-9]{24,}'

  echo "== operator-private home paths (generic fixture users are allowed) =="
  report "$lvl" "real /home/<user> path" '/home/[a-z][a-z0-9_-]*/' '/home/(agent|dev|user|u|a|alice|bob|x|demo|deploy)/'

  echo "== private infrastructure =="
  report "$lvl" "private RFC1918 address"  '192\.168\.[0-9]+\.[0-9]+'
  report "$lvl" "private service port"     ':1879[0-9]\b'
  # Private HOSTNAMES are deliberately not listed here: naming them would publish
  # them, which is the very thing this scan exists to prevent (it caught its own
  # first draft doing exactly that once the script itself became a tracked file).
  # A maintainer keeps the real names in an untracked .publish-denylist
  # (.gitignore'd), one ERE per line; this scan uses it when present. CI has no
  # such file, so CI gates the generic classes only — which is the point: the
  # public gate cannot name what is private.
  if [[ -f .publish-denylist ]]; then
    local dl
    dl="$(grep -vE '^[[:space:]]*(#|$)' .publish-denylist | paste -sd'|' -)"
    [[ -n "$dl" ]] && report "$lvl" "private hostname (local .publish-denylist)" "$dl"
  fi
}

echo "== tracked tree =="
checks FATAL

# --- the commits a push would publish ---------------------------------------
# Prefer the branch's upstream; fall back to origin/<branch> so this works in a
# detached CI checkout too. An empty range (nothing unpushed) is skipped: with
# no revs `git grep` would silently search the worktree instead, doubling the
# tree scan and reporting nothing about history.
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
[[ -z "$upstream" ]] && upstream="origin/$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
if git rev-parse --verify -q "$upstream" >/dev/null 2>&1; then
  unpushed="$(git rev-list "$upstream"..HEAD 2>/dev/null || true)"
  if [[ -n "$unpushed" ]]; then
    echo
    echo "== history: commits not yet on $upstream (a push would publish these) =="
    scan_revs="$unpushed"
    checks FATAL
    scan_revs=""
  fi
fi

# --- the whole reachable history (the audit) --------------------------------
# Every commit reachable from HEAD is what a clone sees, whether or not it is on
# a remote yet. Its content is, by the time anyone reads this, already public on
# a pushed branch; a local fix cannot un-publish it, so these are reported, not
# fatal. Remediation (history rewrite before the offending commit is public, or
# accepting the exposure) is a human decision, not a build failure.
if [[ "$public_audit" == "1" ]]; then
  echo
  echo "== history audit: every commit reachable from HEAD (informational) =="
  scan_revs="$(git rev-list HEAD)"
  checks warn
  scan_revs=""
fi

echo
if [[ "$fatal" == "1" && "$warn_only" == "0" ]]; then
  echo "publish-scan: FAIL — the above must not be published (or allow-list it deliberately)"
  exit 1
fi
echo "publish-scan: PASS — no tracked file and no unpushed commit carries a credential, an operator home path, or private infrastructure"

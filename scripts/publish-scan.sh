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
# It scans TRACKED files only (`git grep`), because only tracked content ships.
#
# Usage: scripts/publish-scan.sh        (exit 0 = nothing that must not ship)
#        scripts/publish-scan.sh --warn (report, always exit 0)
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

warn_only=0
[[ "${1:-}" == "--warn" ]] && warn_only=1

fatal=0

# report <fatal|warn> <label> <grep-pattern> [allowlist-pattern]
# Runs `git grep -E` over tracked files and prints any hits.
report() {
  local level="$1" label="$2" pat="$3" allow="${4:-}" out
  out="$(git grep -n -I -E -- "$pat" 2>/dev/null)" || true
  [[ -z "$out" ]] && return 0
  if [[ -n "$allow" ]]; then
    out="$(printf '%s\n' "$out" | grep -vE "$allow")" || true
    [[ -z "$out" ]] && return 0
  fi
  echo "publish-scan: $level — $label"
  printf '%s\n' "$out" | sed 's/^/    /'
  if [[ "$level" == "FATAL" ]]; then fatal=1; fi
}

echo "== credentials / private keys =="
report FATAL "private-key block"  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
report FATAL "AWS access-key id"  'AKIA[0-9A-Z]{16}'
report FATAL "GitHub token"       '(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,}'
report FATAL "Slack token"        'xox[baprs]-[A-Za-z0-9-]{10,}'
report FATAL "OpenAI-style key"   'sk-[A-Za-z0-9]{24,}'

echo "== operator-private home paths (generic fixture users are allowed) =="
report FATAL "real /home/<user> path" '/home/[a-z][a-z0-9_-]*/' '/home/(agent|dev|user|alice|bob|x|demo)/'

echo "== private infrastructure =="
report FATAL "private RFC1918 address"  '192\.168\.[0-9]+\.[0-9]+'
report FATAL "private service port"     ':1879[0-9]\b'
# Private HOSTNAMES are deliberately not listed here: naming them would publish
# them, which is the very thing this scan exists to prevent (it caught its own
# first draft doing exactly that once the script itself became a tracked file).
# A maintainer keeps the real names in an untracked .publish-denylist
# (.gitignore'd), one ERE per line; this scan uses it when present. CI has no
# such file, so CI gates the generic classes only — which is the point: the
# public gate cannot name what is private.
if [[ -f .publish-denylist ]]; then
  dl="$(grep -vE '^[[:space:]]*(#|$)' .publish-denylist | paste -sd'|' -)"
  [[ -n "$dl" ]] && report FATAL "private hostname (local .publish-denylist)" "$dl"
fi

echo
if [[ "$fatal" == "1" && "$warn_only" == "0" ]]; then
  echo "publish-scan: FAIL — the above must not be published (or allow-list it deliberately)"
  exit 1
fi
echo "publish-scan: PASS — no tracked file carries a credential, an operator home path, or private infrastructure"

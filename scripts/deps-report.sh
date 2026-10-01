#!/usr/bin/env bash
# deps-report — list every module this project builds against, with its version
# and license, and FAIL on a license a permissive, Apache-2.0-licensed project
# cannot ship.
#
# Why this exists. This is a supply-chain security tool; shipping it without an
# auditable dependency inventory would be a poor advertisement for its own
# thesis. The inventory is small on purpose (one direct dependency), and the
# check is cheap, so it should be automatic: a copyleft dependency (GPL/AGPL/
# LGPL/SSPL/BUSL/EUPL) or a module with no discoverable license is a release
# blocker, and it is the kind of thing that only ever arrives via a transitive
# upgrade. The licenses are read from the module cache, so this needs no network
# once `go mod download` has run.
#
# Usage: scripts/deps-report.sh        (exit 0 = every dependency is permissive)
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

command -v go >/dev/null 2>&1 || { echo "deps-report: go not found on PATH"; exit 2; }

cache="$(go env GOMODCACHE)"
printf '%-46s %-14s %s\n' "MODULE" "VERSION" "LICENSE"
printf '%-46s %-14s %s\n' "----------------------------------------------" "--------------" "-------------------------"

bad=0
# Modules that actually compile into the binaries, in both build modes (default
# and -tags sqlite). The module GRAPH (`go list -m all`) is wider: it includes
# test-only transitive requirements that are never built or distributed, and
# whose sources may not even be in the cache. What we ship is what we build.
compiled() {
  go list -deps -f '{{with .Module}}{{.Path}}{{"\t"}}{{.Version}}{{"\t"}}{{.Dir}}{{end}}' ./cmd/... 2>/dev/null
  go list -deps -tags sqlite -f '{{with .Module}}{{.Path}}{{"\t"}}{{.Version}}{{"\t"}}{{.Dir}}{{end}}' ./cmd/... 2>/dev/null
}
while IFS=$'\t' read -r mod ver dir; do
  [[ -z "$mod" || "$mod" == github.com/CRPerdue/* ]] && continue
  lic=""
  for f in "$dir"/LICENSE "$dir"/LICENSE.txt "$dir"/LICENSE.md "$dir"/COPYING "$dir"/COPYING.txt "$dir"/LICENSE-APACHE "$dir"/LICENSE-MIT; do
    [[ -f "$f" ]] || continue
    lic="$f"; break
  done
  if [[ -z "$lic" ]]; then
    printf '%-46s %-14s %s\n' "$mod" "$ver" "!! NO LICENSE FILE FOUND"
    bad=1; continue
  fi
  text="$(cat "$lic" 2>/dev/null)"
  name=""
  case "$text" in
    *"Apache License"*"Version 2.0"*) name="Apache-2.0" ;;
    *"GNU AFFERO GENERAL PUBLIC LICENSE"*) name="AGPL-3.0" ;;
    *"GNU LESSER GENERAL PUBLIC LICENSE"*) name="LGPL" ;;
    *"GNU GENERAL PUBLIC LICENSE"*) name="GPL" ;;
    *"Mozilla Public License Version 2.0"*) name="MPL-2.0" ;;
    *"Server Side Public License"*) name="SSPL" ;;
    *"Business Source License"*) name="BUSL" ;;
    *"European Union Public Licence"*) name="EUPL" ;;
    *"Permission is hereby granted, free of charge"*) name="MIT" ;;
    *"Permission to use, copy, modify, and/or distribute this software for any"*) name="ISC" ;;
  esac
  if [[ -z "$name" && "$text" == *"Redistribution and use in source and binary forms"* ]]; then
    if [[ "$text" == *"Neither the name"* ]]; then name="BSD-3-Clause"; else name="BSD-2-Clause"; fi
  fi
  if [[ -z "$name" ]]; then
    printf '%-46s %-14s %s\n' "$mod" "$ver" "!! UNRECOGNISED ($(basename "$lic"))"
    bad=1; continue
  fi
  case "$name" in
    GPL*|AGPL*|LGPL*|SSPL|BUSL|EUPL)
      printf '%-46s %-14s %s\n' "$mod" "$ver" "!! COPYLEFT: $name"
      bad=1 ;;
    *)
      printf '%-46s %-14s %s\n' "$mod" "$ver" "$name" ;;
  esac
done < <(compiled | sort -u)

echo
if [[ "$bad" == "1" ]]; then
  echo "deps-report: FAIL — a dependency is copyleft or has no recognisable license (module cache: $cache)"
  exit 1
fi
echo "deps-report: OK — every dependency is permissively licensed"

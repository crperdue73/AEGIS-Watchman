#!/bin/sh
# packcheck_test — prove scripts/packcheck.py can still FAIL.
#
# WHY. packcheck reports "N dead globs". A tool that always reports 0 proves
# nothing, and a tool that always reports >0 is furniture too. Both directions
# get tested here, on synthetic packs.
#
#   A. a good glob with a matching sample -> must PASS (0)
#   B. a glob no sample can fire          -> must FAIL (1), naming it
#   C. a glob that fires ONLY via the contains route -> must PASS (0).
#      REWRITTEN 2026-10-27 (beat 616). This case used to assert the OPPOSITE: it
#      demanded `*/etc/cron*` FAIL on the sample "/etc/cron", because the mirror
#      had a "no interior '/'" refusal that engine.go:281 substringNeedle does not
#      have. The test was pinning the port's bug as the specification. `*/etc/cron*`
#      really does catch /etc/cron.d/x via needle "/etc/cron", so "0 dead" is correct.
#      The genuinely-dead case is B, which is a needle no path contains.
#   D. an unreadable/empty pack            -> must exit 2, never 0
#
# Exit 0 if all four behaved.

set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pass=0; fail=0

mkdir -p "$work/rules" "$work/scripts"
cp "$HERE/packcheck.py" "$work/scripts/"

# mk PACKTEXT -> writes rules/carbonato.yaml
mk() { printf '%s\n' "$1" > "$work/rules/carbonato.yaml"; }

# run -> echoes exit code
run() { ( cd "$work" && python3 scripts/packcheck.py >"$work/out" 2>&1 ); echo $?; }

check() { # name want_exit grep_needle
    name=$1; want=$2; needle=$3
    mk "$PACK"
    rc=$(run)
    if [ "$rc" -eq "$want" ] && { [ -z "$needle" ] || grep -q -- "$needle" "$work/out"; }; then
        echo "ok   [$name] (exit $rc)"
        pass=$((pass+1))
    else
        echo "FAIL [$name]: exit $rc wanted $want, needle ${needle:-<none>}"
        sed 's/^/    /' "$work/out"
        fail=$((fail+1))
    fi
}

# A: honest glob, sample fires -> pass
PACK='rules:
  - id: carbonato.exec.obfuscation
    argv_any:
      - "*base64*"'
check "live glob -> PASS" 0 "0 dead"

# B: a glob that cannot possibly fire -> fail, and it must name the glob
PACK='rules:
  - id: carbonato.exec.obfuscation
    argv_any:
      - "*zzz-not-a-thing*"'
check "dead glob -> FAIL" 1 "zzz-not-a-thing"

# C: THE CONTAINS ROUTE. `*/etc/cron*` catches /etc/cron.d/x through the substring
#    needle "/etc/cron" (engine.go:281 trims the stars, guards only `*?[`; '/' is NOT
#    special). packcheck must report it LIVE, not dead.
#    Needs the argv_any key AND a rule id that packcheck has samples for, since a
#    synthetic id is skipped for want of samples and would read as a false "blind".
PACK='rules:
  - id: carbonato.persist.cron_or_rc
    argv_any:
      - "*/etc/cron*"'
check "contains-route glob -> PASS" 0 "0 dead"

# D: unparseable pack -> exit 2, and 2 must never read as a pass
PACK='this is not a rule pack at all'
check "unparseable pack -> exit 2 (blind)" 2 "parsed 0 rules"

echo
echo "packcheck_test: $pass behaved, $fail misbehaved"
[ "$fail" -eq 0 ] || exit 1

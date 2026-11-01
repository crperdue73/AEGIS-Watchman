#!/bin/sh
# fpprobe_test — prove fpprobe can PASS, can FAIL, and cannot be satisfied by a
# pack that never fires. Three properties; a suite with only the second would be
# content with a detector that reports nothing.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rules" "$work/scripts"
cp "$HERE/packcheck.py" "$HERE/fpprobe.py" "$work/scripts/"
pass=0; fail=0

check() { # name want-exit needle packtext
  name=$1; want=$2; needle=$3; pack=$4
  printf '%s\n' "$pack" > "$work/rules/carbonato.yaml"
  ( cd "$work" && python3 scripts/fpprobe.py >out 2>&1 ); rc=$?
  if [ "$rc" -eq "$want" ] && grep -q -- "$needle" "$work/out"; then
    echo "ok   [$name] (exit $rc)"; pass=$((pass+1))
  else
    echo "FAIL [$name] exit $rc want $want"; sed 's/^/    /' "$work/out"; fail=$((fail+1))
  fi
}

# A: a pack with ONE narrow attack glob and no noise -> clean run, exit 0.
check "narrow pack -> PASS" 0 "0 unexpected result" 'rules:
  - id: carbonato.persist.cron_or_rc
    argv_any:
      - "*>> /etc/cron.d/*"'

# B: the same pack plus a noisy glob -> must FAIL and point at the noise.
check "noisy glob -> FAIL, names NOISE" 1 "NOISE" 'rules:
  - id: carbonato.persist.cron_or_rc
    argv_any:
      - "*>> /etc/cron.d/*"
      - "*crontab*"'

# C: a pack that fires on NOTHING -> must FAIL. Without this case, a detector
#    that never alerts would score a perfect quiet rate and read as a success.
check "silent pack -> FAIL, names MISS" 1 "MISS" 'rules:
  - id: carbonato.persist.cron_or_rc
    argv_any:
      - "*zzz_never_matches*"'

# D: unreadable pack -> exit 2, never a pass.
check "unparseable pack -> exit 2" 2 "parsed 0 rules" 'nope'

echo
echo "fpprobe_test: $pass behaved, $fail misbehaved"
[ "$fail" -eq 0 ] || exit 1

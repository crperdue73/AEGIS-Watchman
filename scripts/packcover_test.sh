#!/bin/sh
# packcover_test — prove packcover can PASS as well as FAIL.
# A tool that always reports misses is as useless as one that never does.
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rules" "$work/scripts"
cp "$HERE/packcheck.py" "$HERE/packcover.py" "$work/scripts/"
pass=0; fail=0

check() { # name want needle packtext
  name=$1; want=$2; needle=$3; pack=$4
  printf '%s\n' "$pack" > "$work/rules/carbonato.yaml"
  ( cd "$work" && python3 scripts/packcover.py >out 2>&1 ); rc=$?
  if [ "$rc" -eq "$want" ] && grep -q -- "$needle" "$work/out"; then
    echo "ok   [$name] (exit $rc)"; pass=$((pass+1))
  else
    echo "FAIL [$name] exit $rc want $want"; sed 's/^/    /' "$work/out"; fail=$((fail+1))
  fi
}

# A: every claim covered by the glob that names it -> PASS
check "all claims correct -> PASS" 0 "0 misattributed" 'rules:
  - id: carbonato.persist.survives_session
    argv_any:
      - "*nohup*"
      - "*setsid*"'

# B: the claim fires, but no glob NAMES it -> must FAIL and say WRONG.
#    (A duplicate glob would still contain the marker, so it proves nothing;
#     a catch-all is the real "right answer, wrong reason" shape.)
check "right answer, wrong reason -> FAIL" 1 "WRONG" 'rules:
  - id: carbonato.persist.survives_session
    argv_any:
      - "*nohup*"
      - "*sid*"'

# C: nothing catches the claim at all -> must FAIL, must say MISS
check "uncaught claim -> FAIL" 1 "MISS" 'rules:
  - id: carbonato.persist.survives_session
    argv_any:
      - "*zzz*"'

# C2: a pack rule with NO claims entry must be reported UNCHECKED, not skipped
#     silently. This is the defect I found in packcover itself an hour after
#     writing it: 3 of 7 rules were unchecked while the summary read confident.
check "unchecked rule is named -> FAIL-ish" 1 "UNCHECKED RULES" 'rules:
  - id: carbonato.persist.survives_session
    argv_any:
      - "*nohup*"
      - "*setsid*"
  - id: carbonato.some.unknown_rule
    argv_any:
      - "*whatever*"'

# D: unreadable pack -> exit 2, never a pass
check "unparseable pack -> exit 2" 2 "parsed 0 rules" 'not a pack'

echo
echo "packcover_test: $pass behaved, $fail misbehaved"
[ "$fail" -eq 0 ] || exit 1

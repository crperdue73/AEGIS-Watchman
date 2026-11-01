#!/usr/bin/env bash
# subjectprobe_test.sh — the probe must be able to be WRONG.
#
# A report-only tool still has to be checked, or it becomes furniture nobody
# reads. The pass case matters most: a probe that always prints the same verdict
# scores every check the same way.

set -uo pipefail
cd "$(dirname "$0")/.."
PY=$(command -v python3)
pass=0; fail=0

ok()   { echo "  ok    $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }

# 1. The real pack parses and produces a verdict. If this fails nothing else means anything.
out=$("$PY" scripts/subjectprobe.py 2>&1); rc=$?
if [ $rc -eq 0 ] && echo "$out" | grep -q "OBJECT-ONLY"; then ok "real pack reports per-rule verdicts"
else bad "real pack: rc=$rc"; fi

# 2. The corpus must contain a rule that fires BOTH ways. If every rule came back
#    OBJECT-ONLY the probe has lost the ability to say "this one is fine", and a
#    report of all-bad is as useless as a report of all-good.
if echo "$out" | grep -q "\[BOTH\]"; then ok "probe can report a healthy rule"
else bad "no BOTH verdict — probe cannot distinguish"; fi

# 3. The known noise cases must actually be attributed to object slots.
#    `man crontab` firing on the subject would mean the slot logic is inverted.
if echo "$out" | grep -q "noise.*object.*man crontab"; then ok "man crontab attributed to object slot"
else bad "man crontab attribution wrong or missing"; fi

# 4. Unparseable pack -> exit 2, not a silent empty report.
tmp=$(mktemp -d)
cat > "$tmp/packcheck.py" <<'PY'
PACK = "/nonexistent/pack.yaml"
def parse_pack(s): return []
def go_path_match(g, s): return False
def suffixes(s): return []
def match_one(g, s): return False
PY
cp scripts/subjectprobe.py scripts/fpprobe.py "$tmp/" 2>/dev/null
( cd "$tmp" && "$PY" subjectprobe.py >/dev/null 2>&1 ); rc=$?
rm -rf "$tmp"
if [ $rc -eq 2 ] || [ $rc -eq 1 ]; then ok "unreadable pack fails loud (rc=$rc)"
else bad "unreadable pack returned rc=$rc"; fi

echo "  $pass behaved, $fail misbehaved"
[ $fail -eq 0 ]

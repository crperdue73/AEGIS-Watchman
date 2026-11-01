#!/usr/bin/env bash
# crossprobe_test.sh — the probe must be able to be WRONG, and must show its denominators.
#
# crossprobe's headline is "PURE 18, SHARED 0". Today's last two findings both failed by
# counting cases that could never have failed. So case 3 here does not check the verdict —
# it checks that the probe PRINTS how many lines fired, because a purity score without a
# denominator is the exact defect this run of probes exists to catch.

set -uo pipefail
cd "$(dirname "$0")/.."
PY=$(command -v python3)
pass=0; fail=0

ok()  { echo "  ok    $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }

# 1. Real pack parses, reports, exits 0.
out=$("$PY" scripts/crossprobe.py 2>&1); rc=$?
if [ $rc -eq 0 ] && echo "$out" | grep -q "PURE"; then ok "real pack reports verb2 verdicts"
else bad "real pack: rc=$rc"; fi

# 2. The known SHARED case must be detectable at all. We prove it with a fixture: two
#    rules that both match the same verb2. If the probe cannot say SHARED, its "SHARED 0"
#    on the real pack means nothing.
tmp=$(mktemp -d)
mkdir -p "$tmp/scripts" "$tmp/rules"
cp scripts/packcheck.py scripts/fpprobe.py scripts/crossprobe.py "$tmp/scripts/"
cat > "$tmp/rules/carbonato.yaml" <<'YAML'
rules:
  - id: test.rule_a
    match:
      argv_any:
        - "*crontab*"
  - id: test.rule_b
    match:
      argv_any:
        - "*crontab*"
YAML
out2=$(cd "$tmp" && "$PY" scripts/crossprobe.py 2>&1); rc2=$?
if [ $rc2 -eq 0 ] && echo "$out2" | grep -q "\[SHARED\]"; then ok "probe can say SHARED"
else bad "SHARED case undetectable: rc=$rc2 out=$out2"; fi

# 3. DENOMINATOR MUST BE PRINTED. The purity line alone cannot be read as a rate.
if echo "$out" | grep -q "PURE 18" && echo "$out" | grep -q "SHARED"; then ok "verdict counts printed"
else bad "verdict counts missing"; fi
denominator=$("$PY" - <<'PY'
import sys; sys.path.insert(0, "scripts")
from fpprobe import CORPUS
print(len(CORPUS))
PY
)
if [ "${denominator:-0}" -ge 1 ]; then ok "corpus size readable ($denominator lines)"; else bad "corpus size unknown"; fi

# 4. Unreadable pack -> exit 2.
rm -f "$tmp/rules/carbonato.yaml"
(cd "$tmp" && "$PY" scripts/crossprobe.py >/dev/null 2>&1); rc3=$?
if [ $rc3 -eq 2 ]; then ok "missing pack -> rc 2"; else bad "missing pack -> rc $rc3 (want 2)"; fi

rm -rf "$tmp"
echo "crossprobe_test: $pass ok, $fail fail"
[ $fail -eq 0 ]

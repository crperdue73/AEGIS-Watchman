#!/usr/bin/env bash
# flagprobe_test.sh — the probe must be able to be WRONG.
#
# flagprobe's headline number is "0 FLAG-CARRIED rules". A probe whose answer is
# always zero is a ruler with no markings. These cases force it to say FLAG-CARRIED
# at least once, and to fail loudly when the pack is unreadable.

set -uo pipefail
cd "$(dirname "$0")/.."
PY=$(command -v python3)
pass=0; fail=0

ok()  { echo "  ok    $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; fail=$((fail+1)); }

# 1. Real pack: parses, reports, and the POSITIONAL verdict is present.
out=$("$PY" scripts/flagprobe.py 2>&1); rc=$?
if [ $rc -eq 0 ] && echo "$out" | grep -q "POSITIONAL"; then ok "real pack reports verdicts"
else bad "real pack: rc=$rc"; fi

# 2. Real pack: the known all-attack, all-flag rule is still attributed to the
#    joined line, not silently dropped. If this rule vanished the probe would be
#    reporting a clean zero because it stopped seeing the case.
if echo "$out" | grep -q "carbonato.infra.privileged_container"; then ok "privileged_container is seen"
else bad "privileged_container missing — probe blind to its own motivating case"; fi

# 3. PASS CASE — a pack whose only rule matches a flag value must report
#    FLAG-CARRIED. Without this the "0" in case 1 means nothing.
tmp=$(mktemp -d)
mkdir -p "$tmp/scripts" "$tmp/rules"
cp scripts/packcheck.py scripts/fpprobe.py scripts/flagprobe.py "$tmp/scripts/"
cat > "$tmp/rules/carbonato.yaml" <<'YAML'
rules:
  - id: test.flagvalue
    match:
      argv_any:
        - "/data:/data"
YAML
out2=$(cd "$tmp" && "$PY" scripts/flagprobe.py 2>&1); rc2=$?
if [ $rc2 -eq 0 ] && echo "$out2" | grep -q "\[FLAG-CARRIED\]"; then ok "probe can say FLAG-CARRIED"
else bad "pass case failed: rc=$rc2 out=$out2"; fi
if echo "$out2" | grep -q "FLAG-CARRIED rules: 1"; then ok "pass case counts exactly one"
else bad "pass case count wrong"; fi

# 4. Unreadable pack -> exit 2, not a silent empty report that reads like health.
rm -f "$tmp/rules/carbonato.yaml"
(cd "$tmp" && "$PY" scripts/flagprobe.py >/dev/null 2>&1); rc3=$?
if [ $rc3 -eq 2 ]; then ok "missing pack -> rc 2"
else bad "missing pack -> rc $rc3 (want 2)"; fi

rm -rf "$tmp"
echo "flagprobe_test: $pass ok, $fail fail"
[ $fail -eq 0 ]

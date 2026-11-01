#!/bin/sh
# needleprobe must be able to go RED. A probe that only ever prints "confirmed" is
# decoration — this drives the needle rule in both directions and checks the exit code.
set -u
# Beat 634: every path in this file is resolved against the REPO ROOT, not $PWD. suitecheck.sh runs each
# suite from a temp cwd, so the old relative paths ('scripts/needleprobe.py', 'cp -r internal scripts')
# only worked when a human ran it from the repo root -- under the gate it died in `cp` and reported the
# wreckage as "the probe went red", i.e. a broken invocation wore a passing verdict. It never ran under
# suitecheck before this beat (it was in the baseline, but suitecheck copies the tree); now it does.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
fails=0
ok() { printf 'ok    %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; fails=$((fails+1)); }

# 1. the live tree: mirror and engine now AGREE on slash-bearing needles -> exit 0
#    UPDATED 2026-10-27 (beat 610): the divergence this guard was built around was FIXED in
#    neargo.py. The old version required exit 1 and would now fail forever. Flipped to require
#    agreement -- and section 2 below still proves the guard can go red, so it is not decoration.
python3 "$ROOT/scripts/needleprobe.py" >"$work/live" 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok "live tree returns 0 (mirror and engine agree — gap closed)" \
                || bad "live tree returned $rc, want 0 — a slash-bearing needle diverged again"

# 2. and there must be NO verdict diffs: agreement all the way to the answer
if grep -q 'VERDICT DIFF' "$work/live"; then
  bad "$(grep -c 'VERDICT DIFF' "$work/live") verdict diffs — mirror and engine disagree on an outcome"
else
  ok "no verdict diffs: agreement reaches the match outcome, not just the intermediate"
fi

# 3. the premise check must be able to fail: add a '/' test to the engine rule and confirm RED
mkdir -p "$work/tree"; cp -r "$ROOT/internal" "$ROOT/scripts" "$work/tree/"
python3 - "$work/tree/internal/rules/engine.go" <<'PYEOF'
import sys
p = sys.argv[1]; s = open(p).read()
old = '\tif strings.ContainsAny(core, "*?[") {\n\t\treturn "", false\n\t}'
if old not in s:
    sys.exit("mutation anchor not found in engine.go — update this test")
s = s.replace(old, old + '\n\tif strings.Contains(core, "/") {\n\t\treturn "", false\n\t}', 1)
open(p, 'w').write(s)
PYEOF
( cd "$work/tree" && python3 scripts/needleprobe.py >out 2>&1 ); rc=$?
# Patching the engine to reject '/' CLOSES the gap, so a naive probe would go green and this
# tree would quietly believe the mirror was fixed. It must go RED instead, because the premise
# it was built on ("the engine does not reject a slash") is now false — that is the probe asking
# to be re-derived, not a passing test.
[ "$rc" -eq 1 ] && ok "engine patched to reject '/' -> probe goes RED (asks to be re-derived)" \
                || bad "probe exited $rc after the engine changed; it should refuse to silently pass"

# 4. and it must SAY why, naming the stale premise rather than a generic failure
grep -q "PREMISE: engine needle rule does NOT reject a slash" "$work/tree/out" \
  && ok "probe names the stale premise" \
  || bad "probe went red without naming the stale premise"

[ "$fails" -eq 0 ] && { echo "needleprobe_selfcheck: 4 ok"; exit 0; }
echo "needleprobe_selfcheck: $fails failed"; exit 1

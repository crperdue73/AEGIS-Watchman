#!/usr/bin/env bash
# suitecheck_test.sh — prove suitecheck.sh's own alarm can still fire.
#
# Why: suitecheck.sh is the guard I wrote on 2026-10-25 to catch test suites that pass while
# testing nothing. Beats 565-568 spent three hours discovering that the guard had the same
# defect class in its own body — twice. A guard nobody has ever watched fail is the thing
# this repo exists to complain about, so here is the watch.
#
# Method: grep the guards' own text for how many branches there are, then MUTATE each guard
# on a throwaway copy of the tree and require the mutant to be caught. It is not real
# mutation testing of arbitrary code — it perturbs specific, named guards in this one file
# and fails loudly if a guard it expects to find has MOVED (which would make the mutation a
# no-op and the green result vacuous — the exact trap beat 565 fell into three times).
#
# Requires: bash, tar. No Go, no make, no network.
#
# BEAT 647 — every mutant used to invoke `sh suitecheck.sh`. suitecheck.sh is bash
# (`#!/usr/bin/env bash`) and uses bash-only syntax, notably the ANSI-C quote $'\n' when it builds
# its file list. Under dash that quote is a LITERAL dollar-backslash-n, so SELFHARNESS entries
# were joined into one
# bogus filename: measured 58 suites / 1292 checks under sh against 64 / 1434 under bash, plus a
# phantom "FAIL wirediff.py no shebang". This file has been grading a crippled copy of the gate and
# reporting "mutant caught" the whole time. It only became visible when endpointfixture.py was added
# to SELFHARNESS -- the extra entry changed the shape of the broken string. A test that runs the
# thing under test with the wrong interpreter is testing a file that does not exist.
#
# Usage: ./scripts/suitecheck_test.sh [-v]
# Exit: 0 every mutant was caught; 1 at least one mutant survived.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
verbose=0
[ "${1:-}" = "-v" ] && verbose=1

pass=0
fail=0

# mktree -> fresh copy of the FULL tree without .git. Expensive: ~91 s to run the gate inside it,
# because it executes all 64 product suites.
mktree() {
  local d
  d="$(mktemp -d)"
  ( cd "$ROOT" && tar -cf - --exclude=.git --exclude=__pycache__ . 2>/dev/null ) | tar -xf - -C "$d"
  echo "$d"
}

# stubtree -> a tiny tree holding ONLY suitecheck.sh plus three two-line stub suites.
#
# BEAT 648. Every mutant used to run inside a full copy of the product tree. Eight copies x 91 s is
# ~12 minutes on a good run and ~25 on a loaded box -- longer than one heartbeat, which means the check
# that proves the gate still works was on its way to becoming a check nobody runs. But these mutants
# are about suitecheck.sh's VERDICT LOGIC (does a crash get told apart from a red test? does a deleted
# suite get named?), and that logic does not depend on there being 64 real suites. Measured: the same
# baseline-guard case that takes 91 s on the full tree returns the identical verdict in 0.041 s on a
# three-stub tree. A stub tree that answers the same question in 0.04% of the time is not a shortcut,
# it is the right-sized input.
#
# What this must NOT become: a test that only ever exercises stubs because stubs are convenient. M8
# below still runs ONE full-tree mutant deliberately, so the cheap cases can never drift far enough
# from reality to be meaningless without something going red.
stubtree() {
  local d n
  d="$(mktemp -d)"
  mkdir -p "$d/scripts"
  cp "$HERE/suitecheck.sh" "$d/scripts/suitecheck.sh"
  for n in alpha beta gamma; do
    printf '#!/bin/sh\necho "ok   stub %s assertion"\n' "$n" > "$d/scripts/${n}_test.py"
    chmod +x "$d/scripts/${n}_test.py"
  done
  ( cd "$d/scripts" && bash suitecheck.sh --update-baseline >/dev/null 2>&1 )
  echo "$d"
}

report() { # report NAME ok|bad detail
  if [ "$2" = ok ]; then
    echo "ok   [$1] $3"
    pass=$((pass+1))
  else
    echo "FAIL [$1] $3"
    fail=$((fail+1))
  fi
}

# --- M1: dropping a check from a suite must be caught by the count baseline -----
# (This is the guard's headline capability; if it stops working everything else is decoration.)
# NOTE: the anchor is matched with a REGEX, not a literal string. The first version of this
# test hardcoded the exact source line and a single quote inside it made the anchor a no-op —
# it reported "anchor moved" on an unmodified tree and then kept going. Matching on the case
# NAME alone is stable against quoting and against the sentence being reworded.
d=$(mktree)
if ! grep -q 'expect "duplicate func -> 1"' "$d/scripts/pkgcheck_test.sh"; then
  report M1 bad "anchor moved: pkgcheck_test.sh no longer contains the case M1 mutates — fix the test, not the tree"
else
  # replace the call with a no-op that prints nothing and counts nothing: a silent give-up
  sed -i 's|^expect "duplicate func -> 1".*$|true  # M1: case silently dropped|' "$d/scripts/pkgcheck_test.sh"
  out=$(bash "$d/scripts/suitecheck.sh" 2>&1); rc=$?
  if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "below baseline\|checks now, baseline says"; then
    report M1 ok "silently-dropped case caught: $(printf '%s' "$out" | grep -o 'checks now, baseline says [0-9]*' | head -1)"
  else
    report M1 bad "silently-dropped case NOT caught (rc=$rc)"
    [ "$verbose" -eq 1 ] && printf '%s\n' "$out" | sed 's/^/    /'
  fi
fi
rm -rf "$d"

# --- M2: a suite that prints FAIL and exits 0 must be caught -------------------
# (Found 2026-10-25: pkgcheck_test.sh does exactly this when run without bash.)
d=$(mktree)
cat > "$d/scripts/zz_loudfail_test.sh" <<'SH'
#!/bin/sh
echo "ok   [invented pass]"
echo "FAIL [invented failure] this suite lies about its status"
exit 0
SH
out=$(bash "$d/scripts/suitecheck.sh" 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "zz_loudfail_test.sh.*reported-failures"; then
  report M2 ok "FAIL-then-exit-0 caught"
else
  report M2 bad "FAIL-then-exit-0 NOT caught (rc=$rc)"
  [ "$verbose" -eq 1 ] && printf '%s\n' "$out" | sed 's/^/    /'
fi
rm -rf "$d"

# --- M3: a suite that exits 0 printing nothing must be caught -----------------
# (Beat 564: 25 of 26 suites did exactly this and my own loop called it green.)
d=$(stubtree)   # beat 648: verdict logic only, stub input is the right size
printf '#!/bin/sh\nexit 0\n' > "$d/scripts/zz_silent_test.sh"
out=$(bash "$d/scripts/suitecheck.sh" 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "zz_silent_test.sh.*no-verdicts-printed"; then
  report M3 ok "vacuous exit-0 caught"
else
  report M3 bad "vacuous exit-0 NOT caught (rc=$rc)"
fi
rm -rf "$d"

# --- M4: a suite that crashes mid-run must be caught --------------------------
d=$(stubtree)   # beat 648: verdict logic only
printf '#!/bin/sh\necho "ok   [before crash]"\nkill -9 $$\n' > "$d/scripts/zz_crash_test.sh"
out=$(bash "$d/scripts/suitecheck.sh" 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "zz_crash_test.sh.*exit=137"; then
  report M4 ok "mid-run crash caught"
else
  report M4 bad "mid-run crash NOT caught (rc=$rc)"
fi
rm -rf "$d"

# --- M4b: a suite that dies BEFORE printing any verdict must be named CRASH, not FAIL ----
# Beat 646. M4 above is a MID-RUN crash: it prints `ok` then dies, so it exercises the FAIL path
# and proves nothing about the new CRASH branch. The defect this covers is subtly different and
# worse: the old verdict was one OR'd chain (`rc!=0 || fails!=0 || oks==0`), so a suite that CRASHED
# and a suite that SAID NO printed the identical line. An import error and a failing assertion are
# both exit 1; they are not the same evidence. This case requires the two to be DISTINGUISHABLE,
# not merely both non-zero.
d=$(stubtree)   # beat 648: verdict logic only
printf '#!/bin/sh\necho "Traceback: cannot import x" >&2\nexit 1\n' > "$d/scripts/zz_dead_test.sh"
printf '#!/bin/sh\necho "ok   [a real assertion that holds]"; echo "FAIL [a real assertion that does not]"; exit 1\n' > "$d/scripts/zz_red_test.sh"
out=$(bash "$d/scripts/suitecheck.sh" 2>&1); rc=$?
crash_named=0; red_named=0
printf '%s' "$out" | grep -q "^CRASH zz_dead_test.sh" && crash_named=1
printf '%s' "$out" | grep -q "^FAIL zz_red_test.sh" && red_named=1
if [ "$rc" -ne 0 ] && [ "$crash_named" -eq 1 ] && [ "$red_named" -eq 1 ]; then
  # Distinguishable is the point. If the crash is also reported as a plain FAIL, the branches
  # collapsed again and the verdict is back to "one line for two different things".
  if printf '%s' "$out" | grep -q "^FAIL zz_dead_test.sh"; then
    report M4b bad "deaths named CRASH but ALSO reported as plain FAIL -- branches collapsed"
  else
    report M4b ok "crash and red test are distinguishable (CRASH vs FAIL)"
  fi
else
  report M4b bad "CRASH/FAIL not distinguishable (rc=$rc crash_named=$crash_named red_named=$red_named)"
fi
rm -rf "$d"

# --- M5: a suite deleted from the tree must be caught ------------------------
d=$(stubtree)   # beat 648: verdict logic only; 'beta' stands in for the product suite
rm -f "$d/scripts/beta_test.py"
# BEAT 648 CORRECTION. On a purely clean stub tree, deleting a suite leaves bad_suites=0, so the
# OLD (buggy) ordering ALSO reports the deletion correctly -- this case was green against the very
# defect it exists to catch, which I only found by running it against a reverted copy. The bug only
# bites when something else is ALSO failing, because that is what hits the early `exit 1` above the
# baseline loop. So add a genuinely failing stub: now a wrong ordering loses the deletion message,
# and only a correct ordering keeps both.
printf '#!/bin/sh\necho "ok   [a holds]"; echo "FAIL [b does not]"; exit 1\n' > "$d/scripts/zz_alsofail_test.sh"
chmod +x "$d/scripts/zz_alsofail_test.sh"
out=$(bash "$d/scripts/suitecheck.sh" 2>&1); rc=$?
# BEAT 647. This case used to accept any rc=1 as "caught". It passed for weeks on a run that was red
# for an UNRELATED reason: the baseline comparison sat BELOW an earlier `exit 1`, so it never executed,
# and the deletion was only noticed incidentally because docsindex/packpresent happen to read the
# scripts directory. Asserting the NAME and the REASON, not the exit code, is what makes this a test of
# the baseline guard rather than a test of "something somewhere went wrong".
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "beta_test.py: in the baseline but no longer runs"; then
  if printf '%s' "$out" | grep -q "BELOW BASELINE"; then
    report M5 ok "deleted suite caught by name, by the baseline guard"
  else
    report M5 bad "deleted suite named but no BELOW BASELINE verdict -- summary path broken"
  fi
else
  report M5 bad "deleted suite NOT named by the baseline guard (rc=$rc)"
fi
rm -rf "$d"

# --- M6: the BELOW-BASELINE branch must not also print the success summary ----
# Beat 567 found the failure path inheriting "26 suites held ... none below baseline".
# That is the worst possible output: a guard saying both things at once. This mutant
# asserts the negative — count the success sentence under a real below-baseline failure.
d=$(mktree)
sed -i 's|^expect "duplicate func -> 1".*$|true|' "$d/scripts/pkgcheck_test.sh"
out=$(bash "$d/scripts/suitecheck.sh" 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "suites held"; then
  report M6 bad "failure output ALSO claims 'suites held' — contradictory verdicts"
elif [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "BELOW BASELINE"; then
  report M6 ok "below-baseline path prints one honest line, no success claim"
else
  report M6 bad "M6 inconclusive: rc=$rc, no BELOW BASELINE line (did M1's guard stop working?)"
fi
rm -rf "$d"

# --- M7 + M8: on a FULL tree (beat 648 merges these to save one copy) --------
# M7 proves the mutants are real, not decoration: beat 565's hardest lesson was three "mutation
# tests" where the mutation changed nothing and the green result meant nothing. So take M1's
# mutation and prove it CHANGED the file.
#
# M8 is the counterweight that keeps the stubs honest. M3/M4/M4b/M5 now run on three-line stub
# trees because the verdict logic does not care how many suites exist (measured: 91 s full vs
# 0.041 s stub for the same baseline-guard verdict). The risk of that optimisation is a test suite
# that only ever exercises stubs and drifts away from the real gate. So M8 runs the UNMUTATED
# real tree once and requires the real numbers: 64 suites, 1434 checks, no failures, no crashes.
# If the product tree changes shape, this case goes red and tells me to re-measure deliberately
# instead of letting a stale count sit in a comment.
d=$(mktree)
before=$(md5sum "$d/scripts/pkgcheck_test.sh" | cut -d' ' -f1)
if ! grep -q 'expect "duplicate func -> 1"' "$d/scripts/pkgcheck_test.sh"; then
  mrc=3
else
  sed -i 's|^expect "duplicate func -> 1".*$|true|' "$d/scripts/pkgcheck_test.sh"
  mrc=0
fi
after=$(md5sum "$d/scripts/pkgcheck_test.sh" | cut -d' ' -f1)
if [ "$mrc" -ne 0 ]; then
  report M7 bad "M1's anchor did not match — the mutation would have been a no-op"
elif [ "$before" = "$after" ]; then
  report M7 bad "mutation produced an identical file — a no-op pretending to be a test"
else
  report M7 ok "mutation verifiably changed the tree ($(printf '%s' "$before" | cut -c1-8) -> $(printf '%s' "$after" | cut -c1-8))"
fi

# M8: the control. Unmutated full tree, real numbers required.
#
# Beat 704: this was RED and nothing said so. Two separate reasons, both measured tonight:
#  (a) VIVI_WS was not passed, so windowblind.py derived WS from __file__ inside the COPY -> WS became '/',
#      the secrets store was 'missing', and the suite failed for a reason unrelated to what it checks.
#      Suites that need the real workspace now get an explicit VIVI_WS pointing at it.
#  (b) the pin said 65 suites / 1445 checks -- last true at beat 653. The tree has since grown to 96/1772 and
#      M8 stayed pinned to a number that had not been accurate for ~50 beats. It could not report the drift,
#      because suitecheck.sh EXCLUDES suitecheck_test.sh (line 52, recursion guard). So the control run -- the
#      one case here whose whole job is to notice the real gate changing shape -- was the one case no gate ran.
#      A control that only runs when someone remembers to run it by hand is a control that rots quietly.
d2=$(mktree)
# Beat 706: VIVI_WS must be the WORKSPACE (the dir that contains memory/secrets/), not the project root.
# I passed "$ROOT" here twice and it silently pointed at projects/carbonato-detection-tool, where there is no
# memory/secrets -- so the suite kept failing and I kept 'fixing' the wrong line. ROOT is the project dir;
# the workspace is three levels up from scripts/. Derived, not hardcoded, so a moved tree still works.
WS_ROOT="$(cd "$HERE/../../.." && pwd)"
out2=$(VIVI_WS="$WS_ROOT" bash "$d2/scripts/suitecheck.sh" 2>&1); rc2=$?
want_suites=96
want_checks=1772
got_line=$(printf '%s' "$out2" | grep '^suitecheck: .* suites held' | head -1)
if [ "$rc2" -ne 0 ]; then
  report M8 bad "CONTROL RUN FAILED on an unmutated tree (rc=$rc2) — every other result here is suspect"
  [ "$verbose" -eq 1 ] && printf '%s\n' "$out2" | sed 's/^/    /'
elif printf '%s' "$got_line" | grep -q "$want_suites suites held, $want_checks checks ok" && \
     printf '%s' "$got_line" | grep -q "0 reported failures"; then
  report M8 ok "stub trees have not drifted: full control still $want_suites suites / $want_checks checks"
else
  report M8 bad "full tree no longer matches $want_suites/$want_checks — got: ${got_line:-<nothing>}. Re-measure deliberately, do not just bump the number."
fi
rm -rf "$d2"
rm -rf "$d"

echo
echo "suitecheck_test: $pass behaved, $fail misbehaved"
echo "Reminder: a green run here proves suitecheck's ALARMS still fire. It does not prove the"
echo "          suites it runs are meaningful, and it cannot see a harness that skips a case"
echo "          while still counting it ok."
[ "$fail" -eq 0 ] || exit 1

exit 0

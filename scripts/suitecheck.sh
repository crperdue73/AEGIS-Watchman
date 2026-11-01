#!/usr/bin/env bash
# suitecheck.sh — run every scripts/*_test.{py,sh} and fail if ANY of them fails.
#
# Why this exists: on 2026-10-25 I found that the 26 script tests each exit 0 when
# run alone, but nothing in the tree runs them all, and five of them (pkgcheck_test.sh
# among them) report 0 ok / 8 misbehaved while still exiting 0 — so a bare `for f in
# scripts/*_test*; do $f; done` would call that green. An exit status is not a verdict
# here. The verdict is the ok/fail count the test PRINTS, checked against the status it
# RETURNS. This script checks both.
#
# 2026-10-28 (beat 634): "_test" is not the only way a file can be a suite. pathfixture.py and
# redundant_mut.py assert like suites and are named like instruments; neither was ever run. They are now
# named in SELFHARNESS below. The lesson is that the gate's file selection was a NAME PATTERN, which is
# the same class of defect as a check that greps for a comment: it looks like coverage and isn't.
#
# Three failure modes it catches that `make test` cannot (the Go toolchain is absent on
# this box, so `go test ./...` is dark):
#   1. a suite that fails cases but exits 0            (pkgcheck_test.sh under `sh`)
#   2. a suite that crashes before printing any verdict (a vacuous green)
#   3. a suite that reports FEWER passing checks than it did before (see below)
#
# (3) is not theoretical and it is not what the first version of this script caught.
# 2026-10-25, 5:40 AM: I broke pkgcheck_test.sh on a throwaway copy — made `expect()`
# return 0 after recording a failure — and this script called it "26 suites held, 470
# checks ok, 0 reported failures", rc 0. Every case still printed `ok`, because the case
# that broke was the one that was SUPPOSED to fail; a test harness that gives up while
# still passing is invisible from status and from verdict text alike. The only thing
# that moved was the count: 470 -> 468. So the count is now an assertion.
#
# It does NOT claim any test is meaningful — only that it ran, printed verdicts, reported
# no failures, and checked at least as many cases as the baseline records. Whether a test
# can fail at all is each *_test's own job (and several of them test exactly that).
#
# Usage: ./scripts/suitecheck.sh [-v]
# Exit: 0 all suites ran, reported verdicts, and reported no failures.
#       1 at least one suite failed, crashed, or printed no verdicts.

set -uo pipefail
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="${PYTHON:-python3}"
verbose=0
[ "${1:-}" = "-v" ] && verbose=1

# Baseline: the measured count of passing checks per suite, 2026-10-25 on this tree.
# Update ONLY when a change deliberately adds or removes checks, and say why in the
# commit. `--update-baseline` rewrites it.
BASE="$HERE/suitecheck.baseline"
# Self-test exclusion: suitecheck must not run suitecheck_test (infinite recursion: the test
# copies the tree and runs suitecheck on it). Named exclusion, not a pattern — the recursion
# guard should be visible in the diff if it ever changes.

files=$(ls "$HERE"/*_test.py "$HERE"/*_test.sh 2>/dev/null | grep -v "/suitecheck_test.sh$" | sort)
[ -z "$files" ] && { echo "suitecheck: no test files found in $HERE" >&2; exit 1; }

# BEAT 634: instruments that ARE their own harness.
# pathfixture.py prints `ok`/`FAIL`/`xfail` per case and exits non-zero on a failure -- it behaves exactly
# like a suite, but its name does not end in _test, so suitecheck never ran it and neither did anything
# else. Measured this beat: 0 references from scripts/, the Makefile, or any *_test. It had been silently
# disabled for its whole life, and the first honest run over-matched seven paths
# (docs/FINDING-2026-10-28-contains-fallback-on-paths.md). redundant_mut.py is the mutation proof for
# redundant_test.py and had the same problem: written, committed, never invoked.
#
# This list is the fix, declared rather than pattern-matched, because the alternative -- running EVERY
# non-test module -- would run probes whose job is to investigate, not to assert, and a gate that fails on
# an investigation's findings cannot be used. A file belongs here only if, like a suite, it exits non-zero
# when something is wrong. debriscensus.py asserts exactly that: it names any module in here that does not.
# pathmatchdiff.py added beat 637: it compares neargo's port of Go's path.Match against known-Go answers
# and exits 1 on any disagreement. It qualified for this list all along and was never in it -- and because
# nobody ran it, it sat red for days on two WRONG expectations of its own, accusing correct port code.
# A gate that is never run does not just fail to catch defects; it accumulates them as false positives.
# docsindex.py added beat 638. It qualifies on the same rule: it exits 1 when the index is stale, a
# finding has no disposition, a status is a fragment, or a row loses a cell. Proven by injecting an
# unindexed finding into a copy of the tree and watching it go red (rc=1), not by reading its docstring.
# It had already caught a real defect in this workspace -- my own FINDING-2026-10-28 doc had no Status
# field, so the index silently absorbed a **Class:** heading as the disposition. Nothing else read that
# field, so nothing else could have noticed.
# wirediff.py added beat 638. It counts matched rule values that have no local fixture, and goes red on
# its own structural claims (proven by neutering the both-families check: rc=1, "path=30 endpoint=32").
# It is the file that turns "56 unguarded values" into a work queue with a denominator.
SELFHARNESS="$HERE/pathfixture.py $HERE/endpointfixture.py $HERE/argvfixture.py $HERE/windowfixture.py $HERE/jsonfidelity.py $HERE/clockfidelity.py $HERE/timelinemem.py $HERE/timelineprecedence.py $HERE/prunereap.py $HERE/spoolwedge.py $HERE/centralreject.py $HERE/batchsize.py $HERE/alertfanout.py $HERE/alertwiring.py $HERE/feedblind.py $HERE/classvocab.py $HERE/recordloss.py $HERE/healthprobe.py $HERE/auditsplit.py $HERE/gapsilence.py $HERE/durabilitygap.py $HERE/trimfloor.py $HERE/opaqueupstream.py $HERE/blockship.py $HERE/appendcost.py $HERE/pruneprefix.py $HERE/direntsource.py $HERE/scanblind.py $HERE/redundant_mut.py $HERE/pathmatchdiff.py $HERE/docsindex.py $HERE/wirediff.py $HERE/packvanished.py $HERE/fixtureledger.py $HERE/matcherblind.py $HERE/ruleclaims.py $HERE/docclaims.py $HERE/listenpred.py $HERE/eventclock.py $HERE/replaywarn.py $HERE/scopekey.py $HERE/claimcensus.py $HERE/timelinecap.py $HERE/retentioncensus.py $HERE/hostclaim.py $HERE/hostidshape.py $HERE/agentclaim.py $HERE/baselinekey.py $HERE/seencensus.py $HERE/livenesscensus.py $HERE/provenancecensus.py $HERE/fleetsamplecensus.py $HERE/contractruncensus.py $HERE/capordercensus.py $HERE/inplacefiltercensus.py $HERE/sqlitetrimcensus.py $HERE/retainpathcensus.py"
for f in $SELFHARNESS; do
  [ -f "$f" ] || continue
  files="$files"$'\n'"$f"
done
files=$(printf '%s\n' "$files" | sort)

# --update-baseline: re-measure and write the baseline, then stop. Deliberate act only.
update_base=0
[ "${1:-}" = "--update-baseline" ] && update_base=1
BASELINE_TMP=$(mktemp)
trap 'rm -f "$BASELINE_TMP"' EXIT

total_ok=0
total_fail=0
bad_suites=0
crashes=0
count=0

for f in $files; do
  b=$(basename "$f")
  count=$((count+1))
  # Run with the interpreter its shebang names. The files are not all mode +x, and the
  # shell here is not always the one in the shebang, so the shebang is read, not guessed:
  # pkgcheck_test.sh uses ${BASH_SOURCE[0]}, which /bin/sh does not define.
  shebang=$(head -1 "$f")
  # FINDING-2026-10-26b. This used to fall back to $PY for any unrecognised (including missing)
  # shebang. Eight .py suites in this directory had no shebang at all, so they were handed to `sh`,
  # which executed their docstrings as commands, exited 2, and contributed zero checks -- while the
  # gate still printed "held". Nothing was falsely green only because dash rejects Python syntax
  # loudly instead of exiting 0 on the noise. A harness that GUESSES the interpreter can only ever
  # test the guess, so now it refuses: no shebang, or one it does not recognise, is a bad suite.
  # The per-suite `no shebang` / `unknown shebang` strings below are asserted by shebang_test.py.
  case "$shebang" in
    "#!"*bash*)   runner=bash ;;
    "#!"*python*) runner=$PY ;;
    "#!"*sh)      runner=sh ;;
    "#!"*)        runner=""   ; why=" unknown shebang: $shebang" ;;
    *)            runner=""   ; why=" no shebang (refusing to guess an interpreter)" ;;
  esac
  if [ -z "$runner" ]; then
    bad_suites=$((bad_suites + 1))
    echo "FAIL $b${why}"
    printf '%s %s\n' "$b" 0 >> "$BASELINE_TMP"
    continue
  fi

  out=$(timeout 300 "$runner" "$f" 2>&1)
  rc=$?
  oks=$(printf '%s\n' "$out" | grep -cE '^ *ok')
  fails=$(printf '%s\n' "$out" | grep -cE '^ *(FAIL|not ok)')

  total_ok=$((total_ok + oks))
  total_fail=$((total_fail + fails))
  printf '%s %s\n' "$b" "$oks" >> "$BASELINE_TMP"

  # BEAT 646. These three conditions used to be OR'd together into one "bad" verdict, which made a
  # CRASH and a FAILED TEST print identically: both are `exit!=0` + `no-verdicts-printed`. On 2026-10-28
  # needleprobe.py raised ImportError for hours and the gate scored that as "the probe went red,
  # therefore it works" -- an import error and a failing assertion are both exit 1, and they are not
  # the same evidence. So classify the WHY before counting the suite:
  #
  #   CRASH      rc!=0 and no verdicts printed  -> the suite never reached its assertions. It proves
  #                                                NOTHING, in either direction. Named separately so
  #                                                it cannot be mistaken for a suite that said no.
  #   FAILED     verdicts printed, some negative, or rc!=0 with assertions -> a real red result.
  #   no-verdicts  rc==0 but nothing printed  -> a suite that asserts nothing is not a passing suite.
  #
  # Callie named the same shape on the bus tonight about her own canary ("if the grep itself failed I
  # would read it as a clean box") and said she was not fixing it mid-window. This is the fix.
  if [ "$rc" -ne 0 ] && [ "$oks" -eq 0 ] && [ "$fails" -eq 0 ]; then
    bad_suites=$((bad_suites + 1))
    crashes=$((crashes + 1))
    echo "CRASH $b ($runner) exit=$rc -- died before printing a single verdict; this is evidence of nothing"
    printf '%s\n' "$out" | sed 's/^/    /' | head -20
  elif [ "$rc" -ne 0 ] || [ "$fails" -ne 0 ] || [ "$oks" -eq 0 ]; then
    bad_suites=$((bad_suites + 1))
    why=""
    [ "$rc" -ne 0 ] && why="$why exit=$rc"
    [ "$fails" -ne 0 ] && why="$why reported-failures=$fails"
    [ "$oks" -eq 0 ] && why="$why no-verdicts-printed"
    echo "FAIL $b ($runner)$why"
    printf '%s\n' "$out" | sed 's/^/    /'
  else
    [ "$verbose" -eq 1 ] && echo "ok   $b ($runner) $oks checks"
  fi
done

if [ "$bad_suites" -ne 0 ]; then
  echo "suitecheck: $bad_suites of $count suites did not hold ($total_ok checks ok, $total_fail reported failures, $crashes crashes that proved nothing)"
fi

if [ "$update_base" -eq 1 ]; then
  {
    echo "# suitecheck baseline — measured $count suites, $total_ok checks, $(date -u +%Y-%m-%dT%H:%MZ)"
    echo "# A suite whose count DROPS fails suitecheck. Rewrite only for a deliberate change."
    sort "$BASELINE_TMP"
  } > "$BASE"
  echo "suitecheck: baseline written ($count suites, $total_ok checks) -> $BASE"
  exit 0
fi

# The count assertion. A suite that stops checking things still passes every condition
# above; the only evidence is that its number went down.
if [ ! -f "$BASE" ]; then
  echo "suitecheck: no baseline at $BASE — run ./scripts/suitecheck.sh --update-baseline" >&2
  exit 1
fi
regress=0
while read -r name want; do
  [ -z "$name" ] && continue
  case "$name" in \#*) continue ;; esac
  got=$(awk -v n="$name" '$1==n{print $2}' "$BASELINE_TMP")
  if [ -z "${got:-}" ]; then
    echo "FAIL $name: in the baseline but no longer runs"
    regress=$((regress+1))
  elif [ "$got" -lt "$want" ]; then
    echo "FAIL $name: $got checks now, baseline says $want — a suite that checks less is not a pass"
    regress=$((regress+1))
  fi
done < "$BASE"

# BEAT 647. The bad_suites check used to `exit 1` right here, ABOVE the baseline comparison, so
# deleting a suite could never be reported by the baseline check -- the run died first. Measured:
# `rm tokruns_test.py` gave rc=1 with the headline "2 of 63 suites did not hold", and the words
# "below baseline" never appeared. The deletion was caught only INCIDENTALLY, because docsindex and
# packpresent happen to read the scripts directory and noticed the file was gone. On a tree where
# those two stayed quiet, the same deletion would have printed "all held, none below baseline" and
# exited 0. A guard that can only fire when an unrelated guard fires is not a guard. Both verdicts
# are now computed before either is acted on.
if [ "$regress" -ne 0 ]; then
  # Do not print the headline "all held" summary on this path: a mutant of this very
  # branch was measured to echo "26 suites held ... none below baseline" in the same
  # output as the FAIL line (beat 567). Contradictory text is worse than terse text.
  echo "suitecheck: $regress suite(s) BELOW BASELINE — the count went down ($total_ok checks ok, $total_fail reported failures)"
fi
if [ "$bad_suites" -ne 0 ] || [ "$regress" -ne 0 ]; then
  exit 1
fi

echo "suitecheck: $count suites held, $total_ok checks ok, 0 reported failures, none below baseline"
echo "Note: this proves the script tests ran and passed, NOT that the Go tests do — no toolchain here."

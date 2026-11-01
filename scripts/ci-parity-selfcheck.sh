#!/bin/sh
# ci-parity-selfcheck — prove ci-parity-check.sh can still FAIL.
#
# WHY. On 2026-10-22 ci-parity-check (the guard asserting "preflight covers every
# CI step") was wired into CI. Then a probe showed it has a blind spot: a ci.yml
# whose ONLY step is `make ci-parity-check` PASSES, because ci-parity-check is a
# Makefile target and its own recipe lands in the covered text. The guard counts
# itself as coverage. It cannot gate its own removal without help.
#
# This script builds throwaway repos and asserts the guard's verdict in each:
#   A. CI step preflight never reaches          -> must FAIL
#   B. CI = the guard only, preflight ignores it -> must FAIL
#   B2. CI = the guard only, and preflight reaches it ONLY through `lint:`'s
#       prerequisites -> must FAIL. This is the precise hole that fooled it:
#       the guard citing itself via a chain it is supposed to be verifying.
#   B3. CI = the guard only, preflight invokes it DIRECTLY -> must PASS
#       (must not over-fire once attribution is correct)
#   C. CI step preflight does reach              -> must PASS
#   D. empty CI command set                      -> must exit 2 (blind, not "clean")
#
# Case C is what stops this being a script that just always fails. Case D is what
# stops a broken parse from reading as a pass — the guard already refuses when it
# finds no commands, and that refusal is load-bearing.
#
# Exit: 0 all four behaved · 1 a case misbehaved.

set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
pass=0
fail=0

# probe NAME WANT_EXIT CI_RUNLINE PREFLIGHT_EXTRA [LINT_DEPS]
probe() {
    name=$1 want=$2 runline=$3 pfextra=$4 lintdeps=${5:-}
    d=$(mktemp -d)
    mkdir -p "$d/.github/workflows" "$d/scripts"
    cp "$HERE/ci-parity-check.sh" "$d/scripts/"
    # minimal preflight: one target it does invoke, optionally the guard itself
    {
        printf '#!/bin/sh\n'
        printf 'say "scan"\nmake publish-scan\n'
        [ -n "$pfextra" ] && printf '%s\n' "$pfextra"
    } > "$d/scripts/preflight.sh"
    # minimal Makefile with the targets in play
    {
        printf 'publish-scan:\n\t./scripts/publish-scan.sh\n\n'
        printf 'ghost-target:\n\t@echo ghost\n\n'
        printf 'ci-parity-check:\n\t./scripts/ci-parity-check.sh\n\n'
        printf 'ci-parity-selfcheck:\n\t./scripts/ci-parity-selfcheck.sh\n\n'
        printf 'lint: %s\n\t@echo lint\n' "$lintdeps"
    } > "$d/Makefile"
    # a publish-scan script so the "reached" case has something real
    printf '#!/bin/sh\nexit 0\n' > "$d/scripts/publish-scan.sh"
    printf '%.0s' > /dev/null
    if [ "$runline" = "__NONE__" ]; then
        printf 'jobs:\n  lint:\n    steps:\n      - name: no commands here\n        run: echo hello\n' \
            > "$d/.github/workflows/ci.yml"
    else
        printf 'jobs:\n  lint:\n    steps:\n      - name: probe\n        run: %s\n' "$runline" \
            > "$d/.github/workflows/ci.yml"
    fi
    out=$( cd "$d" && sh scripts/ci-parity-check.sh 2>&1 )
    rc=$?
    if [ "$rc" -eq "$want" ]; then
        echo "ok   [$name] (exit $rc)"
        pass=$((pass+1))
    else
        echo "FAIL [$name]: exit $rc, wanted $want"
        printf '%s\n' "$out" | sed 's/^/    /'
        fail=$((fail+1))
    fi
    rm -rf "$d"
}

# A. a CI step preflight never reaches -> FAIL (1). This is the drift it exists for.
probe "unreached CI step -> FAIL" 1 "make ghost-target" ""

# B. CI contains only the guard, and preflight does not invoke it. Must FAIL.
probe "CI = guard only, preflight ignores it -> FAIL" 1 "make ci-parity-check" ""

# B2. THE BLIND SPOT. CI is only the guard; preflight invokes `lint`, and `lint`
#     lists ci-parity-check as a prerequisite. Before the fix this PASSED —
#     the guard counting itself as coverage through the chain it verifies.
probe "CI = guard only, reached via lint: -> FAIL" 1 "make ci-parity-check" "" "ci-parity-check"

# B3. Same CI, but preflight now invokes the guard directly. Must PASS, or the
#     attribution above is just a permanent veto and the guard is furniture.
probe "CI = guard only, preflight invokes it directly -> PASS" 0 "make ci-parity-check" "make ci-parity-check" "ci-parity-check"

# C. a CI step preflight DOES reach -> PASS (0). Stops the script being a
#    always-fails checker, which is furniture with extra steps.
probe "reached CI step -> PASS" 0 "make publish-scan" ""

# D. no CI commands found -> exit 2 (blind), never a silent 0
probe "no CI commands -> blind exit 2" 2 "__NONE__" ""

echo
echo "ci-parity-selfcheck: $pass behaved, $fail misbehaved"
[ "$fail" -eq 0 ] || exit 1

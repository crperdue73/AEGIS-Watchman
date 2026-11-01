#!/bin/sh
# ci-parity-check — assert `make preflight` still runs what CI runs.
#
# scripts/preflight.sh promises, in its own header, that it runs "the *same
# targets*" every CI job runs, so a green local run and a green CI run mean the
# same thing. That promise is prose. If a CI job gains a step and preflight does
# not, "push-ready" quietly stops meaning what it says — the class that already
# bit once, when the rule-pack pin drifted red on the first CI run while every
# local claim was individually true.
#
# So it gets a check. For every command a CI job runs, this fails unless it is
# reachable from preflight:
#   - a `make T` must be a target preflight invokes, or a (transitive)
#     prerequisite of one;
#   - any other command must appear in preflight, in a Makefile recipe, or in a
#     script preflight calls.
#
# It is deliberately lenient in one direction — a command that appears in *any*
# Makefile recipe counts — so it will not fail a fine tree for the wrong reason.
# The drift it exists to catch is "this command is run nowhere in the local
# path," which leniency does not hide.
#
# Usage: scripts/ci-parity-check.sh    (exit 0 = preflight covers CI)
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/.." && pwd)
cd "$repo"

ci=.github/workflows/ci.yml
pf=scripts/preflight.sh
mk=Makefile

[ -f "$ci" ] || { echo "ci-parity-check: no $ci" >&2; exit 2; }
[ -f "$pf" ] || { echo "ci-parity-check: no $pf" >&2; exit 2; }
[ -f "$mk" ] || { echo "ci-parity-check: no $mk" >&2; exit 2; }

# --- what preflight invokes, and everything those targets depend on ----------
pf_targets=$(grep -oE 'make [a-z][a-z0-9-]*' "$pf" | awk '{print $2}' | sort -u | tr '\n' ' ')
[ -n "$pf_targets" ] || { echo "ci-parity-check: preflight invokes no make targets — this check is blind" >&2; exit 2; }

# A GUARD MUST NOT COUNT ITSELF AS COVERAGE — but it may be covered by preflight.
#
# On 2026-10-22 a ci.yml whose ONLY step was `make ci-parity-check` PASSED this
# check, because `lint:` lists ci-parity-check as a prerequisite and `lint` is a
# step: the transitive closure handed the guard back to itself. The guard proving
# "CI is covered" by citing the guard.
#
# The fix is NOT to blanket-exclude it (first attempt, wrong: it made the real
# tree fail even after preflight began invoking it, because exclusion can't tell
# "invoked by preflight" from "reached through lint"). The fix is an attribution:
# ci-parity-check counts as covered ONLY if preflight invokes it DIRECTLY. It is
# removed from the seed, it never re-enters via a dependency edge, and it is then
# re-admitted iff `make ci-parity-check` appears in preflight's own text.
covered=""
for t in $pf_targets; do
    [ "$t" = ci-parity-check ] || covered="$covered $t"
done
changed=1
while [ "$changed" = 1 ]; do
    changed=0
    for t in $covered; do
        deps=$(awk -v t="$t" '$0 ~ "^" t ":" { sub(/^[^:]*:/, ""); gsub(/\\/, ""); print }' "$mk")
        for d in $deps; do
            [ "$d" = ci-parity-check ] && continue
            case " $covered " in
                *" $d "*) ;;
                *) covered="$covered $d"; changed=1 ;;
            esac
        done
    done
done

# Re-admit the guard only on a direct invocation in preflight.
if grep -qE '^[[:space:]]*make[[:space:]]+ci-parity-check([[:space:]]|$)' "$pf"; then
    covered="$covered ci-parity-check"
    guard_direct=1
else
    guard_direct=0
fi

# --- the covered text: preflight, every Makefile recipe, the scripts it calls -
scripts_called=$(grep -oE 'scripts/[A-Za-z0-9._-]+\.(sh|py)' "$pf" | sort -u)
covered_text=$(
    cat "$pf"
    # Makefile recipes, with the $(GO)/$(PYTHON) variables resolved so a CI
    # `go …` can match a recipe that writes `$(GO) …`.
    awk '/^\t/{print}' "$mk" | sed -e 's/\$(GO)/go/g' -e 's/\$(PYTHON)/python3/g'
    for s in $scripts_called; do
        [ -f "$s" ] && cat "$s"
    done
)

# --- every command CI runs: `run: <cmd>` and the lines inside `run: |` blocks -
reqf=$(mktemp)
trap 'rm -f "$reqf"' EXIT
sed -E 's/^[[:space:]]*run:[[:space:]]*//' "$ci" \
    | sed -E 's/^[[:space:]]+//' \
    | grep -E '^(make [a-z][a-z0-9-]*|go [a-z][a-zA-Z0-9._/-]*( .*)?|\./[A-Za-z0-9._/-]+( .*)?)$' \
    | sort -u >"$reqf"

[ -s "$reqf" ] || { echo "ci-parity-check: found no CI commands — this check is blind" >&2; exit 2; }

status=0
while IFS= read -r cmd; do
    case "$cmd" in
        "make "*)
            t=${cmd#make }
            case " $covered " in
                *" $t "*) printf '  ok       %s\n' "$cmd" ;;
                *) printf '  MISSING  %s (preflight reaches no target %s)\n' "$cmd" "$t" >&2; status=1 ;;
            esac
            ;;
        *)
            if printf '%s\n' "$covered_text" | grep -qF -- "$cmd"; then
                printf '  ok       %s\n' "$cmd"
            else
                printf '  MISSING  %s (run nowhere in the local path)\n' "$cmd" >&2
                status=1
            fi
            ;;
    esac
done <"$reqf"

if [ "$status" != 0 ]; then
    echo "ci-parity-check: FAIL — preflight no longer covers every CI step" >&2
    exit 1
fi
echo "ci-parity-check: PASS — every CI step is reachable from make preflight"

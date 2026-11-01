#!/bin/sh
# blindcheck.sh — the last 56 values in the pack now have a reader.
# Run: scripts/blindcheck.sh   Exit 0 = every fixture agrees with its table.
set -e
cd "$(dirname "$0")/.."
python3 scripts/fieldcheck.py >/dev/null || { echo "fieldcheck: FAILED to read the pack"; exit 2; }
# Beat 632: this printed a bare PASS. blindcheck.py above prints a full denominator -- how many of
# the pack's match values are read by a sample-driven check and how many by nothing -- and this wrapper
# swallowed none of it but reported a verdict with no reach of its own. A reader could not tell 115
# values from 5. The count is lifted from the child's own output rather than recomputed here, so there
# is one source of truth; if the shape changes, the fallback says so instead of inventing a number.
out="$(python3 scripts/blindcheck.py)"
rc=$?
printf '%s\n' "$out"
n_vals="$(printf '%s\n' "$out" | sed -n 's/.*read by nothing:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1)"
n_read="$(printf '%s\n' "$out" | sed -n 's/.*read by a sample-driven check:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -1)"
if [ $rc -eq 0 ]; then
    if [ -n "$n_vals" ] && [ -n "$n_read" ]; then
        printf 'blindcheck.sh: PASS -- %s of %s pack match values read by a sample-driven check, %s by nothing (5 defects asserted, not fixed)\n' \
            "$n_read" "$((n_read + n_vals))" "$n_vals"
    else
        echo "blindcheck.sh: PASS verdict, but the denominator could not be read from blindcheck.py output -- treating the count as UNSTATED" >&2
    fi
fi
exit $rc

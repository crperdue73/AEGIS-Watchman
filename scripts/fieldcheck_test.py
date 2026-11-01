#!/usr/bin/env python3
"""fieldcheck_test — pin fieldcheck's denominator, so its headline cannot drift.

The number this file protects is the one that makes fieldcheck worth running: how much of the
pack a sample-driven check actually reads. If someone adds a new predicate family to the pack,
or wires path_glob_any into a guard, this test's expectations go stale — and saying so is the
point. Adjust them ON PURPOSE, with the reason, never to make the suite green.

Checks:
  1. The argv_any sample-driven count equals packcheck's own count (39). Two tools, one number.
  2. path_glob_any has a REACH-strength instrument (packcheck) and is NOT counted as LIVE.
     Until beat 586 this check asserted `is None` — it pinned "read by nobody," which was never a
     measurement, only a missing dict entry.
  3. The blind share is printed and is a real fraction, not 0% or 100%.
  4. The pack parses to a non-zero number of key blocks (guard against a silent empty parse).

Exit 0 only if all hold.
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fieldcheck  # noqa: E402

ok_n = fail_n = 0


def check(name, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f"  ok    {name} -> {got}")
    else:
        fail_n += 1
        print(f"  FAIL  {name}: got {got}, want {want}")


def main():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fieldcheck.main()
    out = buf.getvalue()
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in ("argv_any", "path_glob_any", "endpoint_glob_any", "exe_any", "detail_equals"):
            rows[parts[0]] = int(parts[1])

    check("fieldcheck exits 0 (it is a report)", rc, 0)
    check("argv_any sample-driven count", rows.get("argv_any"), 39)
    # These two assertions USED to demand `is None` -- they pinned the lie. "path_glob_any is read by
    # nobody" was never a measurement, it was the absence of a dict entry. Beat 586: 62 values across
    # the pack's two biggest families do have instruments (packcheck, endpointfixture). Assert the
    # real strength, and assert it is NOT counted as LIVE -- that direction matters more than "yes".
    check("path_glob_any has an instrument and it is REACH-strength",
          str(fieldcheck.CHECKED_BY.get("path_glob_any", "")).startswith("REACH"), True)
    check("endpoint_glob_any has an instrument and it is REACH-strength",
          str(fieldcheck.CHECKED_BY.get("endpoint_glob_any", "")).startswith("REACH"), True)
    check("  ...and REACH is not counted in the LIVE numerator",
          [fieldcheck.SAMPLE_DRIVEN("path_glob_any"),
           fieldcheck.SAMPLE_DRIVEN("endpoint_glob_any")], [False, False])
    check("key blocks parsed > 0", len(rows) > 0, True)
    reach = rows.get("path_glob_any", 0) + rows.get("endpoint_glob_any", 0)
    check("non-LIVE column is a real fraction", 0 < reach < sum(rows.values()), True)
    print(f"\n  non-LIVE share: {reach} of {sum(rows.values())} values are held by keys no "
          f"sample-driven guard reads (they are REACH-checked, not blind — do not quote as unread)")

    print(f"\nfieldcheck_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

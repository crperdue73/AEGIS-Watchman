#!/usr/bin/env python3
"""typecheck_test — pin typecheck's known answers and its honest gaps.

What this pins, and why each line matters:
  * `persist.file_write` reads detail['path'] but FileWrite never emits 'path' for a rename — the
    one confirmed dead wire, asserted by name so it cannot quietly go green.
  * `container.host_root_mount` still cannot be judged by this file: typecheck reads keyed access
    (`detail["k"]`) and the engine reads a map in a range loop, so the check reports the read as
    unverified rather than as OK. Pinned as a known blind spot.
  * the three `secret.*` file.access rules are reported NO-COLLECTOR, because the eBPF adapter
    that would emit file.access is a documented stub (ebpf_linux.go returns "not yet implemented",
    R2.2). That is not a defect in the pack; it is the P2.0 toolchain gate, and pinning it keeps
    it from being re-reported as news every week.
  * three rules carry no field predicate at all (prior_event only); they are counted, not skipped.

Exit 0 only if all hold.
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import typecheck  # noqa: E402

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
        rc = typecheck.main()
    out = buf.getvalue()

    check("rule count parsed", out.count("carbonato.") >= 20, True)
    check("wires checked", "12 predicate/type wire(s) checked" in out, True)

    # the confirmed dead wire
    line = [l for l in out.splitlines() if "carbonato.persist.file_write" in l]
    check("persist.file_write is MISSING-FIELD", bool(line) and "MISSING-FIELD" in line[0], True)
    check("...and names the field it wants", bool(line) and "detail['path']" in line[0], True)
    check("...and lists what FileWrite does emit",
          bool(line) and "'exe'" in line[0] and "'watch'" in line[0], True)

    # the known blind spot, asserted as blind
    mount = [l for l in out.splitlines() if "container.host_root_mount" in l]
    check("host_root_mount reported via map access, not as OK",
          bool(mount) and "<key from rule>" in mount[0], True)

    # the doc-stub gate, named
    check("file.access rules reported NO-COLLECTOR (eBPF stub), not as a pack defect",
          out.count("no dispatcher in internal/telemetry emits FileAccess") , 3)

    # predicate-less rules counted, not skipped
    check("predicate-less rules counted", out.count("NO-PREDICATE"), 4)

    # exit status must follow the finding, and the finding must be non-zero
    check("exit is 1 while a real wire is dead", rc, 1)

    print(f"\ntypecheck_test: {ok_n} ok, {fail_n} fail")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())

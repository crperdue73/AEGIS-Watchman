#!/usr/bin/env python3
"""appendcost — every append past the cap rewrites the entire spool, inside the poll loop.

THE QUESTION I HAD NOT ASKED
----------------------------
Beats 659/670/671 asked whether the spool *holds* things and whether it *makes progress*.
All three treated `Append` as a cheap, local operation whose only failure mode is the
disk. Nobody asked what Append costs when the spool is already at its limit.

THE MECHANISM (measured from source, comments stripped)
-------------------------------------------------------
    Append (spool.go:60-85):
        if !enabled || len(events)==0 { return nil }
        MkdirAll; OpenFile(O_APPEND|O_CREATE|O_WRONLY)
        write(line + "\n"); f.Sync()
        return s.trim()          <-- ALWAYS, on every successful append

    trim (spool.go:236-256):
        st, _ := os.Stat(path)
        if st.Size() <= s.maxBytes { return nil }      <-- cheap exit, good
        recs, _ := s.read()                            <-- O(n): full decode
        for len(recs) > 1 {                            <-- O(n^2) in the worst case:
            for _, r := range recs { json.Marshal(r) }     re-marshals every record,
            if size <= max { break }                        for every record dropped
            recs = recs[1:]
        }
        rewrite(recs)                                  <-- O(n) write + fsync + RENAME

So once the spool is over `maxBytes`, EVERY append does: full O_APPEND write + fsync,
then a full read + decode of every record, then a quadratic re-marshal loop, then a
full rewrite to a temp file, fsync, and rename. The cheap path exists and is correct
(`st.Size() <= s.maxBytes`), but it is the path the spool is *designed* to leave: the
whole point of trim is to run when over the limit.

WHY IT MATTERS, AND WHY IT IS NOT JUST SLOW
-------------------------------------------
Append is called from inside Poll (behavior.go:417-424), synchronous, before drain. So
trim's cost is paid on the poll path. With `DefaultSpoolMaxBytes = 16 MiB` (beat 671) and
a 2520-event record (beat 661), a wedged-but-not-dropping spool can sit at the cap with
thousands of records. Every 60s poll then re-reads, re-decodes, re-marshals, rewrites and
fsyncs megabytes of data it is about to partially discard anyway.

And it is the exact coupling beat 674 named, one layer over:
  - 674: a READ error stops Poll before drain.
  - here: Append's own trim work is ON the poll path, so a slow spool slows the sensor
    loop, and a large spool slows it further. The cost grows precisely when the system is
    already behind — the worst possible time to add latency to the recovery path.

Also worth naming: `rewrite` is not free of risk. It is tmp -> write -> Sync -> Close ->
Rename (spool.go:200-225), which is the correct atomic pattern, BUT trim runs it on every
append while a concurrent Drain may be reading the same path. I did NOT measure the race;
I am recording it as an unmeasured suspicion, not a finding (see NOT CLAIMED).

CLAIM / NOT CLAIMED
-------------------
CLAIMED (from source): Append always calls trim after a successful write; trim returns
    cheaply when under the cap; when over the cap trim does a full read+decode, a
    re-marshal loop nested inside a drop loop (quadratic), and a full rewrite with
    rename; Append is synchronous and is called from Poll before drain.
NOT CLAIMED: any measured latency number. I have no Go toolchain and did not benchmark;
    the O(n^2) is from the nesting in the source, not from a timer. **NOT CLAIMED: the
    trim/Drain race.** **NOT CLAIMED: that this causes event loss** -- trim drops the
    OLDEST records by design and beats 659/671 already covered that; this finding is
    about cost on the poll path, not correctness.
"""

import sys
import pathlib
import re


def strip_comments(src):
    """A mechanism is an identifier, not a sentence (beat 669)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    src = re.sub(r"\s//\s.*$", "", src, flags=re.M)
    return src


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        raw = (root / "internal/collector/spool.go").read_text()
        beh = strip_comments((root / "internal/collector/behavior.go").read_text())
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1
    code = strip_comments(raw)

    app = code[code.index("func (s *Spool) Append("):]
    app = app[:app.index("\n}\n")]
    trim = code[code.index("func (s *Spool) trim()"):]
    trim = trim[:trim.index("\n}\n")]
    rew = code[code.index("func (s *Spool) rewrite("):]
    rew = rew[:rew.index("\n}\n")]

    print("-- Append pays for trim on every successful write --")
    check("Append ends by calling s.trim()", app.rstrip().endswith("return s.trim()"))
    check("Append fsyncs before trim (so the cost is after a durable write)",
          "f.Sync()" in app and app.index("f.Sync()") < app.index("s.trim()"))
    check("the cheap path in trim exists and is correct",
          "st.Size() <= s.maxBytes" in trim and "return nil" in trim)

    print("\n-- the expensive path: full read, quadratic loop, full rewrite --")
    check("trim does a full s.read() (decode every record)", "s.read()" in trim)
    check("trim drops from the front in a loop", "recs = recs[1:]" in trim)
    check("an inner loop RE-MARSHALS every surviving record, inside the drop loop",
          trim.count("json.Marshal(r)") == 1
          and trim.index("json.Marshal(r)") > trim.index("for len(recs) > 1")
          and trim.index("json.Marshal(r)") < trim.index("recs = recs[1:]"))
    check("trim ends in a full rewrite", "s.rewrite(recs)" in trim)
    # test the PROPERTY (write a temp, fsync, close, rename over the real path), not the
    # NAME of the constructor -- my first version asserted "CreateTemp" and went red on
    # correct code just because the temp file is opened differently (beat 661's rule).
    check("rewrite writes a TEMP file and renames it over the real path (atomic)",
          "Rename(" in rew and "s.path" in rew.split("Rename(")[1][:40]
          and "O_CREATE" in rew or "Create(" in rew)
    # `rew.index("Close()")` finds the FIRST Close, which is an ERROR-path f.Close() inside
    # the write loop -- so the naive ordering test compared Sync against the wrong call and
    # went red on correct code. Anchor on the LAST occurrences, which are the happy path.
    check("rewrite fsyncs the temp file before its final close, which precedes the rename",
          rew.index("Sync()") < rew.rindex("Close()") < rew.index("Rename("))

    print("\n-- and it is on the poll path, before drain --")
    check("Poll calls spool.Append", "b.spool.Append(" in beh)
    check("Append is called BEFORE drain() in Poll  <-- cost lands on the shipping loop",
          beh.index("b.spool.Append(") < beh.index("b.drain()"))

    print("\n-- the cap and the record size that set the magnitude --")
    check("DefaultSpoolMaxBytes is 16 MiB", "DefaultSpoolMaxBytes = 16 << 20" in code)
    print("   (beat 661: one spool record can hold 2520 match events; 16 MiB holds ~40,000)")

    if fails:
        print(f"\nAPPENDCOST RED: {fails}")
        return 1

    print("\n=> Append is not a cheap append. Past the cap, every single append does a full")
    print("   read+decode, a re-marshal loop nested in the drop loop, and a full rewrite")
    print("   with rename -- synchronously, inside Poll, before drain. The cost grows as the")
    print("   backlog grows, i.e. it is greatest exactly when the collector is already behind.")
    print("   NOT claimed: a latency number (no toolchain, no benchmark), nor any loss.")
    print("   Fix, Dad's call: (1) trim on a size RATIO, not on every append -- drop to 75%")
    print("   of the cap so the next N appends take the cheap path; (2) drop-by-bytes instead")
    print("   of re-marshalling the survivors each iteration (compute once, walk once); (3)")
    print("   move trim off the poll path entirely (after drain, or on its own cadence).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

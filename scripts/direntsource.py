#!/usr/bin/env python3
"""direntsource — observe() asks "does this path exist?"; Actions() needs "can I READ it?"

THE QUESTION
------------
Beat 664 found sourceWatch tracks presence, not progress (`os.Stat` once per poll, comparing
two bools). Beat 674 found an Actions() error stops delivery of events already on disk. Both
treated "present" as meaning "readable". It does not.

    observe()  -> os.Stat(w.path); present := err == nil
    Actions()  -> os.Open(f.path); then fh.Stat(); then Seek; then ReadFull

os.Stat and os.Open answer DIFFERENT questions, and on Linux there is a case where the first
says yes and the second gets as far as a read before failing: a DIRECTORY at the log path.

MEASURED ON THIS HOST (python, equivalent syscalls; see NOT CLAIMED)
-------------------------------------------------------------------
    /tmp/dirtest/audit.log  is a directory
        os.stat(path)              -> OK   (is_dir=True, size=40)   <- observe(): PRESENT
        open(path, O_RDONLY)       -> OK   fd 3                     <- Actions(): no early err
        os.fstat(fd)               -> size=40                       <- size := 40
        os.lseek(fd, 0, SEEK_SET)  -> OK                            <- Seek ok
        os.read(fd, 40)            -> EISDIR (errno 21)             <- io.ReadFull -> err

So the sequence in Actions() completes everything up to the read and then returns an error
that is NOT IsNotExist. `Actions()` returns `nil, err`; per beat 674 that error returns from
Poll ABOVE drain, so already-fsynced events stop shipping.

Meanwhile observe() sees present, unchanged, and says nothing -- no transition, no warning.

WHY THIS IS WORSE THAN THE USUAL "NODE" CASE
--------------------------------------------
Every real-world way auditd's log path becomes a directory is a mistake someone made on
purpose-adjacent work: a volume mount point created before the mount landed, an extraction
that produced a directory of that name, a tree restored from a backup with the wrong shape.
It is exactly the state where an operator is ALREADY confused, and the tool's answer is a
green presence check plus a silently stalled pipeline -- the failure shape collected in beats
662-676: the screen that looks the same as a quiet healthy host.

I am NOT claiming this is common. I am claiming that the monitor's own predicate cannot
distinguish it from health, and that the cost is the one 674 measured.

CLAIM / NOT CLAIMED
-------------------
CLAIMED (measured on this host): Stat succeeds on a directory; Open succeeds; fstat reports a
    size; Seek succeeds; the read fails with EISDIR; therefore Actions() reaches its read and
    returns a non-IsNotExist error, while observe() reports the path as present and quiet.
CLAIMED (from source): observe() uses os.Stat only, compares a bool, and has no error-kind
    branch; Actions() maps only IsNotExist to (nil, nil); Poll returns above drain on error.
NOT CLAIMED: that this occurs in production. **NOT CLAIMED: the permission-denied case** --
    this host runs as root, so a 000-mode file still opened; I measured the directory case and
    left the permission case as a prediction, not a finding. **NOT CLAIMED: Go's exact
    syscall sequence** -- transcribed from source, no toolchain (642). The reachability of
    *some* error is what matters; the directory is the one I could measure.
"""

import sys
import pathlib
import re
import os
import tempfile


def strip_comments(src):
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
        beh_raw = (root / "internal/collector/behavior.go").read_text()
        beh = strip_comments(beh_raw)
        eng = strip_comments((root / "internal/telemetry/engine.go").read_text())
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    obs = beh[beh.index("func (w *sourceWatch) observe()"):]
    obs = obs[:obs.index("\n}\n")]
    act = eng[eng.index("func (f *FileSource) Actions("):]
    act = act[:act.index("\n}\n")]

    print("-- observe() asks the weaker question --")
    check("observe() uses os.Stat and nothing else", "os.Stat(w.path)" in obs)
    check("observe() never opens the file", "os.Open" not in obs and "ReadFile" not in obs)
    # PROPERTY, not text: is `present` derived ONLY from Stat's error?
    pres = [ln.strip() for ln in obs.split("\n") if "present :=" in ln]
    print(f"   presence expression: {pres[0] if pres else '*** NOT FOUND ***'}")
    check("observe() derives presence ONLY from Stat's error -- no readability test",
          len(pres) == 1 and pres[0] == "present := err == nil")
    check("observe() compares a BOOL, so it has no notion of 'exists but unreadable'",
          "present == w.present" in obs)
    check("observe() has no error-KIND branch (no IsNotExist / IsPermission anywhere)",
          "IsNotExist" not in obs and "IsPermission" not in obs)

    print("\n-- Actions() asks the stronger question, and fails LATE --")
    check("Actions() opens the path", "os.Open(f.path)" in act)
    # Test the PROPERTY: does the early-error branch map ONLY IsNotExist to (nil,nil)?
    # My first version asserted the text "os.IsNotExist(err)", so adding
    # `|| errors.Is(err, EISDIR)` -- i.e. APPLYING THE FIX -- left it green. A check that
    # cannot fail is not a check.
    cond = ""
    for line in act.split("\n"):
        if "IsNotExist" in line:
            cond = line.strip()
            break
    if not cond:
        print("FAIL no IsNotExist line found in Actions() -- an absent input is not a pass")
        fails += 1
    print(f"   IsNotExist branch condition: {cond or '*** NOT FOUND ***'}")
    check("Actions() maps ONLY IsNotExist to a quiet nil,nil "
          "(so every other error reaches Poll and stops drain)",
          cond.startswith("if ") and "||" not in cond
          and "EISDIR" not in act and "IsDir" not in act)
    check("Actions() stats the OPEN fd (so a directory yields a plausible size)",
          "fh.Stat()" in act)
    check("Actions() seeks and then READS -- the read is where EISDIR lands",
          "fh.Seek(" in act and "io.ReadFull(" in act
          and act.index("fh.Stat()") < act.index("fh.Seek(") < act.index("io.ReadFull("))

    print("\n-- and the error reaches Poll above drain (beat 674's cost) --")
    check("Poll returns on Matches() error before drain()",
          beh.index("return nil") < beh.index("b.drain()"))

    print("\n-- measured live on this host: a DIRECTORY at the log path --")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "audit.log")
        os.mkdir(p)
        try:
            st = os.stat(p)
            stat_ok, size, isdir = True, st.st_size, os.path.isdir(p)
        except OSError:
            stat_ok, size, isdir = False, -1, False
        print(f"   os.stat -> ok={stat_ok} isdir={isdir} size={size}")
        check("Stat SAYS PRESENT for a directory (observe() -> healthy, quiet)", stat_ok and isdir)

        fd = None
        try:
            fd = os.open(p, os.O_RDONLY)
            open_ok = True
        except OSError:
            open_ok = False
        check("Open SUCCEEDS on a directory (so Actions() has no early error)", open_ok)
        if open_ok:
            check("fstat on the dir fd reports a nonzero size (size := N, not 0)",
                  os.fstat(fd).st_size > 0)
            os.lseek(fd, 0, os.SEEK_SET)
            err = None
            try:
                os.read(fd, max(os.fstat(fd).st_size, 1))
            except OSError as e:
                err = e
            os.close(fd)
            print(f"   read -> errno={getattr(err, 'errno', None)} {getattr(err, 'strerror', None)}")
            check("the READ fails with EISDIR -- a non-IsNotExist error",
                  err is not None and err.errno == 21)

    if fails:
        print(f"\nDIRENTSOURCE RED: {fails}")
        return 1

    print("\n=> observe() and Actions() disagree about what 'present' means. On a directory at")
    print("   the log path, observe() reports present+unchanged (silent, by design), while")
    print("   Actions() opens, stats, seeks, and then fails EISDIR -- returning an error that")
    print("   Poll turns into an early return ABOVE drain (674). Present, quiet, and not")
    print("   shipping. NOT claimed: that this happens in production; NOT claimed: the")
    print("   permission case (this host is root, so 000-mode still opened).")
    print("   Fix, Dad's call: (1) make observe() test READABILITY, not existence -- a cheap")
    print("   `fh, err := os.Open(path); if err == nil { _, rerr := fh.Read(1); ... }`, or")
    print("   at minimum `os.Stat(...); if fi.IsDir()` -> a distinct, loud third state; (2)")
    print("   give Actions() a typed error so Poll can distinguish 'unreadable source' from")
    print("   'engine broke' -- the same typed-error fix beat 672 asked for.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

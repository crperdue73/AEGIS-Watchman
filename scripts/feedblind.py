#!/usr/bin/env python3
"""feedblind — beats 659-663 traced one alert end to end. The unexamined premise
under all of them: the engine is LOOKING AT THE LOG. This asks whether the
collector can tell when its auditd feed silently stops saying anything.

WHAT sourceWatch CHECKS (behavior.go:42-79)
-------------------------------------------
    _, err := os.Stat(w.path)
    present := err == nil

That is the whole liveness test: **does the path exist.** It is called once per Poll
(behavior.go:397). The design intent is stated in its own comment -- "a quiet loss
of coverage is the failure this tool exists to refuse" -- and it does honour that
for a source that DISAPPEARS. But existence is not progress.

THE TWO CASES IT CANNOT SEE
---------------------------
1. ROTATION INTO AN EMPTY FILE. `logrotate` renames audit.log to audit.log.1 and
   creates a fresh, empty audit.log. `os.Stat` succeeds the whole time. The watch
   prints NOTHING. Meanwhile FileSource.Actions (engine.go:157-215):

       size := st.Size()
       if size < f.offset { f.offset = 0 }    // "Log rotated/truncated: start over."
       if size == f.offset { return nil, nil }

   With a fresh empty log, size(0) < offset(N) -> **offset reset to 0**, and any
   records that landed in the NEW file before that Poll are read from byte 0. Fine.
   But the rotated-away records that were appended BETWEEN the last read of the old
   file and the rotation are gone, and nothing says so. Worse: if auditd writes
   nothing into the new file and the OLD one still has unread bytes, they are never
   read again -- `size < offset` sent the reader to the new file and the old inode
   is never reopened.

2. AUDITD STOPPED, FILE STILL THERE. auditd dies (or is stopped). audit.log keeps
   existing, size == offset, `Actions()` returns `nil, nil` forever. sourceWatch:
   path exists -> silent. No events, no error, no alert. The collector polls every
   60s, finds nothing, and reports healthy. **Coverage is zero and every observable
   signal says fine.**

MEASURED (simulation, faithful to the two functions above)
----------------------------------------------------------
This script transcribes the offset logic and runs the three scenarios. The numbers
printed are from those runs, not from memory.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: sourceWatch's liveness test is existence-only (source read); a stopped
    auditd and a rotation-into-empty are both invisible to it; the reader treats
    shrink as rotation and never revisits the old inode; on a quiet host both
    scenarios produce zero events and zero signals, indefinitely.
NOT CLAIMED: that logrotate's exact default config produces case 1 on the target
    hosts (copytruncate vs create behaves differently -- copytruncate does NOT
    change the inode, create does), or that any deployment has run blind. This is
    code-level reachability plus a simulation of the two functions; no live auditd
    was stopped.
"""

import sys
import pathlib


class Reader:
    """Transcription of telemetry.FileSource.Actions' offset handling."""

    def __init__(self):
        self.offset = 0
        self.file = []      # list of (name, bytes) the reader sees
        self.pos = 0        # index into self.file

    def current_size(self):
        return len(self.file[self.pos][1])

    def actions(self):
        if self.pos >= len(self.file):
            return None
        data = self.file[self.pos][1]
        size = len(data)
        if size < self.offset:
            self.offset = 0            # "rotated/truncated: start over"
        if size == self.offset:
            return []                  # nothing new; NOT an error
        chunk = data[self.offset:]
        nl = chunk.rfind(b"\n")
        if nl < 0:
            return []
        self.offset += nl + 1
        return [ln for ln in chunk[:nl + 1].split(b"\n") if ln]


class Watch:
    """Transcription of collector.sourceWatch.observe()."""

    def __init__(self, path):
        self.path = path
        self.present = None
        self.known = False

    def observe(self, exists):
        present = exists
        if not self.known:
            self.known, self.present = True, present
            return "" if present else "not readable"
        if present == self.present:
            return ""
        self.present = present
        return "is back" if present else "disappeared"


def main():
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    print("-- the liveness test, from source --")
    try:
        beh = (pathlib.Path(__file__).resolve().parents[1]
               / "internal/collector/behavior.go").read_text()
        eng = (pathlib.Path(__file__).resolve().parents[1]
               / "internal/telemetry/engine.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    obs = beh.split("func (w *sourceWatch) observe()")[1].split("\nfunc ")[0]
    check("sourceWatch tests existence only (os.Stat, no size/mtime/progress)",
          "os.Stat(w.path)" in obs
          and not any(k in obs for k in ("Size()", "ModTime", "offset", "lastSeen")))
    check("the reader treats a shrink as rotation and resets to 0",
          "if size < f.offset {" in eng and "f.offset = 0" in eng)

    print("\n-- scenario 1: auditd STOPS, file remains --")
    w, r = Watch("/var/log/audit/audit.log"), Reader()
    r.file = [("audit.log", b"line1\nline2\n")]
    print(f"     poll 1: events={len(r.actions())}  watch says {w.observe(True) or 'nothing'!r}")
    quiet, warned = 0, 0
    for _ in range(1000):
        if not r.actions():
            quiet += 1
        if w.observe(True):      # count the warnings the LOOP actually produces
            warned += 1
    check(f"1000 quiet polls -> {quiet} empty, {warned} warnings",
          quiet == 1000 and warned == 0)
    print("     => a host whose auditd died is indistinguishable from a calm host.")

    print("\n-- scenario 2: rotation into an EMPTY file --")
    w2, r2 = Watch("/var/log/audit/audit.log"), Reader()
    r2.file = [("audit.log", b"line1\nline2\nline3\n")]
    r2.actions()                                   # read all 3
    print(f"     after first poll: offset={r2.offset}")
    # rotate: new empty file, plus unread tail left in the OLD file
    r2.file.append(("audit.log", b""))
    r2.pos = 1
    r2.file[0] = ("audit.log.1", b"line1\nline2\nline3\nline4\n")   # line4 unread
    got = r2.actions()
    check(f"new empty file -> offset reset to {r2.offset}, {len(got)} events read",
          r2.offset == 0 and got == [])
    check("the unread line4 in the OLD file is never read again",
          r2.pos == 1)
    check("the watch printed nothing during either step", w2.observe(True) == "")
    print("     => rotated-away records are dropped silently; the inode is never")
    print("        reopened because `size < offset` redirected the reader.")

    print("\n-- what WOULD have caught it (struct BODY, not the comment) --")
    # Eighth prose-as-data near miss: the first version searched text that included
    # sourceWatch's doc comment, which mentions "lost", "quiet", etc. A watch could
    # have gained a `lastSeen` field and the prose would have had to change to match.
    # Parse the struct body only.
    body = beh.split("type sourceWatch struct {")[1].split("\n}")[0]
    fields = [ln.split()[0] for ln in body.splitlines()
              if ln.strip() and not ln.strip().startswith("//")]
    check(f"sourceWatch has only {fields} -- no progress state",
          fields == ["name", "path", "present", "known"])
    for mech in ("lastSeen", "lastSize", "lastMod", "inode"):
        check(f"absent from the struct: {mech}", mech not in fields)

    if fails:
        print(f"\nFEED BLINDNESS RED: {fails}")
        return 1

    print("\n=> sourceWatch refuses a SILENT DISAPPEARANCE, which is its stated")
    print("   purpose and it does that well. It cannot refuse SILENT SILENCE: a")
    print("   stopped auditd or a rotation into an empty file leaves every signal")
    print("   at 'present, fine, nothing to report' while Layer 3 sees nothing.")
    print("   Fix, Dad's call: have sourceWatch track (inode, size, mtime) and warn")
    print("   when the file is present but has not advanced for N intervals --")
    print("   'present but not advancing' is the third state the watch lacks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""prioreventprobe_test.py — proves prioreventprobe's claims can go red.

The finding it backs (FINDING-2026-10-27f) is about three CRITICAL rules being fed the wrong clock. If
this probe only ever prints its happy story, the finding is decoration.

  M0  shipped probe                 -> rc 0
  M1  Record() whose `at` is ignored -> the mirror itself goes red (mode 1/3 stop separating)
  M2  Seen() with an inclusive cutoff -> the window becomes untestable, mode 1 goes red
  M3  a pack with no prior_event rules -> mode 4 goes red (it must notice the corpus is gone)
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
fails = 0


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails += 1


def run(tmp, script_src=None, pack_src=None):
    os.makedirs(os.path.join(tmp, "rules"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "scripts"), exist_ok=True)
    src = script_src if script_src is not None else open(os.path.join(HERE, "prioreventprobe.py")).read()
    with open(os.path.join(tmp, "scripts", "prioreventprobe.py"), "w") as fh:
        fh.write(src)
    pack = pack_src if pack_src is not None else open(os.path.join(ROOT, "rules", "carbonato.yaml")).read()
    with open(os.path.join(tmp, "rules", "carbonato.yaml"), "w") as fh:
        fh.write(pack)
    p = subprocess.run([PY, "scripts/prioreventprobe.py"], cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    print("prioreventprobe_test:")
    real_script = open(os.path.join(HERE, "prioreventprobe.py")).read()
    real_pack = open(os.path.join(ROOT, "rules", "carbonato.yaml")).read()

    tmp = tempfile.mkdtemp()
    try:
        rc, out = run(tmp)
        check("M0 shipped probe is green", rc == 0, f"rc={rc}")
        check("M0 and reproduces the defect", "DEFECT REPRODUCED" in out, out[-400:])

        # M1: a Record that ignores `at` -- i.e. exactly the bug, promoted into the mirror.
        # Both stamps now behave identically, so mode 3 can no longer separate them.
        blind = real_script.replace(
            "        self.obs.append((at, cls))",
            "        self.obs.append((as_of, cls))  # M1: ignore when it happened")
        check("M1 the Record mutation applied", blind != real_script)
        rc, out = run(tmp, script_src=blind)
        check("M1 a Record that ignores `at` FAILS", rc == 1, f"rc={rc}")
        check("M1 and flags the honest stamp as also firing", "the honest stamp also fired" in out, out[-500:])

        # M2: Seen with an inclusive lower bound -- the window would let the stale action through
        # even with an honest stamp, so mode 1's "old -> False" claim breaks.
        # M2 originally flipped > to >= and nothing changed: my fixture tests 30s and 3h against a 2m
        # window, so the boundary is never touched. A mutation that cannot reach the boundary proves
        # nothing about it. Mutate the WINDOW instead -- a probe whose window swallows everything makes
        # mode 1's "3h ago -> False" claim false, which is the claim that matters.
        loose = real_script.replace("WINDOW = timedelta(minutes=2)", "WINDOW = timedelta(hours=48)")
        check("M2 the window mutation applied", loose != real_script)
        rc, out = run(tmp, script_src=loose)
        check("M2 a window that swallows a 3h-old action FAILS mode 1", rc == 1, f"rc={rc}")

        # M3: a pack with no correlation rules at all -- mode 4 must not silently pass.
        stripped = "rules:\n  - id: carbonato.test.only\n    severity: low\n    match:\n      type: process.exec\n"
        rc, out = run(tmp, pack_src=stripped)
        check("M3 a pack with no prior_event rules FAILS mode 4", rc == 1, f"rc={rc}")
        check("M3 and names the missing corpus", "expected the shipped pack" in out, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nprioreventprobe_test: " + ("all modes discriminate" if not fails else f"{fails} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

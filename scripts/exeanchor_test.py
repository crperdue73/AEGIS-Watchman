#!/usr/bin/env python3
"""exeanchor_test.py — proves exeanchor's four claims can FAIL, not just pass.

The finding built on this script says the "names its target" class is fixable in the pack today. If
exeanchor only ever prints green, that finding is backed by decoration. Each mode is given a world where
its claim is false and must go red.

  M0  shipped pack, shipped script            -> rc 0 (the baseline is not already red)
  M1  pack edited so exe_any holds a glob     -> rc 1, names the rule (mode 4 discriminates)
  M2  engine semantics edited to accept globs -> mode 1 goes red (the trap claim is load-bearing)
  M3  a blanket-anchored split rule -> mode 5 goes red (it must notice the cron drop is lost)

A script whose only failure mode is "the pack is broken" cannot tell me whether my fix works.
"""
import io
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
fails = 0


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        fails += 1


def run_in(tmp, script_src=None, pack_src=None):
    """Build a throwaway tree and run exeanchor there. Returns (rc, output)."""
    os.makedirs(os.path.join(tmp, "rules"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "scripts"), exist_ok=True)
    for dep in ("packcheck.py", "fpprobe.py"):
        shutil.copy(os.path.join(HERE, dep), os.path.join(tmp, "scripts", dep))
    src = script_src if script_src is not None else open(os.path.join(HERE, "exeanchor.py")).read()
    with open(os.path.join(tmp, "scripts", "exeanchor.py"), "w") as fh:
        fh.write(src)
    pack = pack_src if pack_src is not None else open(os.path.join(ROOT, "rules", "carbonato.yaml")).read()
    with open(os.path.join(tmp, "rules", "carbonato.yaml"), "w") as fh:
        fh.write(pack)
    import subprocess
    p = subprocess.run([sys.executable, "scripts/exeanchor.py"], cwd=tmp,
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    print("exeanchor_test:")
    real_pack = open(os.path.join(ROOT, "rules", "carbonato.yaml")).read()
    real_script = open(os.path.join(HERE, "exeanchor.py")).read()

    tmp = tempfile.mkdtemp()
    try:
        rc, out = run_in(tmp)
        check("M0 shipped pack + shipped script is green", rc == 0, f"rc={rc}")

        mutated = real_pack.replace("exe_any: [docker, podman, nerdctl]",
                                    'exe_any: ["*docker*"]')
        check("M1 the mutation applied to the pack", mutated != real_pack)
        rc, out = run_in(tmp, pack_src=mutated)
        check("M1 a glob in exe_any FAILS", rc == 1, f"rc={rc}")
        check("M1 and names the offending rule", "privileged_container" in out, out[-300:])

        # M2: if exeMatches DID take globs, mode 1's trap claim would be false and must go red.
        permissive = real_script.replace(
            "    return term == posixpath.basename(exe)",
            "    import fnmatch\n    return term == posixpath.basename(exe) or fnmatch.fnmatch(exe, term)")
        check("M2 the engine mutation applied", permissive != real_script)
        rc, out = run_in(tmp, script_src=permissive)
        check("M2 an exeMatch that accepts globs FAILS mode 1", rc == 1, f"rc={rc}")
        # M3: a split rule that anchors on the exe for BOTH families -- the naive fix. Mode 5 must notice
        # it loses the cron drop, because that form runs under sh, not crontab.
        greedy = real_script.replace(
            '    via_file = argv_hit(["*>> /etc/cron.d/*", "*/etc/cron.d/*", "*/var/spool/cron/*"], argv, exe)',
            '    via_file = exe_matches("crontab", exe) and argv_hit(["*>> /etc/cron.d/*", "*/etc/cron.d/*"], argv, exe)')
        check("M3 the greedy mutation applied", greedy != real_script)
        rc, out = run_in(tmp, script_src=greedy)
        check("M3 a blanket-anchored split FAILS mode 5", rc == 1, f"rc={rc}")
        check("M3 and names the lost detection", "loses real detections" in out, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nexeanchor_test: " + ("all four modes discriminate" if not fails
                                  else f"{fails} FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

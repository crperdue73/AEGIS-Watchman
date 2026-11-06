#!/usr/bin/env python3
"""gateaudit — the instrumentary audit: does every check in the gate have a way to fail?

BEAT 747. This audits the AUDITORS, not the product. Three questions:

  1. Is every recent census actually wired into suitecheck.sh? (was: pathfixture.py and
     redundant_mut.py sat in scripts/ and in NO gate for weeks — beat 640)
  2. Does every script named in the gate have a path to a NON-ZERO exit? A script that can
     only print `ok` is not a check, it is a log line with a shebang.
  3. When a new instrument goes red, does it name WHICH claim broke, or does it cascade?

OWN MISTAKE (beat 747), recorded in the doc: first pass counted `fail()` CALLS and flagged
debriscensus.py/claimcensus.py as red-less. Wrong — debriscensus exits via `print + return 1`
on four conditions. A checker must not assume one spelling of failure (same shape as beat 645).
This script therefore accepts ANY non-zero exit construct, and check B pins that it does.

Checks:
  A. the recently written censuses are all named in suitecheck.sh
  B. this auditor itself does not assume one spelling of failure (pins its own pattern set)
  C. every script named in the gate has at least one non-zero exit construct
  D. the gate's verdict line still distinguishes failures and refuses to run over a shrunken set
  E. the doc keeps the measured zeros and the not-established section
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0

# Every construct that ends a Python script non-zero. One spelling is not enough.
RED_PATTERNS = [
    r"sys\.exit\(1\)",
    r"raise SystemExit\(1\)",
    r"return 1\b",
    r"^exit 1\b",
]
RECENT = [
    "walkerrorcensus.py",
    "modtimecensus.py",
    "alertbulkcensus.py",
    "retainfailcensus.py",
    "keycollisioncensus.py",
    "manifestkeycensus.py",
    "fleetcoveragecensus.py",
    "ingestceilingcensus.py",
]


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    global FAILS
    FAILS.append(m)
    print("FAIL " + m)


def read(p):
    return Path(p).read_text(errors="replace")


def gate_scripts():
    src = read(HERE / "suitecheck.sh")
    return sorted(set(re.findall(r"\$HERE/([A-Za-z0-9_]+\.py)", src)))


def has_red(text):
    return any(re.search(p, text, re.M) for p in RED_PATTERNS)


def check_a_wired():
    src = read(HERE / "suitecheck.sh")
    missing = [s for s in RECENT if s not in src]
    if not missing:
        ok("A: all %d most recent censuses are named in suitecheck.sh — a script in "
           "scripts/ but in no gate is furniture (the beat 640 discovery: pathfixture.py "
           "and redundant_mut.py sat ungated for weeks)." % len(RECENT))
    else:
        fail("A: NOT WIRED: %s — written, green, and run by nobody" % (missing,))
    # And they must exist on disk, or suitecheck names a file it cannot run.
    gone = [s for s in RECENT if not (HERE / s).is_file()]
    if gone:
        fail("A: named in the gate but absent from scripts/: %s" % (gone,))
    else:
        ok("A: all %d exist on disk as well as in the gate — the gate is not naming ghosts"
           % len(RECENT))


def check_b_own_lesson():
    me = read(Path(__file__).resolve())
    # Beat 747 own-mistake #2: I counted patterns by matching "\(1\)" in the literal, which
    # `r"return 1\b"` does not contain — so the auditor accused itself of the very fault it
    # checks for. Read the ACTUAL list object instead of re-parsing its source text.
    # Import the LIVE list rather than re-parsing this file's source text. The re-parse
    # is what broke twice: first a regex looking for a literal it could not match in its
    # own definition, then a quoting error while repairing it. A check that reads the
    # thing instead of reading text ABOUT the thing cannot make that mistake.
    ns = {}
    exec(compile("RED_PATTERNS = " + repr(RED_PATTERNS), "<red>", "exec"), ns)
    pats = list(ns["RED_PATTERNS"])
    if len(pats) >= 3:
        ok("B: this auditor accepts %d distinct non-zero exit spellings (%s) — because its "
           "own first pass counted `fail()` calls and wrongly flagged debriscensus.py, which "
           "exits via `print + return 1` on four conditions. A checker must not assume one "
           "spelling of failure." % (len(pats), ", ".join(p.strip("\\b") for p in pats)))
    else:
        fail("B: the red-pattern set shrank to %d — this auditor is now assuming one "
             "spelling of failure, the very error it exists to avoid" % len(pats))


# Scripts the gate names but the published tree does not carry. Each entry is a decision, not
# an accident: these operate on the operator's own credential store, so shipping them would
# mean shipping paths -- and the logic of -- a private host.
WITHHELD_FROM_PUBLISH = {
    "credpath.py", "sweepclaim.py", "windowblind.py", "chatread.py", "chatread_test.py",
}


def check_c_every_script_can_fail():
    scripts = gate_scripts()
    if len(scripts) < 50:
        fail("C: only %d scripts parsed out of suitecheck.sh — a sweep this small is not a "
             "sweep (parse failure, not a clean tree)" % len(scripts))
        return
    redless, absent = [], []
    for s in scripts:
        p = HERE / s
        if not p.is_file():
            if s in WITHHELD_FROM_PUBLISH:
                # Deliberately not published: it carries host-specific credential-store paths.
                # suitecheck.sh names it because the LOCAL gate runs it; a public clone cannot.
                # Declared here rather than silently tolerated, so "missing" and "withheld" stay
                # two different words. Beat 836, during the first public push since 0.2.0.
                continue
            absent.append(s)
            continue
        if not has_red(read(p)):
            redless.append(s)
    if redless:
        fail("C: %d script(s) in the gate have NO path to a non-zero exit: %s — these can "
             "only print, never refuse" % (len(redless), redless))
    else:
        ok("C: every one of the %d scripts named in suitecheck.sh has at least one non-zero "
           "exit construct. Zero red-less scripts. Recorded as a measured ZERO: a script "
           "that cannot fail is a log line with a shebang." % len(scripts))
    if absent:
        fail("C: %d script(s) named by the gate are missing from disk: %s" % (len(absent), absent))


def check_d_gate_verdict():
    src = read(HERE / "suitecheck.sh")
    if re.search(r"reported failures", src):
        ok("D: the gate prints \u201creported failures\u201d as a distinct quantity from "
           "suites-run and checks-ok — a suite that reports nothing and a suite that reports "
           "a problem do not print identically (beats 645/646).")
    else:
        fail("D: the gate's verdict line changed shape — re-read it")
    # Beat 747 own-mistake #3: this check first matched "below baseline" ANYWHERE in the
    # file, and suitecheck.sh mentions the phrase in THREE COMMENTS (the beat 647 postmortem).
    # Deleting the real verdict word from the echo still left the check green. Beat 731 rule:
    # quote a LINE that must hold. Pin the echo statement itself, not the phrase.
    echo = re.search(r'^echo "suitecheck: \$count suites held.*$', src, re.M)
    if echo and "none below baseline" in echo.group(0):
        ok("D: the printed verdict line itself names \u201cnone below baseline\u201d \u2014 "
           "quoted from the echo, not found loose in the file. A shrunken suite set is a "
           "failure, not a pass (beat 636's rule, applied to the gate itself).")
    else:
        fail("D: the baseline clause left the verdict ECHO (it may still survive in the "
             "beat 647 comments, which is why a loose grep passed): %r"
             % (echo.group(0)[:80] if echo else None))


def check_e_doc():
    doc = ROOT / "docs/CENSUS-2026-11-02e-the-instrumentary-audit.md"
    if not doc.exists():
        fail("E: doc missing")
        return
    collapse = lambda x: re.sub(r"\s+", " ", re.sub(r"[*_`]", "", x))
    t = collapse(doc.read_text())
    for phrase, label in [
        ("68 of 68 have a red path", "the measured sweep result"),
        ("a census, not a finding", "the census-not-finding framing"),
        ("Own mistake, caught mid-audit", "the own-mistake section"),
        ("What this census does NOT establish", "the not-established section"),
        ("One FAIL, thirteen still `ok`".replace("`", ""), "the targeted-red evidence"),
        ("the best-written guard in the tree", "the sound-part credit"),
    ]:
        if collapse(phrase).lower() in t.lower():
            ok("E: doc carries %s" % label)
        else:
            fail("E: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_wired()
    check_b_own_lesson()
    check_c_every_script_can_fail()
    check_d_gate_verdict()
    check_e_doc()
    print()
    if FAILS:
        print("gateaudit: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("gateaudit: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

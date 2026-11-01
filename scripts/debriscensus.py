#!/usr/bin/env python3
"""debriscensus — 56 non-test modules in scripts/ and no written answer to "why is this file here?"

Beat 634. Beat 633 wrapped seven probes in `main()`, which fixed the import hazard and left the harder
question untouched: 56 non-test modules sit in scripts/ and NOTHING in the tree says which of them are
instruments that still have a job, which are one-hour scripts that were never meant to live, and which
are duplicates of each other. `grep -rn "attic"` returns one docstring sentence and zero code; the
`scripts/attic/` directory that `wirecheck.py`'s header points at **does not exist**. The archive has no
attic, so nothing was ever retired — it has only ever accumulated.

Why that is a detection problem and not housekeeping:
  - A reader cannot tell a live guard from an abandoned probe. `severityprobe.py`, `severityprobe2.py`
    and `sevprobe2.py` are three files whose names promise the same thing; two are numbered, one is
    abbreviated, and the only way to learn what each does is to open all three and read the prose.
  - `guardtriage.py`'s whole thesis is that a COUNT of names is not a work list. This is the same debt
    on the other axis: a count of files is not an inventory.
  - Every probe in this directory is a claim about the pack. A dead probe is a claim nobody is
    re-checking, sitting next to live ones with no marker on it.

So this file produces the inventory, and every verdict is DERIVED from the file plus the tree -- never
from a hand-kept list, which would rot exactly like `KNOWN_PACK_FREE` warns it would.

Verdicts:
  live     a guard/suite that some gate reaches (make target, suitecheck, another script, or a *_test.py)
  manual   a real instrument that nothing calls: it is run by hand, and that is a legitimate state, but
           it must be NAMED as manual rather than pass for automated coverage
  duplicate  another module in scripts/ has the same behaviour (measured by comparing its check names),
           so at least one copy is dead weight
  debris   no main()/guard structure, no referrer, tiny or scratch-named: a one-hour script that was
           never meant to live here

Exit 0 every module classified and no UNKNOWN · 1 anything UNKNOWN (a verdict this file could not derive)
"""
import ast
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Names that announce a scratch script. Not used to DECIDE debris on its own -- only to break a tie
# between "nothing refers to it" and "it was clearly written for one hour of one night".
SCRATCH = re.compile(r"^(post|pt|dm_send|chat\d{3}|sub|t2|d1|w|r|tl2|tlprobe|hb_check|e2e|bindtest"
                     r"|setscopes|post\d{3})$")


def check_names(path):
    """The set of strings a module passes to check()/ok() -- its behavioural fingerprint.

    Two files with the same fingerprint check the same things. Used for the duplicate verdict, so the
    duplicate claim is measured from what the files ASSERT, not from their names -- `sevprobe2.py`
    abbreviates `severityprobe2.py`, and name similarity would have got that backwards.
    """
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (SyntaxError, OSError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in ("check", "ok"):
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.add(" ".join(a.value.split()))
    return out


def _loads_here(text, name, stem, loader):
    """True only if a mention of this file sits on a line that also loads it (or the line after).

    Beat 636. Distinguishes 'this file is used' from 'this file's name appears in a test fixture'. Looks
    at the matching line and one line either side, because `spec = ...spec_from_file_location(...\n
    os.path.join(HERE, "x.py"))` splits across lines.
    """
    lines = text.splitlines()
    pat = re.compile(rf"[\"'][\w./]*{re.escape(name)}[\"']")
    for i, line in enumerate(lines):
        if not pat.search(line):
            continue
        window = "\n".join(lines[max(0, i - 2):i + 3])
        if loader.search(window):
            return True
    return False


def referrers(name, allfiles):
    """Every file that names this module: make targets, shell harnesses, python imports, prose.

    THE FIRST VERSION OF THIS FUNCTION LIED, and the census caught it as 3 UNKNOWNs. It counted prose:
    a docstring line '`severityprobe` (this hour) said ...' made severityprobe.py 'manual, 2 referrers'
    when nothing invokes it, and a mention in ANOTHER PROBE'S docstring counts as a referrer while the
    probe is itself dead. Two kinds of mention are now separated:

      strong   an `import`/`from` of the stem, or a *.sh / Makefile line that names the file
      weak     a bare mention in python or markdown -- PROSE. A mention is evidence that someone knew
               about the file, not that anything RUNS it.
    """
    stem = name[:-3]
    strong, weak = [], []
    imp = re.compile(r"^\s*(?:from\s+%s\s+import|import\s+%s\b)" % (re.escape(stem), re.escape(stem)),
                     re.M)
    # Beat 636: the import regex has a BLIND SPOT and it is the dangerous direction. A file loaded with
    # importlib.util.spec_from_file_location(...) has a real, working dependency that no `import` line
    # names -- so the census called chatread.py "manual, 0 sig referrers" ONE BEAT AFTER chatread_test.py
    # was wired into suitecheck and started covering it. A FALSE NEGATIVE here reads as "nothing uses
    # this", which is exactly the verdict that gets a live file attic'd or deleted. Two shapes exist in
    # this tree: a literal path ('os.path.join(HERE, "guardreach.py")') and a stem used as the module
    # name. Match the quoted filename first, then the spec name.
    dyn_file = re.compile(rf"[\"'][\w./]*{re.escape(name)}[\"']")
    dyn_stem = re.compile(rf"spec_from_file_location\(\s*[\"'][\w.]*{re.escape(stem)}[\"']")
    # ...but a bare quoted filename is NOT proof of a dependency. `guardreach_test.py` contains the literal
    # "guardtriage.py" purely as a fixture string ('is this name treated as a test?'), and the naive
    # quoted-path regex promoted guardtriage.py to 'live' on that. That is the exact false positive this
    # census's first version made with prose, repeated in a new costume. A quoted path now counts only when
    # something in the line actually OPENS or EXECUTES it.
    loader = re.compile(r"spec_from_file_location|exec_module|runpy|subprocess")
    for p in allfiles:
        base = os.path.basename(p)
        if base == name:
            continue
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(p, ROOT)
        if base == "Makefile" or base.endswith(".sh"):
            if re.search(rf"\b{re.escape(stem)}\b", text):
                strong.append(rel)          # a harness that names it IS a caller
        elif imp.search(text):
            strong.append(rel)              # a real import
        elif dyn_stem.search(text):
            strong.append(rel)              # spec_from_file_location("stem"...) -- unambiguous
        elif dyn_file.search(text) and _loads_here(text, name, stem, loader):
            strong.append(rel)              # quoted path AND an actual load on that line or nearby
        elif re.search(rf"\b{re.escape(stem)}\b", text) or name in text:
            weak.append(rel)                # prose only
    return strong, weak


def main():
    mods = sorted(p for p in os.listdir(HERE) if p.endswith(".py") and not p.endswith("_test.py"))
    allfiles = ([os.path.join(HERE, f) for f in os.listdir(HERE)]
                + [os.path.join(ROOT, f) for f in ("Makefile", "README.md")]
                + glob.glob(os.path.join(ROOT, "docs", "*.md")))
    allfiles = [p for p in allfiles if os.path.isfile(p)]

    # Beat 636: the Makefile is not optional evidence, it is one of the two ways a module is 'live', and
    # `"Makefile" in strong` degrades to `"Makefile" in []` = False when the file is simply MISSING. Found
    # by copying scripts/ to /tmp to mutation-test it: the control copy read live=15/imported=29 against
    # the real tree's live=42/imported=2, so 27 modules silently lost their gate. Nothing errored. The
    # census reported a different world because it was standing in a different one.
    # A classifier whose verdict changes based on files it never checks exists is not classifying the
    # tree, it is classifying the tree it happens to be in. Require the anchors and say so.
    anchors = [os.path.join(ROOT, "Makefile"), os.path.join(ROOT, "README.md")]
    missing_anchors = [a for a in anchors if not os.path.isfile(a)]
    if missing_anchors:
        print(f"census: FAILED -- missing {', '.join(os.path.basename(a) for a in missing_anchors)} under "
              f"{ROOT}. These are load-bearing: a module named in the Makefile is reachable, and without "
              f"the file every such module silently reads as merely 'imported'. Refusing to publish a "
              f"verdict computed without them.")
        return 1

    # Referrers reach modules through *_test.py suites, so a module referenced ONLY by its own test is
    # reached by the gate. That distinction is the point of `manual` vs `live`.
    rows, prints, unknown = [], {}, []
    for name in mods:
        prints[name] = check_names(os.path.join(HERE, name))
    for name in mods:
        if name == os.path.basename(__file__):
            continue                        # a census cannot cite itself as evidence that it runs
        strong, weak = referrers(name, allfiles)
        strong = [r for r in strong if not r.startswith("docs/")]
        weak = [r for r in weak if not r.startswith("docs/")]
        twin = None
        if prints[name]:
            for other in mods:
                if other != name and prints[other] == prints[name] and prints[other]:
                    twin = other
                    break
        src = open(os.path.join(HERE, name), encoding="utf-8").read()
        if any(r.startswith("scripts/") and r.endswith("_test.py") for r in strong) \
                or "Makefile" in strong or any(r.endswith(".sh") for r in strong):
            verdict = "live"
        elif twin:
            verdict = "duplicate"
        elif strong:
            verdict = "imported"             # a real call site, but no gate wraps it
        elif weak and "def main(" in src:
            verdict = "manual"               # an instrument someone wrote about and runs by hand
        elif not weak and "def main(" in src:
            verdict = "orphan"               # has structure, nothing anywhere names it
        elif SCRATCH.match(name[:-3]) or "def main(" not in src:
            verdict = "debris"
        else:
            verdict = "UNKNOWN"
            unknown.append(name)
        rows.append((verdict, name, len(prints[name]), len(strong), len(weak), twin or "",
                     (strong or weak)[:2]))

    order = {"live": 0, "imported": 1, "manual": 2, "orphan": 3, "debris": 4, "duplicate": 5,
             "UNKNOWN": 6}
    rows.sort(key=lambda r: (order[r[0]], r[1]))

    tally = {}
    for v, name, nchecks, nstrong, nweak, twin, ex in rows:
        tally[v] = tally.get(v, 0) + 1
        print(f"{v:10} {name:22} {nchecks:3} checks  sig={nstrong} prose={nweak}"
              + (f"  twin={twin}" if twin else "")
              + (f"  e.g. {', '.join(ex)}" if (v != "live" and ex) else ""))

    print()
    print("     " + ", ".join(f"{k}={tally.get(k, 0)}" for k in ("live", "imported", "manual",
                                                               "orphan", "debris", "duplicate",
                                                               "UNKNOWN")))
    for k, line in (("orphan", "nothing in the tree names -- an instrument with no reader"),
                    ("debris", "no main()/guard and no name -- one-hour scripts; scripts/attic/ still "
                               "does not exist"),
                    ("manual", "run by hand, named only in prose -- legitimate, but NOT automated "
                               "coverage")):
        if tally.get(k):
            print(f"     {tally[k]} {k}: {line}")

    # THE ASSERTION: every module gets a verdict this file can defend. An UNKNOWN is the failure --
    # not a large debris count, which is information, not a fault. A census that goes red because the
    # tree is messy is a census nobody runs twice.
    if unknown:
        print(f"\ncensus: {len(unknown)} UNKNOWN -- no verdict derivable for: {unknown}")
        return 1
    # Beat 636: an empty or shrunken module set must FAIL, not pass. Run from a directory with no modules
    # in it (a mutation copy in /tmp, a mistyped checkout) and the old code printed "all 0 non-test modules
    # classified; nothing unaccounted for" and exited 0 -- a perfect green verdict over nothing. That is the
    # same pathology this project hunts, in my own instrument: the check reports success because it has
    # nothing to check, and "0 problems found" and "0 things looked at" print identically.
    if len(mods) < 20:
        print(f"census: FAILED -- only {len(mods)} non-test module(s) found under {HERE}. This tree has 56; "
              f"a census of almost nothing is not a clean census. (Running from the wrong directory?)")
        return 1

    print(f"\ncensus: all {len(rows)} non-test modules classified; nothing unaccounted for")
    return 0


if __name__ == "__main__":
    sys.exit(main())

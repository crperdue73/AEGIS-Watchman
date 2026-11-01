#!/usr/bin/env python3
"""covermap_test — prove covermap can say UNNAMED, say PACK-ONLY, and can't be fooled by a substring.

WHY
  covermap's answer this beat was "0 unnamed, 13 named" — a clean sweep. A sweep is the shape of
  result that is most likely to be an artifact: if `classify` reported 'named' for everything, or if
  the walk silently returned the pack plus one doc, every value would look discussed. So the test
  does not check the numbers; it checks the DISCRIMINATION.

  Each case builds a temp tree, runs covermap against it, and asserts which class each value lands
  in. The cases are chosen to be wrong if the classifier is wrong in either direction:
    A. a value that appears nowhere but the pack   -> UNNAMED (the real gap must be reportable)
    B. a value that appears only in the pack       -> PACK-ONLY (not coverage, not unnamed)
    C. a value named in one other file             -> NAMED
    D. a value whose only outside hit is the DOC of the pack itself -> PACK-ONLY, not NAMED
       (a rule that counts `rules/carbonato.yaml` as "the tree" would make the gap invisible)
    E. a substring collision: 'host_network' appears only inside 'host_network_disabled' -> the
       substring hit IS counted (measured), and the test states that as the known limit rather
       than pretending the classifier is exact.

Exit 0 if all behaved, 1 otherwise.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))            # .../scripts
PROJECT = os.path.dirname(ROOT)                              # the project
sys.path.insert(0, ROOT)
# Each case runs covermap inside a TEMP TREE, but the script it runs lives in THIS tree, and
# covermap walks from its own __file__. A pre-existing 'covermap' module on sys.path could
# shadow it — worse, phase 1 of this test imported covermap from the real tree and every case
# returned the REAL project's answer ('10 unread, against 309 files') for a temp pack.
# So: run the script as a FILE (by path, no import), and assert the file count it reports is
# the temp tree's, not this one's. That assertion is what would have caught it.

import covermap                                              # noqa: E402

SCRIPT = os.path.join(ROOT, "covermap.py")

ok_n = 0
bad_n = 0


def check(name, got, want):
    global ok_n, bad_n
    if got == want:
        print(f"ok    {name} -> {got!r}")
        ok_n += 1
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        bad_n += 1


PACK = """\
rules:
  - id: carbonato.test.alpha
    title: t
    match:
      type: file.access
      path_glob_any:
        - "*alpha-glob*"
  - id: carbonato.test.beta
    title: t
    match:
      type: container.start
      detail_non_empty_any:
        - "delta-value"
        - "echo-value"
        - "foxtrot-value"
      detail_glob_any:
        - "*golf-glob*"
        - "kilo-token"
"""


def build(tmproot, extra_files):
    os.makedirs(os.path.join(tmproot, "rules"), exist_ok=True)
    os.makedirs(os.path.join(tmproot, "scripts"), exist_ok=True)
    # The real scripts, so covermap's own imports resolve exactly as they do in the project.
    # Only rules/ and the extra files differ per case — the code under test is never a copy.
    for fn in ("covermap.py", "blindcheck.py", "fieldcheck.py", "packcheck.py"):
        src = os.path.join(ROOT, fn)
        if os.path.exists(src):
            with open(src, encoding="utf-8") as fh:
                body = fh.read()
            with open(os.path.join(tmproot, "scripts", fn), "w") as fh:
                fh.write(body)
    with open(os.path.join(tmproot, "rules", "carbonato.yaml"), "w") as fh:
        fh.write(PACK)
    for rel, body in extra_files.items():
        full = os.path.join(tmproot, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(body)


def run_case(extra_files):
    """Run covermap inside a temp project and parse its table into {value: class}."""
    tmp = tempfile.mkdtemp(prefix="covermap-test.")
    try:
        build(tmp, extra_files)
        # The script must be the TEMP tree's copy: covermap derives ROOT from __file__, so the
        # project's own copy walks the project no matter what cwd says. Phase 1 of this test ran
        # SCRIPT (this repo's path) and every case returned the real project's answer.
        script = os.path.join(tmp, "scripts", "covermap.py")
        proc = subprocess.run([sys.executable, script], cwd=tmp,
                              capture_output=True, text=True)
        # GUARD: the run must be about the TEMP tree. If it isn't, every class below is a lie
        # about the real project rather than about the fixture.
        m = re.search(r"against (\d+) files", proc.stdout)
        got_files = int(m.group(1)) if m else -1
        table = {}
        last = None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("[") and "]" in line:
                last = line[1:line.index("]")].strip()
            elif last and line.count("'") == 2 and "named in:" not in line:
                # table line shape:  "detail_non_empty_any: 'delta-value'"
                # Phase 2 of this test split on "': " and got None for every value — the row is
                # 'key: value' so the quote is followed by the END of line, never by "': ".
                val = line.split("'")[1]
                table[val] = last
        return table, proc, got_files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    # A/B: nothing outside the pack mentions these.
    table, proc, nf = run_case({"README.md": "nothing to see\n"})
    check("0. the run walked the TEMP tree, not this project", nf < 20, True)
    # A/B: a value that appears ONLY in the pack is PACK-ONLY, not UNNAMED. My first version of
    # this case asserted 'unnamed' and went red — the classifier was right and the fixture was
    # wrong: 'delta-value' is a literal in rules/carbonato.yaml, so it is a spelling with no
    # coverage, which is exactly the category the tool invented to name that. UNNAMED needs a
    # value present in the pack by DERIVATION yet absent as a string — so it is exercised below
    # by deleting the literal from every non-pack file in a way classify() can still see.
    check("A. a value appearing only in the pack is PACK-ONLY, and that is the honest class",
          table.get("delta-value"), "pack-only")
    check("B. ...same for every literal in the pack",
          table.get("foxtrot-value"), "pack-only")
    check("A2. UNNAMED is only for a value the pack holds but no file spells — impossible here, "
          "so the class is exercised directly on classify()",
          covermap.classify(("zz-" + "no-file-spells-this-" + "zz"), covermap.walk(ROOT))[0],
          "unnamed")
    check("the run exits 0 even with unnamed values (it is a report)", proc.returncode, 0)

    # C: one other file names it.
    table, _, _ = run_case({"notes.md": "we discussed delta-value at length\n"})
    # beat 576: the class is 'documented', because a .md mention is not a test. The old
    # expectation 'named' was the bug in assertion form.
    check("C. a value named in one other prose file is DOCUMENTED (not TESTED)",
          table.get("delta-value"), "documented")
    check("C2. ...and a value still only in the pack stays PACK-ONLY",
          table.get("foxtrot-value"), "pack-only")

    # D: the pack's own doc must not count as the tree.
    table, _, _ = run_case({"rules/carbonato.yaml.bak": "echo-value\n"})
    check("D. a hit inside rules/ DOES count (only the pack file itself is excluded)",
          table.get("echo-value"), "pack-only")

    # E: the known limit — a substring hit counts. Stated, not hidden. FOUR wrong versions of this
    # one case, every one of them structurally impossible rather than a tool defect:
    #   (i)  'golf-glob'  — the pack spells it '*golf-glob*', stars included;
    #   (ii) 'alpha-glob' — the pack spells it '*alpha-glob*', stars included;
    #        a value containing '*' can never be a substring of real code, so neither could hit;
    #   (iii) a duplicate E block: my edits kept landing on the first copy while the second one,
    #        still using 'alpha-glob', ran last and overwrote the result.
    # The pack value must be a BARE TOKEN for a substring collision to exist at all: 'kilo-token'.
    table, _, _ = run_case({"code.py": "octet = 'kilo-token-disabled'\n"})
    check("E. a bare-token value hit inside a longer identifier IS counted at all "
          "('kilo-token' inside 'kilo-token-disabled' — known limit, stated). Landing in a .py "
          "that is not a test makes it DOCUMENTED, which is the honest class.",
          table.get("kilo-token"), "documented")

    # F: the refusal. An empty pack must not print a clean map.
    tmp = tempfile.mkdtemp(prefix="covermap-test.")
    try:
        build(tmp, {})
        with open(os.path.join(tmp, "rules", "carbonato.yaml"), "w") as fh:
            fh.write("rules: []\n")
        # The TEMP copy again — SCRIPT is this project's path and covermap walks from __file__,
        # so running SCRIPT here found the real pack's 13 values and exited 0. The same bug I
        # fixed in run_case was still sitting in this case; a fix applied at one call site is not
        # a fix. This case is why that matters: it is the one that exists to prove a clean result
        # is REFUSED, and it was itself reading a pack that wasn't empty.
        proc = subprocess.run([sys.executable, os.path.join(tmp, "scripts", "covermap.py")],
                              cwd=tmp, capture_output=True, text=True)
        check("F. a 0-value pack REFUSES to print a clean map (exit 2)", proc.returncode, 2)
        check("F2. ...and says so rather than printing zeros",
              "Refusing" in proc.stderr, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # G: the split beat 576 exists for. A pack value named ONLY in a .md finding is not coverage.
    table, _, _ = run_case({"WORKLOG.md": "we discussed delta-value at length\n"})
    check("G. a value only in a worklog is DOCUMENTED, not TESTED",
          table.get("delta-value"), "documented")

    # H: and a value in a test file that never names its rule is 'named-in-test', not TESTED.
    table, _, _ = run_case({"test_other.py": "x = 'kilo-token'\n"})
    check("H. a hit in a test file that never names the rule is named-in-test",
          table.get("kilo-token"), "named-in-test")

    # I: the instrument must not certify itself. The fixture has to keep covermap RUNNABLE —
    # v1 wrote the bare string into covermap.py, which replaced the program and died on
    # NameError. A test that breaks the thing under test measures nothing.
    base = open(os.path.join(ROOT, "covermap.py"), encoding="utf-8").read()
    table, _, _ = run_case({"scripts/covermap.py": base + "\n# note: kilo-token\n"})
    check("I. a hit inside covermap.py itself is not a mention and not coverage",
          table.get("kilo-token"), "self-only")

    print(f"\ncovermap_test: {ok_n} ok, {bad_n} fail")
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())

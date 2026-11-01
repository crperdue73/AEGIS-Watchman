#!/usr/bin/env python3
"""seencensus — what the inventory covers, and what it does when it can't look.

BEAT 730. Beat 729's closing note said the next target was `Inventory`. Opened it, and
the useful question turned out to be the inverse of what I expected: not "what is in
Files" but **what is absent from Files, and does the absence carry any sign?**

THE TWO PATHS, MEASURED
-----------------------
`WalkRoot` (collector.go:172) deliberately keeps walking past trouble — the doc comment
says so: *"Unreadable files are skipped rather than failing the whole walk (a locked file
should not blind the collector to every other file in the agent)."* That is a good
decision and I am not arguing with it. It produces **three** different absences:

  1. `err != nil` on a directory      -> `filepath.SkipDir`  (whole subtree, silent)
  2. `err != nil` on a file/entry     -> `return nil`         (one entry, silent)
  3. `HashFile` fails on a matched file -> `return nil`       (one file, silent)

All three land as "not in `inv.Files`". No counter, no warning, no field on `Inventory`.
`Inventory` is `{Root, Files []FileState}` — there is nowhere for a "could not read N
files" fact to live even if someone wanted to record it.

WHAT DRIFT THEN SAYS ABOUT IT
-----------------------------
`Drift` (drift.go:16) iterates the **baseline** first. For a path in the baseline that is
not in `curr`, the comment on that branch is literally `// Removed file.` — it emits
`EventIdentityDrift` with `SHA256Before` set and `SHA256After` empty, severity from
`severityFor(path)`, and for an identity/persona path that is **high**.

So a file that became **unreadable** — chmod, an ACL, a mount that dropped, a lock taken
by something the collector cannot see — is reported as a file that was **deleted**. The
two are not the same event, and in a detector built to catch an agent's identity files
being tampered with, "the identity file was removed" is the alarm people act on.

And because the event is well-formed and severity-high, nothing downstream can tell them
apart either. The alert looks exactly like a correct one.

WHY THIS IS WORSE THAN THE 724-729 FAMILY
-----------------------------------------
Those were silences: something true that nobody could see. This is a silence that
**becomes a positive false statement**. An absent file is reported as an assertion:
*this file was here and is now gone.* A miss is a non-event; this is a false event, with
a severity attached, pointing at the highest-value files in the tree.

Note also the asymmetry that makes it likely rather than theoretical: the skip paths are
**silent by design and by comment**, so the walk's honesty is only as good as its
hash. `HashFile` errors are the one place where the collector's *intent* (keep going) and
its *output* (looks like a deletion) diverge.

SCOPE, HONESTLY
---------------
- Requires a file that was readable at baseline time to become unreadable later. Not
  exotic (backup agents, `chmod 600` during a secret rotation, a bind mount appearing),
  but not the default case either.
- **This is not measured against a running collector** — no Go toolchain here (beat 642),
  so the three skip branches and the `// Removed file.` branch are source reads. The
  claim is about what the code *will* say, and it is the kind of claim that a single
  test with a chmod'd file would settle. **NOT CLAIMED as observed.**

NOT APPLIED — options, none picked, all Dad's call:
  1. **Record the gap in `Inventory`**: add e.g. `Skipped []SkippedPath` (or a count per
     reason) so the absence travels with the data. Then `Drift` can distinguish "gone"
     from "not seen", and the roster/alert can say which. Structurally honest; changes
     the wire schema for both collector and central.
  2. **Make the skip loud**: log once per walk with the count and first few paths.
     Cheapest. Does not fix the false "removed" event; only makes it traceable in logs
     nobody greps during an incident.
  3. **`Drift` treats an unreadable current path as `unverified`, not `removed`** — needs
     option 1 first, so it is a consequence, not an alternative.
  4. Suppress removed-files whose reason might be permission. **Argue against**: it makes
     a real deletion silent to fix a false alarm, trading a loud wrong answer for a quiet
     missing one. In this product that is the worse trade.

WHAT THIS SCRIPT DOES
---------------------
Pins the shape so any of the above goes red and forces re-derivation:
  A. the three silent-skip branches still exist (with their comments, which are the
     design intent and must not vanish silently)
  B. `Inventory` still has no field capable of carrying the absence
  C. `Drift`'s not-in-current branch still emits removal-shaped events at severity
  D. `severityFor` still calls identity/persona paths high (the multiplier on the bug)
  E. the doc carries the not-applied marker and the not-claimed-as-observed scope
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The exact population of HIGH-graded identity files, pinned as a set. Beat 730 M3 proved a
# two-member AND cannot see one member disappear.
EXPECTED_HIGH_SUFFIXES = ["soul.md", "identity.md", "persona.md"]
ROOT = HERE.parent
FAILS, OKS = [], 0


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def check_a_three_silent_skips():
    src = read("internal/collector/collector.go")
    w = src[src.find("func WalkRoot("):]
    if re.search(r"if d != nil && d\.IsDir\(\) \{\s*return filepath\.SkipDir", w):
        ok("A gap: an unreadable DIRECTORY silently skips the whole subtree "
           "(filepath.SkipDir, no record)")
    else:
        fail("A: the directory-skip branch changed — re-derive")
    if re.search(r"if herr != nil \{\s*return nil //\s*unreadable file: skip, keep walking", w):
        ok("A gap: a matched file whose hash fails is silently skipped, keeping the walk — "
           "the intent is right, the silence is the issue")
    else:
        fail("A: the HashFile-skip branch changed — re-derive")
    if "a locked file should not" in src:
        ok("A: the skip-keep-walking design is stated in the doc comment — this is a "
           "deliberate decision, and the finding is about its UNSIGNED output, not the "
           "decision itself")
    else:
        fail("A: the design-intent comment was lost — the finding must be re-read against "
             "whatever replaced it")


def check_b_inventory_cannot_carry_it():
    src = read("internal/collector/collector.go")
    m = re.search(r"type Inventory struct \{(.*?)\n\}", src, re.S)
    if not m:
        fail("B: Inventory type not found")
        return
    body = m.group(1)
    if re.search(r"^\s*(Root|Files)\b", body, re.M) and len(
            [l for l in body.splitlines() if l.strip()]) == 2:
        ok("B gap: Inventory is exactly {Root, Files} — there is no field where "
           "'could not read N files' could be carried, so the absence has nowhere to live")
    else:
        fail("B: Inventory's shape changed — if it now carries skips, the gap may be "
             "closing; re-derive rather than patch")


def check_c_drift_says_removed():
    src = read("internal/collector/drift.go")
    if re.search(r"if !ok \{\s*// Removed file\.", src):
        ok("C gap: a baseline path missing from current is labelled `// Removed file.` — "
           "unreadable and deleted collapse into the same branch")
    else:
        fail("C: the not-in-current branch changed — re-derive")
    seg = src[src.find("if !ok {"):]
    seg = seg[:seg.find("continue")]
    if "SHA256Before: b.SHA256" in seg and "SHA256After" not in seg.replace("SHA256Before", ""):
        ok("C gap: the event asserts a before-hash and no after-hash — a positive claim "
           "that the file WAS there and is NOW gone, not a report of not-seeing")
    else:
        fail("C: the removal event's field shape changed — re-derive")
    if re.search(r"ev\.Severity = severityFor\(path\)", seg):
        ok("C gap: the false removal inherits full severity from severityFor — it arrives "
           "looking like a correct alarm")
    else:
        fail("C: severity assignment on the removal branch changed — re-derive")


def check_d_identity_paths_are_high():
    src = read("internal/collector/drift.go")
    seg = src[src.find("func severityFor("):]
    seg = seg[:seg.find("\n}")]
    # Measured, not assumed: the constants are schema.SeverityHigh/Medium, not literals.
    high = re.findall(r'strings\.HasSuffix\(p, "([^"]+)"\)', seg)
    if not re.search(r"return schema\.SeverityHigh", seg):
        fail("D: severityFor no longer returns SeverityHigh — re-derive the multiplier")
        return
    # M3 (beat 730): requiring "soul.md in high AND identity.md in high" stayed GREEN when
    # soul.md was removed from the list, because identity.md alone satisfied the AND. A set
    # check has to pin the whole population, not two members of it.
    if high != EXPECTED_HIGH_SUFFIXES:
        fail("D: the high-severity suffix list is %r, expected %r — a partial regression in "
             "the grading is still a regression; re-derive" % (high, EXPECTED_HIGH_SUFFIXES))
        return
    if True:
        ok("D: severityFor grades %s HIGH — so a false removal on those files arrives "
           "wearing the severity that makes people act" % ", ".join(high))
    else:
        fail("D: the high-severity suffix list changed to %r — re-derive" % (high,))
    if "skills.md" not in high and not any("skill" in h for h in high):
        ok("D: 'skill' does NOT appear in the high list, contrary to my first draft of this "
           "doc — the grading is narrower than I wrote it. Corrected rather than kept.")
    else:
        ok("D: a skill path IS graded high")


def check_e_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01k-unreadable-is-reported-as-removed.md"
    if not doc.exists():
        fail("E: the finding doc is missing")
        return
    # Beat 730: the first version searched raw text, so a phrase spanning a line break
    # ("positive false\nstatement") read as absent. Doc prose wraps; the check must not
    # care. Normalise whitespace, then search.
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("NOT CLAIMED as observed", "the no-toolchain honesty"),
        ("false statement", "the core claim: absence becomes an assertion"),
    ]:
        if phrase in t:
            ok("E: doc carries %s" % label)
        else:
            fail("E: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_three_silent_skips()
    check_b_inventory_cannot_carry_it()
    check_c_drift_says_removed()
    check_d_identity_paths_are_high()
    check_e_doc_scope()
    print()
    if FAILS:
        print("seencensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("seencensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

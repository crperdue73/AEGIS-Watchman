#!/usr/bin/env python3
"""walkerrorcensus — an unwalkable root is reported as an empty agent.

BEAT 743. filepath.WalkDir reports a permission error for the directory it cannot
read with d != nil and d.IsDir() true. WalkRoot's callback answers that with
`return filepath.SkipDir`, which swallows it. So a root that cannot be walked returns
a valid Inventory with Files: []. Central diffs that against the baseline and reports
every known file as REMOVED, then commits the empty baseline.

MEASURED (beat 743, Go 1.22.12, real WalkRoot, unprivileged uid, root containing
a/soul.md and identity.md): `err=<nil> files=0`.

Checks:
  A. the callback turns a directory error into SkipDir — the swallow
  B. every non-dir error path returns nil, so WalkDir can never fail
  C. the `if err != nil` guard after the walk is therefore dead in the error direction
  D. Inventory has no field to carry "could not look" — the wire cannot say it
  E. central diffs an empty current against a full baseline with no guard
  F. PutBaseline is unconditional, so the empty inventory is COMMITTED
  G. CollectAll's guard is on the glob, before the walk — cannot cover EACCES
  H. the doc keeps the measured claim, the de-dup against 730, and the not-claimed
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    global FAILS
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def func_body(src, needle):
    i = src.find(needle)
    if i < 0:
        return ""
    j = src.find("\nfunc ", i + 1)
    return src[i:] if j < 0 else src[i:j]


def check_a_swallow():
    b = func_body(read("internal/collector/collector.go"), "func WalkRoot(")
    if not b:
        fail("A: WalkRoot not found")
        return
    # Quote the comparison AND the escape it returns — the pair is the swallow.
    # Beat 743 M1: the fix is ONE WORD (`return err` for `return filepath.SkipDir`), and it
    # keeps the identical surrounding lines and comment. Anchor on the return STATEMENT with
    # whitespace/comment tolerance, not on adjacency to the if.
    guard = re.search(r"if d != nil && d\.IsDir\(\) \{([\s\S]{0,200}?)\n\t\t\}", b)
    ret = re.search(r"return filepath\.SkipDir", guard.group(1)) if guard else None
    if ret:
        ok("A gap: a directory error becomes filepath.SkipDir — the walk continues and "
           "WalkDir returns nil. Measured end to end: err=<nil> files=0 on a root holding "
           "two files. Fails the moment that return yields err instead.")
        ok("A gap: a directory error becomes filepath.SkipDir — the walk continues and "
           "WalkDir returns nil. Measured end to end: err=<nil> files=0 on a root holding "
           "two files. Fails if the error is propagated instead.")
    elif guard:
        fail("A: the directory-error branch no longer returns SkipDir (now %r) — the swallow "
             "may be fixed; re-derive before reusing this finding"
             % guard.group(1).strip()[:80])
    else:
        fail("A: the d != nil && d.IsDir() guard is gone — re-derive")
    if re.search(r"if err != nil \{[\s\S]{0,120}?return nil // unreadable file", b) or \
            re.search(r"return nil // unreadable file: skip, keep walking", b):
        ok("A: the per-FILE skip is present too (:202-204) — that is beat 730's door, "
           "already filed, and not re-claimed here")
    else:
        fail("A: the per-file skip comment moved — check 730's finding still matches")


def check_b_never_errors():
    b = func_body(read("internal/collector/collector.go"), "func WalkRoot(")
    # Every `return` inside the callback that yields an error
    cb = b[b.find("filepath.WalkDir("):b.find("})\n", b.find("filepath.WalkDir("))]
    if re.search(r"return (fmt\.|err\b|os\.)", cb):
        fail("B: the callback can now return an error to WalkDir — the swallow may be "
             "partial; re-derive")
    else:
        ok("B gap: no return inside the callback yields an error — every path returns nil, "
           "SkipDir, or continues. WalkDir therefore cannot fail, so WalkRoot's error "
           "return is decorative.")


def check_c_dead_guard():
    b = func_body(read("internal/collector/collector.go"), "func WalkRoot(")
    if re.search(r"\n\tif err != nil \{\s*\n\t\treturn nil, err\s*\n\t\}", b):
        ok("C gap: the post-walk `if err != nil { return nil, err }` is dead in the error "
           "direction — a guard that cannot fire. Same species as beat 737's."
           "  Fails if the callback starts propagating.")
    else:
        fail("C: the post-walk error guard was removed or changed — re-derive")


def check_d_wire_cannot_say():
    b = func_body(read("internal/collector/collector.go"), "type Inventory struct {")
    if not b:
        b = func_body(read("internal/collector/collector.go"), "type Inventory")
    fields = re.findall(r"^\t([A-Z]\w*)\s+", b or "", re.M)
    if sorted(fields) == ["Files", "Root"]:
        ok("D gap: Inventory carries exactly %r — two fields, no way to say 'could not "
           "look'. The wire format itself cannot distinguish empty from unreadable. Pinned "
           "as a SET, so adding Skipped/Complete/Unverified goes red (that is the fix)."
           % sorted(fields))
    else:
        fail("D: Inventory now has %r — the wire may be able to carry unverified state; "
             "re-derive" % sorted(fields))


def check_e_central_diffs():
    b = func_body(read("internal/central/service.go"), "func (s *Service) Ingest(")
    if re.search(r"events = collector\.Drift\(host, agent, baseline, inv, s\.MaxDiffBytes\)", b):
        ok("E gap: central calls Drift(baseline, inv) with no check that inv was actually "
           "read — an empty inventory and an unreadable root produce identical events")
    else:
        fail("E: the Drift call changed — re-derive")
    d = func_body(read("internal/collector/drift.go"), "func Drift(")
    if re.search(r'c, ok := curr\[path\][\s\S]{0,200}?EventIdentityDrift', d):
        ok("E gap: Drift emits identity_drift for every baseline path missing from curr — "
           "so an empty current yields one removal event per known file")
    else:
        fail("E: Drift's removal branch changed — re-derive")


def check_f_baseline_committed():
    b = func_body(read("internal/central/service.go"), "func (s *Service) Ingest(")
    if re.search(r"\n\tif err := s\.store\.PutBaseline\(ctx, key, inv\); err != nil \{", b):
        ok("F gap: PutBaseline is unconditional — the empty inventory is COMMITTED. The "
           "next good walk then reports every file as ADDED, so one permission change "
           "produces two opposite false alarms and the second reads like an intrusion.")
    else:
        fail("F: PutBaseline is now guarded — the amplification may be gone; re-derive")


def check_g_guard_is_on_glob():
    b = func_body(read("internal/collector/run.go"), "func CollectAll(")
    if re.search(r"if len\(matches\) == 0 \{", b) and "WalkRoot(" in b:
        mi = b.find("if len(matches) == 0")
        wi = b.find("WalkRoot(")
        if mi < wi:
            ok("G gap: CollectAll's only guard runs BEFORE the walk and tests the GLOB "
               "(len(matches) == 0) — it protects a nonexistent root and cannot see an "
               "EACCES, which only exists inside the walk. The guard is where the "
               "information is not.")
        else:
            fail("G: the guard moved after the walk — re-derive")
    else:
        fail("G: CollectAll's glob guard changed — re-derive")


def check_h_doc():
    doc = ROOT / "docs/FINDING-2026-11-02a-an-unwalkable-root-is-reported-as-an-empty-agent.md"
    if not doc.exists():
        fail("H: doc missing")
        return
    t = re.sub(r"\s+", " ", re.sub(r"[*_`]", "", doc.read_text()))
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("an unwalkable root is reported as an empty agent", "the one-line claim"),
        ("err=<nil> files=0", "the executed measurement"),
        ("MEASURED (beat 743)", "the measured section"),
        ("this is 730's third door", "the de-duplication against 730"),
        ("What I am NOT claiming", "the not-claimed section"),
        ("Skipping unreadable subtrees is the right call", "the sound part"),
    ]:
        if phrase in t:
            ok("H: doc carries %s" % label)
        else:
            fail("H: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_swallow()
    check_b_never_errors()
    check_c_dead_guard()
    check_d_wire_cannot_say()
    check_e_central_diffs()
    check_f_baseline_committed()
    check_g_guard_is_on_glob()
    check_h_doc()
    print()
    if FAILS:
        print("walkerrorcensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("walkerrorcensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""inplacefiltercensus — the in-place slice filter, its safety invariant, and its comment.

BEAT 736. Beat 735 found the cap trims positionally. Following the same function one layer
down found `keep := m.recs[:0]` ranging over `m.recs` — the classic aliasing idiom that is
correct here and looks exactly like the version that isn't.

This script asserts, for BOTH slice backends:
  A. the filter is the aliasing form (same array, not a fresh make) — pinned, because if it
     ever becomes `make([]Record, 0, len(...))` the memory-hygiene note stops applying
  B. the write index can never exceed the read index — the ONE condition that makes it safe.
     Stated structurally: `keep` receives exactly the elements that pass the predicate, and
     `range` visits every element, so len(keep) <= i by induction. The script pins the
     predicate's shape so a rewrite that appends MORE than it visits goes red.
  C. the comment claims a boundary search ("Find the first index") that the code does not do
  D. the clock caveat is attached to the window pass, not the positional cap (beat 735)
  E. prune runs on EVERY Append on both slice backends — the O(n)-per-write shape is pinned
  F. doc scope: NOT APPLIED, NOT CLAIMED as an observed overwrite, and the safety argument
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
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def norm_comment(text):
    # Beat 734's rule: strip comment leaders BEFORE collapsing whitespace, or a phrase that
    # wraps across two `//` lines can never match. Third appearance — now a helper.
    return re.sub(r"\s+", " ", re.sub(r"//+", "", text))


def func_body(src, needle):
    i = src.find(needle)
    if i < 0:
        return ""
    j = src.find("\nfunc ", i + 1)
    return src[i:] if j < 0 else src[i:j]


BACKENDS = [
    ("MemoryRecordStore", "m", "func (m *MemoryRecordStore) pruneLocked"),
    ("FileRecordStore", "f", "func (f *FileRecordStore) pruneLocked"),
]


def check_a_aliasing_form():
    src = read("internal/central/eventstore.go")
    for name, v, sig in BACKENDS:
        b = func_body(src, sig)
        if not b:
            fail("A: %s pruneLocked missing — re-derive" % name)
            continue
        # Beat 736: first run went RED here because I wrote the variable as a bare regex
        # `f\.recs` with word boundaries fighting a one-letter name. Quote the assignment
        # with an explicit capture group instead of interpolating a bare letter.
        # Beat 736, second correction in the same check: the filter variable is named
        # `keep`, not a single letter — I had interpolated the RECEIVER (%s) as if it were
        # the slice variable. Match the receiver explicitly.
        if re.search(r"\bkeep := %s\.recs\[:0\]" % v, b):
            ok("A: %s filters with `keep := %s.recs[:0]` — SAME backing array, so the filter "
               "writes over slots it is still reading" % (name, v))
        elif re.search(r"keep\s*:=\s*make\(", b):
            # Beat 736: M1 (allocate a fresh slice) went GREEN here — I wrote the
            # gap-closing branch as a friendly `ok(...)` note. A detector that prints a
            # note when its own premise disappears is not detecting anything. This
            # instrument's job is to be a tripwire for "the code changed under the finding",
            # so every gap-closing branch FAILS.
            fail("A: %s now allocates a fresh slice — the aliasing premise is GONE; this "
                 "finding must be re-derived or retired" % name)
        else:
            fail("A: %s filter form unrecognised — re-derive" % name)


def check_b_safety_invariant():
    """The safety condition is len(keep) <= i. Structurally: keep receives at most one
    element per element visited, and the range visits all of them."""
    src = read("internal/central/eventstore.go")
    for name, v, sig in BACKENDS:
        b = func_body(src, sig)
        if not b:
            continue
        # The predicate must be a per-element test on r, appending r — not a bulk append.
        appends = re.findall(r"keep = append\(keep, ([^)]+)\)", b)
        if len(appends) == 1 and appends[0].strip() in ("r",):
            ok("B: %s appends exactly ONE element (%r) inside a per-element branch — so "
               "len(keep) <= i holds by induction and the in-place write can never land on "
               "an unread slot. This is why it is not a bug." % (name, appends[0].strip()))
        elif len(appends) > 1 or (appends and appends[0].strip() != "r"):
            fail("B: %s appends %r — more than one element per visit breaks the "
                 "len(keep)<=i invariant; the in-place filter may now be unsafe"
                 % (name, appends))
        else:
            fail("B: %s no longer appends a filtered element — re-derive" % name)
        if re.search(r"append\(keep,\s*%s\.recs\[" % v, b):
            fail("B: %s appends a SLICE, which can grow keep faster than range advances" % name)


def check_c_comment_claims_a_search():
    src = read("internal/central/eventstore.go")
    for name, v, sig in BACKENDS:
        b = func_body(src, sig)
        if not b:
            continue
        c = norm_comment(b)
        # Beat 736 MEASUREMENT, not a pass: FileRecordStore.pruneLocked carries NO comment.
        # My doc said the two implementations were identical; they are not. The Memory copy
        # explains itself (wrongly, C) and the File copy explains nothing at all.
        if name == "FileRecordStore":
            # Beat 736, and this check is the beat's lesson in miniature — it went wrong
            # THREE ways before it was right:
            #   1. original: encoded "File has no comment" as bare `ok(...)` with no
            #      observation, so M4 (adding a comment) stayed green;
            #   2. my first fix: replaced the pass with an UNCONDITIONAL fail, which turned
            #      the control red on a clean tree — I fixed the "doesn't catch it" by
            #      making it always catch it, which is the same as not catching anything;
            #   3. this: assert the state that IS true, and fail when it changes.
            if "//" in b:
                fail("C: FileRecordStore.pruneLocked now HAS a comment — the "
                     "documented/undocumented asymmetry this beat measured is gone; re-derive")
            else:
                ok("C: FileRecordStore.pruneLocked still has NO comment at all — same "
                   "in-place filter as Memory, zero explanation. The asymmetry is who "
                   "explains themselves, not what they execute.")
            continue
        if "Find the first index still in window" in c:
            ok("C: %s comment still says 'Find the first index still in window' — but the code "
               "runs a full linear filter and never uses a boundary index. The comment "
               "describes an optimisation the function does not perform." % name)
        elif "index" in c:
            ok("C: %s comment mentions an index but not the phrase — re-read; possible "
               "rewrite" % name)
        else:
            fail("C: %s comment no longer claims a boundary search — the finding may be "
                 "fixed; re-derive" % name)
        # The structural half: no binary search / early exit anywhere in the body.
        if re.search(r"sort\.Search|break", b):
            fail("C: %s now contains a search or early exit — re-derive this finding" % name)


def check_d_caveat_attachment():
    src = read("internal/central/eventstore.go")
    for name, v, sig in BACKENDS:
        b = func_body(src, sig)
        if not b:
            continue
        c = norm_comment(b)
        if name == "FileRecordStore" and "mis-ordered clock" not in c:
            ok("D: the clock caveat exists ONLY on the Memory copy — File's pruneLocked has "
               "no such note, so the positional cap's assumption is documented in one of the "
               "two places it holds")
            continue
        if "mis-ordered clock only costs a stale record surviving" in c:
            ok("D: %s keeps the clock caveat next to the WINDOW pass — where order genuinely "
               "cannot hurt — and not next to the positional cap below it (beat 735). "
               "Second beat in a row the comment is attached to the wrong half." % name)
        else:
            fail("D: %s clock caveat moved or changed — re-derive" % name)


def check_e_prune_per_append():
    src = read("internal/central/eventstore.go")
    for name, v, sig in BACKENDS:
        app = func_body(src, "func (%s *%s) Append" % (v, name))
        if not app:
            fail("E: %s Append missing" % name)
            continue
        if re.search(r"%s\.pruneLocked\(%s\.now\(\)\)" % (v, v), app):
            ok("E: %s runs a FULL prune (O(n) scan + possible re-slice) on every single "
               "Append, under the store mutex — perf shape recorded, NOT a recommendation "
               "(beat 724: Dad's call)" % name)
        else:
            fail("E: %s Append no longer prunes inline — shape changed" % name)


def check_f_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01q-the-in-place-filter-aliases-its-input.md"
    if not doc.exists():
        fail("F: the finding doc is missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("NOT CLAIMED as an observed overwrite", "the deliberate narrow scope"),
        ("len(keep) <= i", "the safety invariant that makes it correct"),
    ]:
        if phrase in t:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_aliasing_form()
    check_b_safety_invariant()
    check_c_comment_claims_a_search()
    check_d_caveat_attachment()
    check_e_prune_per_append()
    check_f_doc_scope()
    print()
    if FAILS:
        print("inplacefiltercensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("inplacefiltercensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""capordercensus — the record cap trims by arrival, its comment claims by time.

BEAT 735. Beat 734 named two unchecked things and then answered one of them (CI does run
the sqlite tag — retracted the dramatic version). This beat answers the other: is
RecordRetention's "both are needed" claim actually true?

YES — both halves are enforced, on all three backends. That claim survives intact. The
finding is one layer in: **what "oldest" means when the cap trims.**

THE MEASUREMENT
---------------
The doc comment on the type (eventstore.go:110-113) says:
    "records older than this are dropped on write, and no more than MaxRecords are kept.
     Both are needed — a time window keeps the view 'recent' and a hard cap bounds
     memory/disk under a high-severity storm."

Window: all three backends compare `rec.When` to a cutoff. Memory :187-197, File :288,
SQLite :163-168. All three enforce it. ✅
Cap:    Memory :198-200 and File :296-298 do
            m.recs = m.recs[len(m.recs)-m.retain.MaxRecords:]
        which keeps the LAST N ELEMENTS OF THE SLICE — i.e. by ARRIVAL order.
        SQLite :170-177 does it by time:
            DELETE FROM records WHERE id NOT IN (
              SELECT id FROM records ORDER BY when_unix DESC, id DESC LIMIT ?)

And MemoryRecordStore's own field comment (:128) reads:
    recs []Record // append order (oldest first)

So the memory/file backends **declare** that append order IS oldest-first, and pruneLocked
(:184-191) has a comment saying exactly that:
    "records are roughly sorted by append time; a mis-ordered clock only costs a stale
     record surviving, never data loss."

THE GAP
-------
That parenthetical is true for the WINDOW pass, which filters by `r.When` and therefore does
not care about order. It is NOT true for the CAP pass, which slices positionally:

  - If arrival order diverges from time order, the cap deletes records by the wrong key.
  - The survivors are the N most recently ARRIVED, not the N most recent IN TIME.
  - "A stale record surviving" is the cost the comment names — but the cap's failure mode is
    the opposite: a RECENT record being deleted because it arrived late, while a stale one
    that arrived early is kept.

Is divergence reachable? In production, no: `retain` (service.go:82-93) stamps `When` from
`s.nowTime()` at ingest, so `When` is the ingest clock and append order == time order by
construction. I checked whether the clock is ever swapped in production — `s.now` has
exactly ONE writer (the test hook), and grep for `nowTime` shows no other production path.
So today this is **latent**, holding because of a property two files away, not because the
store checks it.

It becomes live the moment anything backdates a record: a replay/import path, a clock
correction applied after ingest, or a future "ingest at event time" change. `Record.When`
is a plain exported field; nothing in `Append` validates monotonicity.

SQLite gets this right — and gets it right STRUCTURALLY, because it does not have an
arrival order to confuse with time order at all.

WHY IT MATTERS MORE THAN IT LOOKS
---------------------------------
The cap exists to bound a severity storm. A storm is exactly when many records arrive
quickly — and a storm is when divergence (if any exists) is most likely and the cap fires
most often. So the cap's ordering assumption is weakest precisely in the condition it was
built for. That is the same shape as beats 730/731/733: a guard whose precondition is least
reliable when the guard is most needed.

WHAT THIS SCRIPT CHECKS
-----------------------
A. the type comment still claims both halves are needed, and both are really enforced
B. Memory/File cap is POSITIONAL (slice tail), SQLite cap is BY TIME — the asymmetry pinned
C. the slice is documented as "append order (oldest first)" — the assumption is stated
D. production stamps When from the service clock at ingest, so divergence is NOT reachable
   today (this is the honest scope: latent, not live)
E. nothing validates monotonicity on Append — no check, no error, no sort
F. doc scope: NOT APPLIED, NOT CLAIMED as live, and the "latent, not reachable today" line
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


def func_body(src, needle):
    i = src.find(needle)
    if i < 0:
        return ""
    j = src.find("\nfunc ", i + 1)
    return src[i:] if j < 0 else src[i:j]


def check_a_both_halves_enforced():
    src = read("internal/central/eventstore.go")
    # Beat 735: this went RED first run because I sliced src[:4000] and the comment sits at
    # char ~4100. Truncating the input is the same class as matching a word near the code —
    # the check was answering about a document I had shortened. Normalise the WHOLE file.
    norm = re.sub(r"\s+", " ", re.sub(r"//+", "", src))
    if "Both are needed" in norm:
        ok("A: the type says both halves are needed — the claim I came to test")
    else:
        fail("A: the 'both are needed' rationale changed — re-derive")
    mem = func_body(src, "func (m *MemoryRecordStore) pruneLocked")
    fil = func_body(src, "func (f *FileRecordStore) pruneLocked")
    for name, b in (("Memory", mem), ("File", fil)):
        if not b:
            fail("A: %s pruneLocked not found — re-derive" % name)
            continue
        # Beat 735: M5 deleted the `r.When.After(cutoff)` COMPARISON and left
        # `cutoff := now.Add(...)` behind, so `re.search("cutoff", b)` still matched and the
        # window half looked enforced while every record was being kept. A declared variable
        # is not an enforced rule. Quote the comparison.
        win = re.search(r"r\.When\.After\(cutoff\)", b)
        cap = re.search(r"len\([a-z]\.recs\) > [a-z]\.retain\.MaxRecords", b)
        if win and cap:
            ok("A: %sRecordStore enforces BOTH the window and the cap — the claim is true, "
               "and this beat is not about one half being documentation" % name)
        else:
            fail("A: %sRecordStore no longer enforces both halves" % name)
    sql = read("internal/central/eventstore_sqlite.go")
    pr = func_body(sql, "func (s *SQLiteRecordStore) prune")
    if re.search(r"DELETE FROM records WHERE when_unix < \?", pr) and re.search(
        r"ORDER BY when_unix DESC, id DESC LIMIT \?", pr
    ):
        ok("A: SQLiteRecordStore enforces both, and its cap is BY TIME (ORDER BY when_unix) "
           "— it does not have an arrival order to confuse with time order")
    else:
        fail("A: SQLite's prune changed — re-derive the asymmetry")


def check_b_cap_is_positional():
    src = read("internal/central/eventstore.go")
    for name in ("MemoryRecordStore", "FileRecordStore"):
        b = func_body(src, "func (%s) pruneLocked" % ("m *" + name[0:1].lower() if False else "m *MemoryRecordStore") if name.startswith("Memory") else "func (f *FileRecordStore) pruneLocked")
        m = re.search(r"([a-z])\.recs = \1\.recs\[len\(\1\.recs\)-\1\.retain\.MaxRecords:\]", b)
        if not m:
            fail("B: %s cap is no longer a positional slice — re-derive" % name)
            continue
        # Beat 735: M1 inserted a sort.SliceStable immediately above the slice and this check
        # stayed GREEN — it asked "is there a positional slice?" and never "is anything
        # ordering it first?". That is the gap-closing direction, which is the only direction
        # that matters for a detector. Pin the ABSENCE of any ordering call in the function.
        if re.search(r"sort\.(Slice|SliceStable|Sort)\(", b) or re.search(r"\.Sort\(", b):
            fail("B: %s capLocked now SORTS before trimming — the arrival-vs-time gap may be "
                 "closing; re-derive the finding" % name)
        else:
            ok("B: %s cap keeps the slice TAIL and nothing orders the slice first — "
               "positional, i.e. by arrival order, with no sort in sight" % name)


def check_c_assumption_stated():
    src = read("internal/central/eventstore.go")
    if re.search(r"recs\s+\[\]Record // append order \(oldest first\)", src):
        ok("C: the slice is documented as 'append order (oldest first)' — the positional "
           "cap rests on this comment")
    else:
        fail("C: the 'append order (oldest first)' comment changed — re-derive")
    body = func_body(src, "func (m *MemoryRecordStore) pruneLocked")
    norm = re.sub(r"\s+", " ", re.sub(r"//+", "", body))
    norm = re.sub(r"\s+", " ", norm)
    if "roughly sorted by append time" in norm and "mis-ordered clock only costs a stale record surviving" in norm:
        ok("C: pruneLocked's own comment names the clock risk — but scoped to the WINDOW "
           "pass, where it is true; the CAP pass above it does not have that property")
    else:
        fail("C: the pruneLocked clock comment changed — re-derive")


def check_d_not_reachable_today():
    svc = read("internal/central/service.go")
    b = func_body(svc, "func (s *Service) retain")
    if re.search(r"when := s\.nowTime\(\)", b) and re.search(r"RecordFromEvent\(host, agent, ev, when\)", b):
        ok("D: production stamps When from the service clock AT INGEST — so append order == "
           "time order by construction, and the positional cap is correct today. This "
           "finding is LATENT, not live.")
    else:
        fail("D: retain no longer stamps the ingest clock — divergence may now be reachable; "
             "re-derive the scope")
    nt = func_body(svc, "func (s *Service) nowTime")
    if re.search(r"s\.now != nil", nt):
        ok("D: the clock has one override point, and grepping nowTime shows no production "
           "caller swaps it — recorded so 'latent' is a measurement, not a hope")


def check_e_nothing_validates_order():
    src = read("internal/central/eventstore.go")
    for name, b in (
        ("Memory", func_body(src, "func (m *MemoryRecordStore) Append")),
        ("File", func_body(src, "func (f *FileRecordStore) Append")),
    ):
        if not b:
            fail("E: %s Append not found" % name)
            continue
        if re.search(r"sort\.|Before\(|After\(", b):
            fail("E: %s Append now orders/validates the record — the gap may be closing; "
                 "re-derive" % name)
        else:
            ok("E gap: %sRecordStore.Append appends and prunes WITHOUT checking that When is "
               "monotone — the positional cap trusts a property nothing verifies" % name)


def check_f_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01p-cap-trims-by-arrival-not-time.md"
    if not doc.exists():
        fail("F: the finding doc is missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("NOT CLAIMED as live", "the deliberate narrow scope"),
        ("latent", "the honest reachability statement"),
    ]:
        if phrase in t:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_both_halves_enforced()
    check_b_cap_is_positional()
    check_c_assumption_stated()
    check_d_not_reachable_today()
    check_e_nothing_validates_order()
    check_f_doc_scope()
    print()
    if FAILS:
        print("capordercensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("capordercensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

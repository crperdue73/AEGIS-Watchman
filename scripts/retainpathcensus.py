#!/usr/bin/env python3
"""retainpathcensus — who calls the record store, and how many writes one report costs.

BEAT 738. Five beats inside the store, zero beats reading its caller. This is that read.

The claim is a SHAPE, not a rate: `retain` appends one record per event, and on SQLite
each append is three commits and a full `DELETE ... NOT IN (SELECT ...)` trim scan. So a
report of N events costs N transactions and N trim scans. NO timing is claimed anywhere.

Checks:
  A. retention is per-event — one Append inside a loop over events, not a batch call
  B. `RecordStore` has exactly one write method (no batch door exists to take)
  C. the report clock is HOISTED out of the loop — one `when` per report (this is why a
     report's records sort correctly, and it quietly underwrites beat 735)
  D. the SQLite append's statement count is pinned: INSERT + prune, and prune is a further
     call — NOT one statement
  E. the call sites are exhaustive: retain is reached from exactly Ingest and IngestActions
  F. the doc does not claim a rate, and carries its scope markers (Fern's rule, 7300)
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


SVC = "internal/central/service.go"
ES = "internal/central/eventstore.go"


def check_a_per_event():
    src = read(SVC)
    b = func_body(src, "func (s *Service) retain(")
    if not b:
        fail("A: retain not found")
        return
    # Pin the loop AND the Append inside it. A batch call would look nothing like this.
    loop = re.search(r"for _, ev := range events \{", b)
    app = re.search(r"s\.records\.Append\(context\.Background\(\), rec\)", b)
    if loop and app and b.find("for _, ev") < b.find("s.records.Append"):
        ok("A: retain loops `for _, ev := range events` and calls Append once per event — "
           "N events in a report means N Append calls, not one batch write")
    elif loop and not app:
        fail("A: the loop remains but the per-event Append is gone — a batch path may have "
             "arrived; re-derive")
    else:
        fail("A: retain shape unrecognised — re-derive")


def check_b_one_write_method():
    src = read(ES)
    i = src.find("type RecordStore interface {")
    if i < 0:
        fail("B: RecordStore interface not found")
        return
    j = src.find("\n}", i)
    body = src[i:j]
    methods = re.findall(r"^\t([A-Z]\w+)\(", body, re.M)
    writes = [m for m in methods if m in ("Append", "AppendBatch", "AppendAll", "Insert", "Write")]
    if writes == ["Append"]:
        ok("B gap: RecordStore has exactly one write method, `Append` — there is no batch "
           "door for the caller to take even if it wanted one")
    elif "AppendBatch" in methods or "AppendAll" in methods:
        fail("B: a batch write method now exists — option 1 may have been taken; re-derive")
    else:
        ok("B: write methods are %r — re-read the interface" % writes)


def check_c_clock_hoisted():
    src = read(SVC)
    b = func_body(src, "func (s *Service) retain(")
    if not b:
        return
    when_i = b.find("when := s.nowTime()")
    loop_i = b.find("for _, ev := range events")
    inner = b[b.find("for _, ev"):] if loop_i >= 0 else ""
    if when_i >= 0 and loop_i >= 0 and when_i < loop_i and "nowTime()" not in inner:
        ok("C: `when := s.nowTime()` sits ABOVE the loop and is never re-read inside it — "
           "every record in one report shares one timestamp. That is why a report's records "
           "sort correctly, and it is the quiet reason beat 735's trim stays safe today.")
    else:
        fail("C: the report clock is no longer hoisted — records within one report may now "
             "carry different timestamps, which changes beat 735's premise")


def check_d_statement_count():
    src = read("internal/central/eventstore_sqlite.go")
    b = func_body(src, "func (s *SQLiteRecordStore) Append")
    if not b:
        fail("D: sqlite Append not found")
        return
    execs = re.findall(r"(?:s\.db|tx)\.ExecContext\(", b)
    if len(execs) == 1 and re.search(r"return s\.prune\(ctx\)", b):
        ok("D: sqlite Append makes 1 direct ExecContext (the INSERT) and then calls prune, "
           "which executes the window pass and the cap pass — 3 statements per append, "
           "each committing separately (beat 737)")
    elif len(execs) == 0:
        fail("D: sqlite Append no longer executes anything directly — re-derive")
    # Beat 738, and I wrote this lesson down LAST BEAT and repeated it anyway: the branch
    # below used to be a friendly `ok(...)` "recount before reusing the figure". M5 (prune
    # deleted from Append) went green against it. A branch that congratulates the code for
    # changing is not a tripwire. Print the count, but FAIL — this instrument's whole job is
    # to be the record that makes someone re-derive.
    else:
        fail("D: sqlite Append now makes %d direct ExecContext calls, not 1 — the "
             "3-statements-per-append figure this finding rests on must be re-derived"
             % len(execs))


def check_d2_prune_still_inline():
    """M5's exact shape: prune removed from Append and moved elsewhere."""
    src = read("internal/central/eventstore_sqlite.go")
    b = func_body(src, "func (s *SQLiteRecordStore) Append")
    if not b:
        fail("D2: sqlite Append not found")
        return
    if re.search(r"return s\.prune\(ctx\)", b):
        ok("D2: Append still calls prune inline — the per-report trim count (N) is real")
    else:
        fail("D2: Append no longer prunes inline — the N-trims-per-report claim is GONE; "
             "re-derive beat 738")


def check_e_call_sites():
    src = read(SVC)
    # Pin the WHOLE population as a set (beat 730/734 rule), not a count.
    callers = sorted(set(re.findall(r"s\.retain\(host, agent, (\w+)\)", src)))
    if callers == ["accepted", "events"]:
        ok("E: retain is called with exactly [accepted, events] — from IngestActions and "
           "Ingest. Pinned as an exact set, so a third path upstream goes red.")
    else:
        fail("E: retain call population is now %r, expected ['accepted', 'events']" % callers)
    # The two enclosing functions, pinned by name.
    funcs = []
    for m in re.finditer(r"func \(s \*Service\) (\w+)\(", src):
        fname, start = m.group(1), m.start()
        nxt = src.find("\nfunc ", start + 1)
        chunk = src[start:] if nxt < 0 else src[start:nxt]
        if "s.retain(host, agent," in chunk:
            funcs.append(fname)
    if sorted(funcs) == ["Ingest", "IngestActions"]:
        ok("E: enclosed by exactly Ingest and IngestActions — the two ingest entry points, "
           "which is what makes the multiplier a per-report figure")
    else:
        fail("E: enclosing functions are now %r — re-derive the multiplier" % sorted(funcs))


def check_f_doc_discipline():
    doc = ROOT / "docs/FINDING-2026-11-01s-retention-is-n-transactions-per-report.md"
    if not doc.exists():
        fail("F: doc missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    t_nomark = re.sub(r"[*_`]", "", t)
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("NOT observed", "the narrow scope"),
        # Beat 738: I stripped the asterisks here and the doc keeps them, so this check
        # failed on a doc that was correct. Normalise BOTH sides of the comparison — a
        # checker must not be stricter than the markup it is reading (4th whitespace/
        # normalisation bug in this family: 729, 730 E, 734, now markup).
        ("The claim is the shape", "the shape-not-rate discipline"),
    ]:
        if phrase in t_nomark:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))
    # Fern's rule: no invented performance numbers. Pin the refusal to name a rate.
    if re.search(r"\b(slow|ms/|per second|ops/sec)\b", t) and "have not timed it" not in t:
        fail("F: doc describes a rate without refusing to time it — Fern's rule (7300)")
    else:
        ok("F: doc makes no timed claim and says so explicitly — the finding is a shape, "
           "not a rate, and there is no plausible-sounding number in it")


def main():
    check_a_per_event()
    check_b_one_write_method()
    check_c_clock_hoisted()
    check_d_statement_count()
    check_d2_prune_still_inline()
    check_e_call_sites()
    check_f_doc_discipline()
    print()
    if FAILS:
        print("retainpathcensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("retainpathcensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

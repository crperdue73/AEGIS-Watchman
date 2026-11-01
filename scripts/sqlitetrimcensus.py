#!/usr/bin/env python3
"""sqlitetrimcensus — the SQLite append is one write made of three transactions.

BEAT 737. Finished reading all three record-store backends line by line. The SQLite one
was the last, and it is the durable one.

Two things are true and only one of them is a finding:

  1. The header comment's three claims (indexed, WAL cross-process, pure-Go no-CGO) all
     CHECK OUT. Asserted here as a measurement, not taken on faith — knowing the sound
     parts is as load-bearing as knowing the broken ones (beat 732 rule).
  2. `Append` = INSERT (commits), then `prune` = two more ExecContext calls (each commits).
     Three autocommit transactions with `SetMaxOpenConns(4)`. No BEGIN anywhere in the file.

Checks:
  A. the header's three claims are actually implemented (indexes, WAL DSN, pure-Go driver)
  B. Insert and prune are SEPARATE statements — no transaction wraps them
  C. the cap delete uses `NOT IN (SELECT ... LIMIT ?)`, so it runs post-insert and targets
     MaxRecords, leaving a MaxRecords+1 state between statements
  D. the connection pool is >1 while writes are multi-statement — the interleaving premise
  E. NO transaction API is used anywhere in the file (pinned absence, beat 735's lesson)
  F. the other two backends append+trim under one lock — the asymmetry pinned
  G. doc scope markers
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


SQL = "internal/central/eventstore_sqlite.go"


def check_a_header_claims_hold():
    src = read(SQL)
    if re.search(r"CREATE INDEX IF NOT EXISTS idx_records_host_when ON records \(host_id, when_unix\)", src):
        ok("A: idx_records_host_when (host_id, when_unix) exists — Query's host+time filter "
           "is a range scan, exactly as the header claims")
    else:
        fail("A: the host/when index is gone — the header's first claim is now false")
    if re.search(r"CREATE INDEX IF NOT EXISTS idx_records_sev ON records \(severity_rank\)", src):
        ok("A: idx_records_sev exists — second index claimed and present")
    else:
        fail("A: the severity index is gone")
    if "journal_mode(WAL)" in src and "busy_timeout(5000)" in src:
        ok("A: WAL + busy_timeout are in the DSN — the cross-process locking claim is real, "
           "and it is the specific thing the JSON FileStore lacks")
    else:
        fail("A: WAL/busy_timeout gone from the DSN — cross-process claim now false")
    if "_ \"modernc.org/sqlite\"" in src and "no CGO" in src:
        ok("A: the driver is modernc.org/sqlite (pure Go) and the comment says why — "
           "'clone and build works anywhere' is the actual reason, not a decoration")
    else:
        fail("A: driver changed — the no-CGO claim needs re-deriving")


def check_b_append_is_split():
    src = read(SQL)
    b = func_body(src, "func (s *SQLiteRecordStore) Append")
    if not b:
        fail("B: Append not found")
        return
    has_insert = re.search(r"s\.db\.ExecContext\(ctx, ins", b) is not None
    calls_prune = re.search(r"return s\.prune\(ctx\)", b) is not None
    if has_insert and calls_prune:
        ok("B: Append is INSERT then `return s.prune(ctx)` — two separate commits. A prune "
           "failure returns an error for a record that already persisted.")
    else:
        fail("B: Append shape changed — re-derive")


def check_c_cap_runs_post_insert():
    src = read(SQL)
    p = func_body(src, "func (s *SQLiteRecordStore) prune")
    if not p:
        fail("C: prune not found")
        return
    m = re.search(
        r"DELETE FROM records WHERE id NOT IN \(\s*SELECT id FROM records ORDER BY when_unix DESC, id DESC LIMIT \?", p
    )
    if m:
        ok("C: the cap is `DELETE ... WHERE id NOT IN (SELECT ... LIMIT ?)` — it selects the "
           "newest MaxRecords to KEEP and deletes the rest, evaluated AFTER the insert "
           "committed. So the store transiently holds MaxRecords+1.")
    else:
        fail("C: the cap delete changed — re-derive the off-by-one")
    # The off-by-one is a consequence of the ORDER: insert-then-prune. Pin the order.
    app = func_body(src, "func (s *SQLiteRecordStore) Append")
    if app.find("ins,") >= 0 and app.find("s.prune(ctx)") > app.find("ins,"):
        ok("C: the insert precedes the prune in Append — that order is what makes the "
           "MaxRecords+1 window exist; prune-before-insert would not have it")
    else:
        fail("C: insert/prune order changed — re-derive the off-by-one")


def check_d_pool_size():
    src = read(SQL)
    m = re.search(r"db\.SetMaxOpenConns\((\d+)\)", src)
    if m:
        n = int(m.group(1))
        if n > 1:
            ok("D: SetMaxOpenConns(%d) — more than one connection while a write spans three "
               "statements, so two writers can interleave between them. That is the race "
               "premise, and it is a deliberate choice (lock churn vs concurrency)." % n)
        else:
            fail("D: pool is now size %d — the interleaving premise is gone; re-derive" % n)
    else:
        fail("D: no SetMaxOpenConns — default pool, premise still holds but unpinned")


def check_e_no_transaction():
    src = read(SQL)
    # Pin the ABSENCE (beat 735: a gap-closing branch must never pass quietly).
    if re.search(r"db\.Begin|BeginTx|\.ExecContext\(ctx, \"BEGIN|SAVEPOINT", src):
        fail("E: a transaction now exists in the sqlite store — the finding may be fixed; "
             "re-derive")
    else:
        ok("E gap: NO transaction API is used anywhere in eventstore_sqlite.go — one logical "
           "write is three autocommit statements, and nothing says so in the header")


def check_f_asymmetry():
    src = read("internal/central/eventstore.go")
    for name, v in (("Memory", "m"), ("File", "f")):
        a = func_body(src, "func (%s *%sRecordStore) Append" % (v, name))
        if not a:
            fail("F: %s Append not found" % name)
            continue
        if re.search(r"%s\.pruneLocked\(%s\.now\(\)\)" % (v, v), a):
            ok("F: %sRecordStore appends AND trims inside one mutex-held call — no window "
               "between them. Three backends, three notions of when the cap applies." % name)
        else:
            fail("F: %s Append no longer trims inline" % name)


def check_g_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01r-sqlite-append-is-three-transactions.md"
    if not doc.exists():
        fail("G: doc missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("NOT CLAIMED as observed", "the deliberate narrow scope"),
        ("idempotent", "why the cap race is survivable rather than corrupting"),
    ]:
        if phrase in t:
            ok("G: doc carries %s" % label)
        else:
            fail("G: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_header_claims_hold()
    check_b_append_is_split()
    check_c_cap_runs_post_insert()
    check_d_pool_size()
    check_e_no_transaction()
    check_f_asymmetry()
    check_g_doc_scope()
    print()
    if FAILS:
        print("sqlitetrimcensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("sqlitetrimcensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

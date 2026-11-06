#!/usr/bin/env python3
"""retainfailcensus — a failing record store is logged 250 times and reported as success.

BEAT 746, from Callie's 7406: "what changed tonight isn't my resolve, it's the shell."
Applied to Service.retain: the only thing that changes on a store failure is a log line.

MEASURED (beat 746, real Service.Ingest, Append always returns "database or disk is full"):
  RESULT events=250 append_attempts=250 log_lines=250 ingest_err=<nil>
Every append fails, ingest returns nil, the baseline is committed, and the fleet history
is silently lost. The log grows one line PER EVENT — 1000-event cycles (beat 745) mean
1000 lines per cycle.

Checks:
  A. retain appends per event and logs per failure (the volume)
  B. retain returns nothing and Ingest returns nil regardless — no error escapes
  C. the doc comment's guarantee belongs to the baseline write, not the record store
  D. the latent nil-logger silence (unreachable in main.go, real for embedders)
  E. what is NOT lost: the alert still fires, resolveActors is correctly ordered
  F. the doc keeps the measured row, the de-dup against 745, and the not-claimed section
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


def check_a_per_event():
    b = func_body(read("internal/central/service.go"), "func (s *Service) retain(")
    if not b:
        fail("A: retain not found")
        return
    if re.search(r"for _, ev := range events \{", b) and re.search(r"rec := RecordFromEvent\(", b):
        ok("A gap: retain appends ONE RECORD PER EVENT in a loop — no batch, no early "
           "abort on first failure. Measured: 250 events -> 250 attempts -> 250 log lines.")
    else:
        fail("A: the append loop changed — re-derive")
    if re.search(r's\.logger\.Printf\("central: record store append failed: %v", err\)', b):
        ok("A gap: and it logs per failure inside that loop. At the 1000-event cycles "
           "measured in beat 745, a full disk writes 1000 near-identical lines PER CYCLE — "
           "a flood that buries the one fact worth reading: the store is down.")
    else:
        fail("A: the per-failure log changed — re-derive")


def check_b_no_error_escapes():
    b = func_body(read("internal/central/service.go"), "func (s *Service) retain(")
    # Signature check, not body (beat 741): retain must return NOTHING.
    sig = re.search(r"func \(s \*Service\) retain\([^)]*\)\s*(\S*)", b)
    if sig and sig.group(1).strip() == "{":
        ok("B gap: `retain` returns NOTHING — quoted from the signature, so the error is "
           "dropped at the call site by construction, not by an ignored return.")
    else:
        fail("B: retain's signature changed (%r) — if it now returns an error, re-derive"
             % (sig.group(1) if sig else "?"))
    ing = func_body(read("internal/central/service.go"), "func (s *Service) Ingest(")
    if re.search(r"return &IngestResult\{FirstSeen: !found, Events: events\}, nil", ing):
        ok("B gap: Ingest ends `return &IngestResult{...}, nil` — MEASURED with a store "
           "whose Append always fails: ingest_err=<nil>. The collector gets a 200 and a "
           "committed baseline while its history went nowhere.")
    else:
        fail("B: Ingest's return changed — re-derive")


def check_c_comment_overreach():
    src = read("internal/central/service.go")
    if re.search(r"the store is already correct", src):
        ok("C gap: the comment says \u201cDelivery failures are logged, never fatal: the "
           "collector still gets its events, and the store is already correct.\u201d That "
           "guarantee belongs to the BASELINE write, which happens first. It does not hold "
           "for the record store, where retention is the only copy — a claim extended over "
           "a subsystem where it is false.")
    else:
        fail("C: the comment was rewritten — re-read whether the overreach is gone")


def check_d_nil_logger():
    src = read("internal/central/service.go")
    n = len(re.findall(r"err != nil && s\.logger != nil", src))
    if n >= 2:
        ok("D latent: %d sites guard with `err != nil && s.logger != nil` — with NO logger "
           "installed, both alert and retention failures are completely silent. Not a bug: "
           "cmd/central/main.go:56 always sets one. Recorded because NewService defaults "
           "logger to nil (=46-48), so any embedding without a logger inherits silent data "
           "loss, and no test in the package would catch it." % n)
    else:
        ok("D: the nil-logger guard is gone (%d sites) — if a logger is now required at "
           "construction, this latent note is obsolete; update the doc" % n)
    if re.search(r"return &Service\{store: store, MaxDiffBytes: 4096\}", src):
        ok("D latent: and NewService leaves logger nil, confirming the default is the silent one")
    else:
        fail("D: NewService's default changed — re-check the latent note")


def check_e_sound_parts():
    b = func_body(read("internal/central/service.go"), "func (s *Service) retain(")
    ing = func_body(read("internal/central/service.go"), "func (s *Service) Ingest(")
    if re.search(r"s\.resolveActors\(events\)\s*\n\s*s\.retain\(host, agent, events\)\s*\n\s*if s\.alerter != nil", ing):
        ok("E sound: resolveActors runs BEFORE retain, so a stored record carries the "
           "resolved uid username; and the alert fires regardless of retention outcome — "
           "detection is not lost here, only history.")
    else:
        fail("E: the resolve/retain/alert ordering changed — re-derive")


def check_f_doc():
    doc = ROOT / "docs/FINDING-2026-11-02d-a-failing-record-store-is-reported-as-success.md"
    if not doc.exists():
        fail("F: doc missing")
        return
    collapse = lambda x: re.sub(r"\s+", " ", re.sub(r"[*_`]", "", x))
    t = collapse(doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("log_lines=250 ingest_err=<nil>", "the measured row"),
        ("Why this is not the same as beat 745", "the de-dup against 745"),
        ("unreachable in the shipped binary", "the latent-trap honesty"),
        ("What I am NOT claiming", "the not-claimed section"),
        ("belongs to the baseline write", "the comment-overreach finding"),
    ]:
        if collapse(phrase).lower() in t.lower():
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_per_event()
    check_b_no_error_escapes()
    check_c_comment_overreach()
    check_d_nil_logger()
    check_e_sound_parts()
    check_f_doc()
    print()
    if FAILS:
        print("retainfailcensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("retainfailcensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

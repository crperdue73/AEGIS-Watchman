#!/usr/bin/env python3
"""ingestceilingcensus — the ingest deadline, and the one step exempt from it.

BEAT 739. I opened this beat with a wrong headline ("nothing bounds how long a request may
run") and killed it before posting by reading http.go. This instrument encodes the
CORRECTED claim so the wrong one can never quietly be re-derived from my notes:

  Both ingest handlers build a 10s deadline (http.go:205, :250 -> context.go:11-13).
  `retain` then passes context.Background() to every Append (service.go:89) — a fresh
  context that the deadline cannot cancel.

Checks:
  A. both ingest handlers set a 10s request deadline (the fact that killed my first draft)
  B. retain's Append uses context.Background(), NOT the request ctx — pinned by literal
  C. the deadline is threaded into Ingest/IngestActions (so the ceiling is real for those)
  D. alert receives the request ctx; retain does NOT — the asymmetry, both directions
  E. the server's socket timeouts: pin which are set and which are zero, WITHOUT claiming
     the zero ones are an unbounded-time bug (they aren't, given A). Any CHANGE in this set
     FAILS — a branch that merely notes a change is not a tripwire (3rd time this class
     bit me: 736 M1, 738 M5, 739 M4)
  F. the doc keeps its correction and does not carry the retracted headline as a live claim
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


def check_a_deadline_set():
    src = read("internal/central/http.go")
    hits = re.findall(r"contextWithTimeout\(r, 10\*time\.Second\)", src)
    if len(hits) == 2:
        ok("A: both ingest handlers wrap the request in a 10s deadline (http.go:205, :250) — "
           "this is the fact that falsified my first headline this beat. There IS a ceiling.")
    elif len(hits) == 0:
        fail("A: no 10s request deadline in http.go — my correction is now wrong; re-derive")
    else:
        fail("A: %d deadline sites, expected exactly 2 — re-derive" % len(hits))
    ctx = read("internal/central/context.go")
    if re.search(r"return context\.WithTimeout\(r\.Context\(\), d\)", ctx):
        ok("A: contextWithTimeout derives from r.Context() — so it is the request's deadline, "
           "cancelled when the client goes away or the timer fires")
    else:
        fail("A: contextWithTimeout no longer derives from the request context")


def check_b_retain_uses_background():
    src = read("internal/central/service.go")
    b = func_body(src, "func (s *Service) retain(")
    if not b:
        fail("B: retain not found")
        return
    if "s.records.Append(context.Background(), rec)" in b:
        ok("B gap: retain calls Append with context.Background() — a FRESH context, so the "
           "handler's 10s deadline cannot cancel retention. Deliberate (a record write must "
           "survive a client hangup) and also the reason retention is the exempt step.")
    elif re.search(r"s\.records\.Append\(ctx, rec\)", b):
        fail("B: retain now takes/uses a request ctx — the exemption may be fixed; re-derive")
    else:
        fail("B: retain's Append context form unrecognised — re-derive")


def check_c_deadline_threads_in():
    src = read("internal/central/http.go")
    ins = func_body(src, "func (s *Service) handleIngest")
    act = func_body(src, "func (s *Service) handleActions")
    for name, b, call in (("Ingest", ins, "s.Ingest(ctx,"), ("IngestActions", act, "s.IngestActions(ctx,")):
        if not b:
            fail("C: handler body for %s not found" % name)
            continue
        if call in b and "defer cancel()" in b:
            ok("C: %s is called with the deadline ctx and cancel is deferred — the ceiling "
               "covers baseline read, drift, baseline write and alerting" % name)
        else:
            fail("C: %s no longer receives the deadline ctx — recount what the 10s covers" % name)


def check_d_asymmetry():
    """The asymmetry, pinned from BOTH sides (beat 730/735: presence alone is not a check)."""
    src = read("internal/central/service.go")
    ins = func_body(src, "func (s *Service) Ingest(")
    if not ins:
        fail("D: Ingest not found")
        return
    if "s.retain(host, agent, events)" in ins:
        ok("D: the retain call passes no ctx at all — the exemption starts there, not inside "
           "Append")
    else:
        fail("D: retain call shape changed")
    if "s.alert(ctx, host, agent, events)" in ins:
        ok("D: alert DOES receive the request ctx — so the network sinks are bounded while "
           "retention is not. That asymmetry is the finding.")
    else:
        fail("D: alert no longer receives the request ctx — re-derive the asymmetry")


def check_e_socket_timeouts():
    src = read("cmd/central/main.go")
    i = src.find("srv := &http.Server{")
    j = src.find("\n\t}", i)
    block = src[i:j] if i >= 0 else ""
    if "ReadHeaderTimeout: 10 * time.Second" in block:
        ok("E: ReadHeaderTimeout is set — the slowloris guard, and the only socket timeout "
           "in the binary")
    else:
        fail("E: ReadHeaderTimeout gone — the stated guard no longer exists")
    for field in ("WriteTimeout", "ReadTimeout", "IdleTimeout"):
        if re.search(field + r"\s*:", block):
            # Beat 739: THIRD consecutive beat where I wrote a gap-closing branch as a note.
            # 736 M1 (fresh slice), 738 M5 (prune leaves Append), now M4 (WriteTimeout added).
            # The reusable lesson is not "be careful", it is: this instrument's exit status is
            # the only thing anyone downstream reads, so every branch that observes a CHANGE
            # in the code under observation must FAIL. `ok()` is for facts that still hold.
            fail("E: %s is now set on the server — the socket-timeout observation this "
                 "finding records has changed; re-derive (do NOT reuse the old note)" % field)
    # Pin the ABSENCE as an observation, but NOT as a bug claim (that was my wrong draft).
    if not re.search(r"WriteTimeout\s*:", block) and not re.search(r"IdleTimeout\s*:", block):
        ok("E note: WriteTimeout and IdleTimeout are zero — recorded as defence-in-depth "
           "hygiene, NOT as an unbounded-time bug, because check A shows the 10s request "
           "deadline already returns the handler")


def check_f_doc_honesty():
    doc = ROOT / "docs/FINDING-2026-11-01t-ingest-has-no-time-ceiling.md"
    if not doc.exists():
        fail("F: doc missing")
        return
    raw = doc.read_text()
    t = re.sub(r"\s+", " ", raw)
    for phrase, label in [
        ("CORRECTION — same beat, and it guts the headline", "the correction header"),
        ("That is FALSE", "the retracted claim kept visible"),
        ("retention is exempt from the ingest deadline", "the corrected title"),
    ]:
        if phrase in t:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))
    # The wrong ONE-LINE must not survive as the live claim.
    if re.search(r"\*\*One line:\*\* `cmd/central` sets \*\*only `ReadHeaderTimeout`", raw):
        fail("F: the retracted one-line is still presented as the live claim")
    else:
        ok("F: the retracted one-line is not presented as the live claim — the rewritten "
           "one-line names the exemption instead")


def main():
    check_a_deadline_set()
    check_b_retain_uses_background()
    check_c_deadline_threads_in()
    check_d_asymmetry()
    check_e_socket_timeouts()
    check_f_doc_honesty()
    print()
    if FAILS:
        print("ingestceilingcensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("ingestceilingcensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

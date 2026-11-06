#!/usr/bin/env python3
"""spoolackcensus -- a 202 that accepted nothing is counted as shipped.

BEAT 751. The spool's comment promises "only removed once central has accepted the batch ...
costs latency, never evidence." Measured: two batches central accepted ZERO events from are
reported as shipped and deleted from disk.

The defect is not that unnamed events are dropped -- dropping garbage is correct. It is that
the drop is invisible to the sender, and the spool deletes evidence on the strength of a 202.

Checks:
  A. central still drops unnamed events silently and returns nil (the DROP is real)
  B. the handler still answers 202 for a total drop (the drop is INVISIBLE)
  C. Drain still advances on nil + counts len(r.Events) (the spool still TRUSTS the 202)
  D. the spool comment still makes the "only once accepted / never evidence" promise
  E. sound parts: zero-event request 400s, missing host.id 400s, Drain pauses on real error
  F. doc keeps the measured rows, the not-claimed section, and the options
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


def body(path, func):
    """Scope a check to its own function body (beat 733) -- never the whole file."""
    text = Path(ROOT / path).read_text(errors="replace")
    m = re.search(r"^func \([^)]*\) %s\(" % re.escape(func), text, re.M)
    if not m:
        return None
    start = m.start()
    depth = 0
    for i in range(text.index("{", start), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def check_a():
    b = body("internal/central/service.go", "IngestActions")
    if b is None:
        fail("A: IngestActions not found -- re-read the file")
        return
    if re.search(r'if\s+ev\.Event\s*==\s*""\s*\{[^}]*continue', b, re.S):
        ok("A DROP: unnamed events are skipped with `continue` inside IngestActions -- dropping "
           "garbage rather than alerting on it is the CORRECT behaviour and is not the defect.")
    else:
        fail("A: the unnamed-event drop changed shape -- re-read IngestActions")
    # 2026-11-06 (beat 836): CLAUSE A INVERTED, ON PURPOSE.
    #
    # This check used to ASSERT the defect: "a total drop still returns 0, nil" and go red if it
    # changed. That is an instrument pointed at a gap, and when the gap closed it fired correctly --
    # which means the gap is gone and the assertion has to turn around with it.
    #
    # Dad picked option 1 + 3 of finding 2026-11-02g. IngestActions now returns ErrTotalDrop on a
    # total drop and the handler answers 400. If either of those ever reverts, EVIDENCE LOSS comes
    # back -- so the check now fails when the refusal is MISSING, not when it appears. A file that
    # kept the old direction and was merely "updated to green" would be a check that can never fail
    # again, which is the thing this whole project is about.
    m = re.search(r'if\s+len\(accepted\)\s*==\s*0\s*\{[^}]*\}', b, re.S)
    if m and re.search(r"ErrTotalDrop", m.group(0)):
        ok("A REFUSED: a TOTAL drop now returns an error wrapping ErrTotalDrop -- quoted from "
           "inside the guard, so the caller can distinguish 'all dropped' from 'nothing to do' "
           "(beat 743's rule: quote the return INSIDE the guard). Finding 2026-11-02g option 1.")
    elif m and re.search(r"return\s+0,\s*nil", m.group(0)):
        fail("A: a TOTAL drop returns `0, nil` again -- finding 2026-11-02g has REOPENED and a "
             "collector's spool will delete a batch central retained nothing from")
    else:
        fail("A: the total-drop guard changed shape -- re-read IngestActions")


def check_b():
    text = (ROOT / "internal/central/http.go").read_text(errors="replace")
    if re.search(r"writeErr\(w,\s*http\.StatusBadRequest,\s*\"missing events\"\)", text):
        ok("B: a request with no events at all is refused 400 `missing events` -- the zero-EVENT "
           "case is guarded.")
    else:
        fail("B: the missing-events guard is gone -- re-read the handler")
    # The dangerous shape: a non-empty events list that accepted nothing still 202s.
    if re.search(r"StatusAccepted", text) and not re.search(r"accepted\s*==\s*0", text):
        ok("B GAP: the handler answers 202 Accepted without ever inspecting the accepted count -- "
           "`n` is encoded in the body for the caller to read, and nothing requires the caller "
           "to read it. 202 for 'none accepted' is byte-identical to 202 for 'all accepted'.")
    else:
        fail("B: the handler now inspects the accepted count -- the gap is CLOSING")


def check_c():
    b = body("internal/collector/spool.go", "Drain")
    if b is None:
        fail("C: Drain not found")
        return
    if re.search(r"if\s+err\s*:=\s*submit\([^)]*\);\s*err\s*!=\s*nil\s*\{", b):
        ok("C GAP: Drain advances on `submit(...) == nil` only -- a 202 is nil, so a batch central "
           "took ZERO events from advances the same way a fully-accepted batch does.")
    else:
        fail("C: the Drain submit guard changed shape -- re-read it")
    if re.search(r"shipped\s*\+=\s*len\(r\.Events\)", b):
        ok("C GAP: and it counts `shipped += len(r.Events)` -- the number SUBMITTED, not the "
           "number central reported taking. Progress is measured on the sending side.")
    else:
        fail("C: the shipped accounting changed -- re-read Drain")
    if re.search(r"s\.rewrite\(recs\[idx:\]\)", b):
        ok("C GAP: then `s.rewrite(recs[idx:])` keeps only what was NOT submitted -- so an "
           "unaccepted batch is deleted from disk exactly like an accepted one.")
    else:
        fail("C: the rewrite call changed -- re-read Drain")


def check_d():
    text = (ROOT / "internal/collector/spool.go").read_text(errors="replace")
    flat = re.sub(r"\s+", " ", re.sub(r"^//+ ?", "", text, flags=re.M))
    for phrase, label in [
        ("only removed once central has accepted", "the acceptance promise"),
        ("costs latency, never evidence", "the 'never evidence' claim"),
    ]:
        if phrase in flat:
            ok("D: the spool comment still promises \u201c%s\u201d \u2014 the comment is the spine "
               "of this finding: a true statement about the mechanism, made false by what the "
               "mechanism treats as proof." % label)
        else:
            fail("D: the comment lost its %r promise -- the finding's framing must be re-derived"
                 % label)


def check_e():
    text = (ROOT / "internal/central/http.go").read_text(errors="replace")
    if re.search(r'if\s+req\.Host\.ID\s*==\s*""\s*\{[^}]*StatusBadRequest', text, re.S):
        ok("E sound: a missing host.id is refused 400 -- measured this beat, not assumed.")
    else:
        fail("E: the host.id guard is gone")
    b = body("internal/collector/spool.go", "Drain")
    if b and re.search(r"log\.Printf\(\"collector: spool drain paused", b) and re.search(r"break", b):
        ok("E sound: on a REAL error Drain logs, breaks, and preserves every record from idx on -- "
           "the retry mechanism the comment describes genuinely works. The gap is only that 202 "
           "is treated as proof of ACCEPTANCE when it is proof of RECEIPT.")
    else:
        fail("E: the pause-on-error path changed -- the sound part must be verified, not assumed")


def check_f():
    doc = ROOT / "docs/FINDING-2026-11-02g-a-batch-central-accepted-nothing-counts-as-shipped.md"
    if not doc.exists():
        fail("F: doc missing")
        return
    # Beat 751 own-mistake: stripping * _ ` from BOTH sides mangled the literal `<nil>` I was
    # checking for, so the document was accused of losing a line it plainly contains. Normalise
    # whitespace on both sides (beat 745) -- but never strip characters out of measured output,
    # because measured output is the payload, not markup.
    t = re.sub(r"\s+", " ", doc.read_text())
    # Two kinds of phrase need two kinds of normalisation: measured rows are payload (whitespace
    # only), prose rows may carry **bold** (whitespace + markup). Using one rule for both produced
    # two false FAILs in a row on a document that was correct both times.
    def prose(x):
        return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", x)).lower()

    for phrase, label in [
        ("shipped=2 err=<nil> spooled_batches_after=0", "the measured drain row"),
        ("code=202 body={\"accepted\":0}", "the measured endpoint row"),
        ("costs latency, never evidence", "the quoted promise"),
        ("What this is NOT", "the not-claimed section"),
        ("the delivery contract is Dad's call", "the ownership boundary"),
        ("The right answer is probably 1 + 3", "the un-picked options"),
    ]:
        hay = t.lower() if "RESULT" in label or "measured" in label else prose(t)
        needle = (re.sub(r"\s+", " ", phrase).lower() if "RESULT" in label or "measured" in label
                  else prose(phrase))
        if needle in hay:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a()
    check_b()
    check_c()
    check_d()
    check_e()
    check_f()
    print()
    if FAILS:
        print("spoolackcensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("spoolackcensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

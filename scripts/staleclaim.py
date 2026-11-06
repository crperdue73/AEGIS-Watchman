#!/usr/bin/env python3
"""staleclaim.py -- find comments that explain why something cannot be checked, after it could be.

WHY THIS EXISTS (beat 780)
--------------------------
Three findings in three consecutive beats turned on the same shape:

  beat 776  `argv_semantics_test.go` header said "NEVER BEEN RUN". It was true when written,
            before the Oct 08 migration took the Go toolchain. The toolchain came back 2026-11-01
            (beat 740) and the header sat there for two days describing a world that had ended.
            The tests underneath it passed on first execution.
  beat 778  `needleprobe.py` carried the same host excuse.
  beat 779  `argvfixture.py`'s LIMITS section ended "The Go toolchain is the only path to
            measurement and this host does not have it."

Each was found by hand, one at a time, while looking for something else. That is not a process,
that is luck with a good memory. A note that explains why a check is impossible outlives the
condition that made it impossible, and nothing in this repo was watching for that.

WHAT THIS CHECKS
----------------
It does not grep for words and call that a finding -- a phrase list is a guess about how people
write. It asks a question that has an answer:

    For every file that carries an "I cannot verify this here" claim, is the CAUSE of that claim
    still present?

The causes are enumerated in CAUSES below: a missing toolchain, an absent binary, a missing branch.
Each cause has a `still_true()` predicate that runs against THIS box, right now. A file whose claim
is marked `cause: toolchain` is only stale if the toolchain IS here.

So the guard has three outcomes per claim, and they are genuinely different:
    OK     -- the claim's cause is real; the note is honest and still needed.
    STALE  -- the cause is gone; the note is describing a world that ended. Fails.
    UNKNOWN-- the file names a cause that is not in CAUSES. Fails: an unregistered cause cannot be
              watched, and silently ignoring it is how the three above survived.

THAT THIRD OUTCOME IS THE POINT. A guard that only knows the causes I thought of would have missed
the fourth one. A guard that says "I don't know what you mean by `cause: wifi`" cannot be outrun by
inventing a new excuse.

HOW A CLAIM IS DECLARED
------------------------
Annotations in the file, on their own line:

    STALECLAIM cause=toolchain  since=2026-10-08  checked=2026-11-03

`since` is when the cause appeared. `checked` is when a human last confirmed the note still applies.
Both are required: an undated claim cannot be aged, and a claim with no `since` might be describing
something that was never true. The `since`/`checked` pair is what makes this auditable rather than
decorative.

USAGE
    python3 scripts/staleclaim.py            # report
    python3 scripts/staleclaim.py --check    # exit 1 on any STALE or UNKNOWN
"""
import datetime
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The annotation must sit inside a COMMENT (or a Go doc comment), never in prose. My first version
# matched the literal in this file's own docstring and reported a phantom unknown cause -- the guard
# writing its own finding, which is the same failure shape as a fixture that supplies the answer
# (beat 650). Requiring a comment leader is what makes the annotation a code fact.
ANN = re.compile(r"^\s*(?:#|//)\s*STALECLAIM\s+(.*)")
KV = re.compile(r"(\w+)=([^\s]+)")


def _go_toolchain_absent():
    """The CAUSE, not its absence-of-absence. The Oct 08 migration removed the toolchain; it came
    back 2026-11-01 (beat 740). A claim saying "no Go toolchain here" is live only while this is
    True -- my first draft returned True when the toolchain WAS present, so every claim read as
    'still live' on the exact box where it was stale. The predicate must answer the cause's
    question, not the convenient one.
    """
    if os.path.exists("/usr/local/go/bin/go"):
        return False
    return shutil.which("go") is None


def _never(why):
    return lambda: False


CAUSES = {
    # name          -> (still_true?, description)
    "toolchain": (_go_toolchain_absent,
                  "Go toolchain / gofmt / make absent from this host"),
    "ebpf": (lambda: not (os.path.exists("/sys/fs/bpf") and bool(shutil.which("clang"))),
             "no bpffs mount or no clang/libbpf-dev, so eBPF code cannot be loaded or built here"),
    # A cause that can never be true again is how a claim gets permanently excused. Anything
    # registered here must be a live predicate, not a constant -- see the UNKNOWN branch below.
    "none": (_never(""), "placeholder: use only if the note has no external cause (should be rare)"),
}


def fields_ok(c, key):
    return bool(c.get(key))


def walk():
    skip = {".git", "__pycache__", "node_modules", "testdata"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        rel = os.path.relpath(base, ROOT)
        if rel.startswith("docs") or rel.startswith("memory"):
            # Docs describe history and are supposed to contain old claims in past tense.
            # WORKLOG is a log. The code is what must not lie about the present.
            continue
        for f in files:
            if f.endswith((".py", ".sh", ".go")):
                yield os.path.join(base, f)


def scan():
    claims, unknowns = [], []
    for path in walk():
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = ANN.search(line)
            if not m:
                continue
            fields = dict(KV.findall(m.group(1)))
            cause = fields.get("cause")
            rel = os.path.relpath(path, ROOT)
            if cause not in CAUSES:
                unknowns.append((rel, i, cause, line.strip()))
                continue
            claims.append({
                "has_pending": "pending" in fields,
                "pending": fields.get("pending", ""),
                "next": fields.get("next", ""),
                "resolved": fields.get("resolved", ""),
                "resolved_by": fields.get("by", ""),
                "file": rel,
                "line": i,
                "cause": cause,
                "since": fields.get("since", ""),
                "checked": fields.get("checked", ""),
            })
    return claims, unknowns


# A deferral may sit queued for a while -- that is patience. Past this it is avoidance.
# MEASURED IN DAYS, NOT BEATS. My first version subtracted the day-of-month from the WORKLOG beat
# count and reported "pending=decide for 792 beats", because `checked=2026-11-03` parses to 3 and the
# beat counter reads 795. The number was garbage and -- worse -- it LOOKED like a measurement, which
# is why four mutants aimed at it failed to fire: there was nothing coherent underneath to break.
# `checked=` holds a DATE, so the only honest comparison is date against date.
PENDING_MAX_DAYS = 14


def days_pending(checked):
    """Days between the `checked=` date and today. None if it cannot be parsed.

    None means "expiry cannot judge this claim" -- it is reported as unparseable rather than
    silently treated as fresh, because a claim that reads as fresh forever is the exact defect this
    check exists to catch.
    """
    try:
        when = datetime.date.fromisoformat(checked)
    except (ValueError, TypeError):
        return None
    return (datetime.date.today() - when).days


def main():
    check = "--check" in sys.argv
    claims, unknowns = scan()

    stale, undated, ok, pending, malformed, resolved, expired, baddate = [], [], [], [], [], [], [], []
    for c in claims:
        if not (c["since"] and c["checked"]):
            undated.append(c)
            continue
        if "pending" in c and c["pending"]:
            # A PENDING claim is a known-queued verification, not a pass. It is reported loudly on
            # every run and does not fail the gate -- because a gate entry that is permanently red
            # is one people learn to ignore and then delete. The honesty mechanism is that the line
            # prints every single time, so the queue cannot fade from view.
            # BEAT 796: a pending= with no expiry is a decision that never comes due. The queue
            # printed forever on the theory that visibility is enough -- but four beats of me
            # reading "pending=decide" and doing nothing is the measurement that disproves it.
            # `next=` says WHAT would clear it; that was never the missing piece. What was missing
            # is WHEN. A claim that has been pending past its own declared beat is now a failure,
            # not a reminder. This is the queued hardening from beat 792's NEXT list, built
            # because I was the evidence it was needed.
            age = days_pending(c.get("checked", ""))
            if c.get("pending") and age is None:
                baddate.append(c)
                continue
            if False:
                # N5 in the beat-796 mutation table: a claim whose `checked=` cannot be parsed was
                # being bucketed as fresh, so a typo'd date disables its own expiry forever. Bad
                # dates must fail loudly, and they must fail in the SUMMARY too -- otherwise the
                # malformed line prints and the count says everything is fine (the safe-lie shape).
                malformed.append(c)
            elif c.get("pending") and age > PENDING_MAX_DAYS:
                expired.append(c)
            elif not c.get("next"):
                # A deferral must say what would clear it, and must say so TO ITS OWN NAME. My first
                # version pushed this into `undated`, which printed nothing about the missing `next`
                # -- measured: M1 removed next= and the summary merely shifted a number. A guard
                # that silently reclassifies a malformed claim as another kind of bad claim is
                # hiding the defect it just found.
                malformed.append(c)
            else:
                pending.append(c)
            continue
        if c["resolved"]:
            # A resolved claim is history: the cause expired and the work was delivered. It is
            # reported separately, not counted as a failure -- but a `resolved` with no `by=` is
            # an assertion with no artifact behind it, so that IS a failure.
            if not c["resolved_by"]:
                undated.append(c)
            else:
                resolved.append(c)
            continue
        alive, _desc = CAUSES[c["cause"]]
        if alive():
            ok.append(c)
        else:
            stale.append(c)

    for c in expired:
        print("FAIL %s:%d has been pending=%s since %s (%d days, max %d) — a deferral with no "
              "deadline is a decision that quietly stops being one. Decide it: %s"
              % (c["file"], c["line"], c["pending"], c["checked"],
                 days_pending(c["checked"]), PENDING_MAX_DAYS, c["next"]))

    for c in baddate:
        # A separate message, because it is a separate defect. N5 in the mutation table showed this
        # branch printing "declares pending= but no next=" for a claim that HAD a next= and a corrupt
        # date -- a failure message that names the wrong fault sends the reader to the wrong line.
        print("FAIL %s:%d has pending=%s but checked=%r is not an ISO date — a claim whose age cannot "
              "be computed can never expire, which is the defect this check exists to catch"
              % (c["file"], c["line"], c["pending"], c["checked"]))

    for c in malformed:
        print("FAIL %s:%d declares pending=%s but no next= — a deferred verification must say what "
              "would clear it" % (c["file"], c["line"], c["pending"]))

    for c in pending:
        print("PENDING %s:%d cause=%s is gone (since %s) and this note is NOT yet re-verified — "
              "tracked, not ignored. Next: %s"
              % (c["file"], c["line"], c["cause"], c["since"], c["next"]))

    for c in resolved:
        # A resolved claim says the CAUSE EXPIRED and the work was delivered. Printing "still live"
        # here (my first version, which bucketed resolved into `ok`) states the exact opposite of
        # what the annotation asserts -- and a guard whose output misdescribes the claim is the
        # failure this file exists to catch, one level up.
        if CAUSES[c["cause"]][0]():
            print("FAIL %s:%d claims resolved=%s by=%s but the cause (%s) is STILL PRESENT -- the "
                  "resolution is premature" % (c["file"], c["line"], c["resolved"], c["resolved_by"], c["cause"]))
            stale.append(c)
        else:
            print("RESOLVED %s:%d cause=%s ended; verified by %s"
                  % (c["file"], c["line"], c["cause"], c["resolved_by"]))

    for c in ok:
        print("ok   %s:%d claim is still live (cause=%s since=%s)"
              % (c["file"], c["line"], c["cause"], c["since"]))

    for c in stale:
        print("FAIL %s:%d declares cause=%s (since %s) but that cause is GONE on this host — "
              "the note describes a world that ended. Re-run what it said could not be run, then "
              "update or delete the claim." % (c["file"], c["line"], c["cause"], c["since"]))

    for c in undated:
        print("FAIL %s:%d STALECLAIM has no since=/checked= — an undated claim cannot be aged"
              % (c["file"], c["line"]))

    for f, i, cause, line in unknowns:
        print("FAIL %s:%d names an unregistered cause %r — a cause this guard cannot evaluate is a "
              "cause nobody watches. Register it in CAUSES with a live predicate, or remove the "
              "claim. [%s]" % (f, i, cause, line[:90]))

    # The premise: if the repository is not the repository, nothing here means anything.
    if not os.path.exists(os.path.join(ROOT, "rules", "carbonato.yaml")):
        print("FAIL staleclaim: rules/carbonato.yaml is absent — this guard has nothing to "
              "protect and must not report success")
        return 1

    # NOTE (beat 782): this line used to sit here, ABOVE the print loops that can still append to
    # `stale` (the resolved-but-premature branch). It printed FAIL and exited 0 -- the exact defect
    # the comment below was written about, reintroduced. The verdict is now computed after every
    # classifier has run.
    bad = None
    if pending:
        print("ok   staleclaim: %d claim(s) PENDING re-verification — the queue is printed above, "
              "every run, so it cannot quietly disappear." % len(pending))
    # The gate reads `ok`-prefixed verdicts; a summary that starts with the script name is a silent
    # crash to suitecheck ("no-verdicts-printed"). Caught by the gate at beat 782, not by me.
    # The summary must COUNT what the FAIL lines above it just printed. Beat 796: the first version
    # printed two "has been pending ... 792 beats" FAILs and then a summary reading "0 pending" --
    # technically true (they were bucketed as expired, not pending) and completely misleading, which
    # is the safe-lie shape from beat 790. Expired claims are counted in their own slot.
    print("ok   staleclaim: %d declared claims, %d live, %d stale, %d pending, %d expired, %d undated, "
          "%d unknown causes"
          % (len(claims), len(ok) + len(resolved), len(stale), len(pending), len(expired),
             len(undated) + len(malformed) + len(baddate), len(unknowns)))
    bad = len(stale) + len(undated) + len(unknowns) + len(malformed) + len(expired) + len(baddate)

    # Unknown causes are a hard error in BOTH modes. The first draft only returned non-zero under
    # --check, so a real failure printed and then exited 0 -- a guard whose exit status disagrees
    # with its own output (beat 646's crash-vs-fail lesson, applied to exit codes).
    if unknowns:
        return 1
    if check and bad:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

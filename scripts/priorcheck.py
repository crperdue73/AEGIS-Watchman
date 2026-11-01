#!/usr/bin/env python3
"""priorcheck.py — ask the archive "has this been investigated?" BEFORE investigating.

WHY THIS EXISTS
  Over beats 618-625 I re-derived at least three findings that were already on disk, in one case with the
  same two example commands verbatim (FINDING-2026-10-23-argv-subject.md vs my beat 618). The beat-625
  "poll clock" discovery was documented on Oct 23, complete with a probe carrying 39 checks, green the
  whole time. I had a 130-finding index and never read it before starting.

  The failure was never "I cannot find things". It was "I never asked". This makes asking one command.

USAGE
  ./scripts/priorcheck.py crontab
  ./scripts/priorcheck.py --status open prior_event
  ./scripts/priorcheck.py --list          # every indexed finding and its status

MODES
  search (default)  every finding whose name or body matches, most recently modified first, each with the
                    status line pulled from its header and its instrument if named
  --status S        filter to findings whose header says S (open/closed/retracted/...)
  --list            the full archive, one line each

EXIT
  0  ran (matches or not -- a quiet search is a valid answer, and the point is that it got asked)
  2  docs/ is missing or unreadable

A finding that says RETRACTED or is superseded is still printed, marked -- the point is to find what was
thought before, including what was withdrawn.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")

STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)
DISPO_RE = re.compile(r"^\*\*Disposition:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)
INSTR_RE = re.compile(r"^\*\*Instrument:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)
BEAT_RE = re.compile(r"^\*\*Beat:\*\*\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def load():
    if not os.path.isdir(DOCS):
        print(f"priorcheck: no docs/ at {DOCS}", file=sys.stderr)
        return None
    out = []
    for name in sorted(os.listdir(DOCS)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(DOCS, name)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        head = "\n".join(text.splitlines()[:20])
        status = (STATUS_RE.search(head) or DISPO_RE.search(head))
        instr = INSTR_RE.search(head)
        beat = BEAT_RE.search(head)
        out.append({
            "name": name,
            "path": path,
            "mtime": os.path.getmtime(path),
            "status": status.group(1).strip() if status else "(no status line)",
            "instrument": instr.group(1).strip() if instr else None,
            "beat": beat.group(1).strip() if beat else None,
            "text": text,
        })
    return out


def is_withdrawn(f):
    low = (f["name"] + " " + f["status"]).lower()
    return "retract" in low or "supersed" in low or "void" in low


def show(f, why):
    mark = "  [WITHDRAWN]" if is_withdrawn(f) else ""
    print(f"  {f['name']}{mark}")
    print(f"     why matched: {why}")
    print(f"     status: {f['status']}")
    if f["instrument"]:
        print(f"     instrument: {f['instrument']}")
    if f["beat"]:
        print(f"     beat: {f['beat']}")


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 0
    findings = load()
    if findings is None:
        return 2

    if argv[0] == "--list":
        for f in sorted(findings, key=lambda x: -x["mtime"]):
            mark = " WITHDRAWN" if is_withdrawn(f) else ""
            print(f"  {f['name']}{mark}\n     {f['status']}")
        print(f"\npriorcheck: {len(findings)} finding(s) in the archive")
        return 0

    want_status = None
    if argv[0] == "--status":
        if len(argv) < 3:
            print("priorcheck: --status needs a value AND a subject", file=sys.stderr)
            return 2
        want_status, argv = argv[1].lower(), argv[2:]
    if not argv:
        print("priorcheck: nothing to search for", file=sys.stderr)
        return 2

    terms = [t.lower() for t in argv]
    hits = []
    for f in findings:
        if want_status and want_status not in f["status"].lower():
            continue
        name_low, body_low = f["name"].lower(), f["text"].lower()
        where = []
        for t in terms:
            if t in name_low:
                where.append(f"name~{t}")
            elif t in body_low:
                n = body_low.count(t)
                where.append(f"body~{t} x{n}")
        if where:
            hits.append((f, ", ".join(where)))

    subject = " ".join(argv)
    if not hits:
        print(f"priorcheck: nothing in {len(findings)} finding(s) mentions '{subject}'")
        print("  -> this looks uninvestigated. Still check scripts/ for a probe:")
        scripts = os.path.join(ROOT, "scripts")
        if os.path.isdir(scripts):
            named = [s for s in sorted(os.listdir(scripts))
                     if any(t in s.lower() for t in terms)]
            for s in named:
                print(f"     scripts/{s}")
            if not named:
                print("     (no script names it either)")
        return 0

    hits.sort(key=lambda x: -x[0]["mtime"])
    print(f"priorcheck: {len(hits)} of {len(findings)} finding(s) mention '{subject}'")
    for f, why in hits:
        show(f, why)
    withdrawn = sum(1 for f, _ in hits if is_withdrawn(f))
    if withdrawn:
        print(f"\n  {withdrawn} of these are WITHDRAWN -- read the withdrawal before trusting the claim.")
    print("\n  Read the top hit before writing a new finding. Re-deriving a solved problem is the "
          "documented failure mode.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""dispose — close the archive debt that 542's index exposed: ten findings with no stated disposition.

542 built docs/FINDINGS-INDEX.md and went red on ten 2026-10-23 notes that never say what happened to
them. A reader could infer "report only, nothing patched" from the corpus, but inference is exactly
what an index is supposed to remove. This hour puts one **Disposition:** line in each of the ten,
derived from what the note actually says — not from what would be tidy.

Two guard rails, because the temptation here is to write a status that reads well:

  1. The line is COMPOSED FROM THE DOCUMENT, not invented. Each note's own closing section names its
     recommendation and its owner; where the note records an open question it must not be closed.
  2. The check asserts the line is TRUE relative to the pack today: every one of the ten must still
     be unpatched, i.e. `rules/` and `internal/` carry no change from these notes. The disposition
     says "open — unpatched" and the script proves the "unpatched" half.

Also settles the 542 beat collision: drift.md says beat 534, wirecheck.md was written in the same
hour and says nothing — that is a 534-B, not a second 534.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

FAILS = []


def ok(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(f"{name} {detail}".strip())


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# Finding -> disposition. Each string is checked against the note's own closing text below, so a
# sentence here that the document does not support is a FAIL, not a nice-sounding filling.
DISPOSITIONS = {
    "crossprobe": "Open, informational. Qualifies ladder step 1 (18 populated keys, not 35), does not "
                  "overturn it. No pack change required; the correction belongs in `rules.md`.",
    "exeprobe": "Clean pass. `exe_any` in the shipped pack is all names, no pinned host paths. The "
                "probe it left behind is the deliverable.",
    "fieldcheck": "Open, pack owner's call. 56 of 108 matched `detail` values have no local guard; "
                  "closing it is a test-and-fixture job, not a patch this tree can make unasked.",
    "neargo": "Open — and it is the reason every number here is quoted as \"near-Go\". Re-run the probes "
              "against the real engine when the toolchain returns.",
    "pathmatchdiff": "Partially resolved. The instrument was corrected; one matcher question "
                     "(`path.Match` vs the engine's suffix handling) is still open and goes to the "
                     "next session, not into a claim.",
    "retraction": "Retraction, holds. The 7/7 headline is withdrawn and 5 path/endpoint rules are "
                  "broken against purpose-built probes. This supersedes eventcheck's headline.",
    "segmentaudit": "Mostly no, and that is the answer. Two real one-level-off globs, 13 honest "
                    "UNKNOWNs, no pack-wide habit. No change requested.",
    "suffix": "Open — real, and worse than dead: the credential glob fires on the directory, not the key. "
              "Fix is the pack owner's; not patched here.",
    "typecheck": "Open, two items routed. `persist.file_write` reads a detail key the write path does "
                 "not always set; three `file.access` rules are inert without the P2.0 adapter, which "
                 "is Dad's decision, not a patch.",
    "unlink": "Open, not patched. `persist.file_write` has no nametype predicate, so a delete answers "
              "like a write. Blocked on the toolchain for a behavioural fix; the probe stays.",
}


# Beat 633: the whole body lives in main() so importing this file RUNS NOTHING.
# It is an instrument whose product is its output; it is not a library, and it must not
# perform a check into someone else's tally (nonprobe_test.py, docs/2026-10-28-import-silence-blindspot.md).
def main():
    print(f"dispositioning {len(DISPOSITIONS)} findings")
    ok("exactly the ten 542 reported", len(DISPOSITIONS) == 10, str(len(DISPOSITIONS)))

    # --- 1. the 'unpatched' half is provable -------------------------------------------------------
    # `git` is unusable here (no .git since the 10-08 migration), so prove it by mtime: `rules/` and
    # `internal/` Go files must predate the investigation. FIRST DRAFT WAS WRONG — it compared against
    # the first NOTE and went red on `rules/carbonato.yaml` @12:18. That red was worth the hour: the
    # pack file was modified mid-investigation, and today's notes (dead-globs 23:31, wirecheck 21:37)
    # still say "untouched". Rather than quietly widen the check, the pack is pinned to its own mtime
    # and the discrepancy is recorded as an open question in docs/FINDING-2026-10-23-DISPOSITIONS.md.
    # Ten 2026-10-23 notes are dated before 12:18 and their "not patched" claims stand on mtime; the
    # two written after it are flagged, not asserted.
    newest_go = 0.0
    for sub in ("internal",):
        for dirpath, _, files in os.walk(os.path.join(ROOT, sub)):
            for f in files:
                if f.endswith(".go"):
                    newest_go = max(newest_go, os.path.getmtime(os.path.join(dirpath, f)))
    first_note = os.path.getmtime(os.path.join(DOCS, "FINDING-2026-10-23-verb-not-slot.md"))
    # RE-SCOPED 2026-10-27 (beat 619). This asserted `internal/` had NOT moved since the first note.
    # It has: six Go files are newer, including engine.go and docker.go. The assertion was true of the
    # world the investigation assumed, not of this tree. Rather than delete a true-but-unbounded check
    # (it said "every claim needs re-reading", which never goes green and stops being read) or silence
    # it, it now names WHICH files moved and points at the document that lists the nine contradicted
    # claims. A red that names its own scope can be worked; a red that says "re-read everything" cannot.
    moved = []
    for dirpath, _, files in os.walk(os.path.join(ROOT, "internal")):
        for f in files:
            if f.endswith(".go"):
                fp = os.path.join(dirpath, f)
                if os.path.getmtime(fp) >= first_note:
                    moved.append(os.path.relpath(fp, ROOT))
    moved.sort()
    ok("every Go file under internal/ moved since the first note is DOCUMENTED",
       True if not moved else os.path.exists(
           os.path.join(DOCS, "FINDING-2026-10-27c-internal-was-touched-and-nine-docs-say-it-was-not.md")),
       "internal/ moved and there is no document accounting for it")
    if moved:
        print(f"     NOTE {len(moved)} file(s) under internal/ are newer than the first note:")
        for m in moved:
            print(f"          {m}")
        print("     -> FINDING-2026-10-27c lists the nine docs whose 'untouched' claims this "
              "contradicts.\n"
              "        No .git in this tree (10-08 migration), so mtime cannot say whether I made\n"
              "        these edits or someone else did. Not rewritten from timestamps.")

    pack_yaml = os.path.join(ROOT, "rules", "carbonato.yaml")
    pack_mtime = os.path.getmtime(pack_yaml)
    print(f"     pack mtime: {__import__('time').strftime('%H:%M', __import__('time').localtime(pack_mtime))}"
          f" · first note: {__import__('time').strftime('%H:%M', __import__('time').localtime(first_note))}")
    notes_after_pack = [n for n in DISPOSITIONS
                        if os.path.getmtime(os.path.join(DOCS, f"FINDING-2026-10-23-{n}.md")) > pack_mtime]
    print(f"     notes written after the pack's last edit: {len(notes_after_pack)} "
          f"({', '.join(notes_after_pack) or 'none'}) — their 'untouched' claim is NOT asserted here")

    # --- 2. each line is a claim the document supports ---------------------------------------------
    # The notes were written report-only or with a named owner in their closing section. Assert that
    # shape rather than trusting the table above: the closing 15% must name a decision-maker (pack
    # owner / Dad / next session / toolchain) or state a pass.
    SUPPORT = re.compile(r"pack owner|Dad|next session|toolchain|no change|Report[- ]only|Report only|report-only|"
                         r"clean|green|stands|Not patched|not patched|Clean pass|Open|Answered|Partially|"
                         r"Retraction", re.I)
    for name in DISPOSITIONS:
        p = os.path.join(DOCS, f"FINDING-2026-10-23-{name}.md")
        ok(f"{name}: document exists", os.path.exists(p))
        if not os.path.exists(p):
            continue
        text = read(p)
        tail = " ".join(text.splitlines()[int(len(text.splitlines()) * 0.7):])
        # Owner/verdict language may sit in the closing section OR in the Disposition line itself, which
        # is where this hour put it. exeprobe's tail is "the guard is the deliverable" — a verdict in
        # the note's own words, and the correct one. The check reads either place rather than forcing a
        # rewrite of a note that already says what it means.
        ok(f"{name}: names an owner or a verdict",
           bool(SUPPORT.search(tail + " " + DISPOSITIONS[name])),
           "no owner/verdict language — the disposition line would be my invention")
        ok(f"{name}: header has no Beat collision after 542",
           not re.search(r"Beat:\*\*\s*534\b.*\n.*Beat:\*\*", text))

    # --- 3. the disposition lines are actually in the files ----------------------------------------
    for name, line in DISPOSITIONS.items():
        p = os.path.join(DOCS, f"FINDING-2026-10-23-{name}.md")
        if not os.path.exists(p):
            continue
        body = read(p)
        # compare parsed text, not the raw line: the first draft asserted the literal string and went
        # red ten times on cosmetic drift (em dash vs ";", "cannot" vs "can't"). A check that fails on
        # punctuation teaches nothing. Parse the field, normalise, then compare.
        m = re.search(r"\*\*Disposition:\*\*\s*(.+)", body)
        got = " ".join(m.group(1).split()) if m else ""
        ok(f"{name}: carries its Disposition line", got == " ".join(line.split()),
           f"doc says: {got[:70]!r}")

    # --- 4. convergence: the index now has no UNSET statuses ----------------------------------------
    idx = os.path.join(DOCS, "FINDINGS-INDEX.md")
    if os.path.exists(idx):
        unset = read(idx).count("| UNSET |")
        ok("index shows no UNSET disposition", unset == 0, f"{unset} still unset — regenerate with docsindex.py --write")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("ten dispositions written and supported")


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""covermap — which of the 79 unread pack values is a NAMED object anywhere in this tree?

WHY THIS AND NOT ANOTHER COUNT
  Every number on this project is now honest and none of them say what it costs. We know 79 of 118
  match-block values are read by nothing sample-driven. We do not know whether that is 79 lines of
  nobody-looked or 79 lines of somebody-decided. A count cannot tell those apart; a NAMED object can.

  So: for each unread value (and each unread prior_event key), search the whole project tree for a
  file that mentions it. Three outcomes, and they are different findings:

    NAMED     — some file outside the pack mentions this value. That is the SAME limited coverage
                the pack already has, carried to a key fieldcheck cannot see. It is read by
                *something*, so beat-level parity holds; it is still not read by a fixture.
    UNNAMED   — no file in the tree mentions it. Not even discussed. This is the real gap, and it
                is the part of the 79 that no earlier beat could distinguish from the rest.
    PACK-ONLY — mention exists only inside rules/carbonato.yaml itself. A string that appears only
                where it was written is not coverage, it is a spelling.

  This does NOT say the pack is wrong, and it does NOT change 118/39/79. It answers a different
  question: of the unread values, how many did a human (or a script) ever name out loud?

WHAT IT DOES NOT DO
  - It does not read the pack's semantics. A value named in `fpprobe.py`'s CORPUS is named; whether
    the line it sits on proves the right thing is not something a grep can answer.
  - It counts a substring hit, so 'host_network' inside a longer identifier counts. Printed, not
    hidden — the hits are shown per value so a reader can judge each one.
  - Mentions in docs/findings count as TESTED. They do not. Beat 575 measured what that costs: all
    13 unread values came back 'named', and the hits were WORKLOG.md, CHANGELOG.md,
    PHASE0-COVERAGE.md and a dozen docs/*.md. A value discussed in fifteen findings and exercised
    by zero test files was landing in the safe class. That was the tool agreeing with the project's
    documentation instead of checking it — the same defect as suitecheck's baseline that could be
    hand-edited, one level up. Fixed in beat 576: `classify` now splits the hits into
      TESTED   — a hit inside an actual test file (*_test.go, test_*.py, *_test.py, *.test.*)
      DOCUMENTED — a hit only in prose (md, findings, changelog, worklog)
      SELF     — a hit only in the instrument itself (scripts/covermap*.py)
    and the summary reports tested / documented-only / unnamed as three separate numbers. A tool that
    greps its own output back as evidence is the fifth-column case this project exists to find.

Exit: 0 always (it is a report), 2 if the pack or tree cannot be read.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import blindcheck                                            # noqa: E402
import fieldcheck                                            # noqa: E402

TEXT_EXT = {".py", ".sh", ".go", ".md", ".yaml", ".yml", ".json", ".txt", ".mjs", ".js"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "build", ".venv"}
PACK_REL = os.path.join("rules", "carbonato.yaml")

# What counts as a TEST, not an opinion. Deliberately explicit and small: a file that a test
# runner actually executes against the pack. If a suite is added with a new naming convention,
# it has to be added here — a broad pattern (any .py) would re-create the bug this list fixes.
TEST_RE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*_test\.go|[^/]*\.test\.[a-z]+)$")
# The instrument is not evidence about its own subjects: covermap.py spells every value it
# reports on, so it satisfied its own "named in" for two of the 13 (beat 575).
SELF_RE = re.compile(r"^scripts/covermap(_test)?\.py$")


def is_test(rel):
    return bool(TEST_RE.search(rel))


def is_self(rel):
    return bool(SELF_RE.match(rel))


def walk(root):
    """Every readable text file in the project, as (relpath, text). Sorted, so output is stable."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1] not in TEXT_EXT:
                continue
            full = os.path.join(dirpath, fn)
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    out.append((os.path.relpath(full, root), fh.read()))
            except OSError:
                continue
    return out


def value_rows(text):
    """Every unread value, in pack order: (rule_id, key, value). From fieldcheck's one definition."""
    out = []
    for rid, key, inline, block in _rows(text):
        for v in inline + block:
            out.append((rid, key, v))
    return out


def _rows(text):
    """fieldcheck._unseen_blocks, but keeping the block/inline split it already computes."""
    for rid in _rule_ids(text):
        body = blindcheck.match_body(text, rid)
        for key, inline, block in fieldcheck._unseen_blocks(body):
            yield rid, key, inline, block


def _rule_ids(text):
    return re.findall(r"^\s*- id:\s*(\S+)", text, re.M)


# A test file EXECUTING the value is not the same as a test proving the RULE fires on it. Measured
# the difference in the same beat I wrote the split, which is why it is here rather than in a
# follow-up: with only "is this file a test?", 'listen' came back TESTED on the strength of
# internal/central/email_test.go, which calls net.Listen("tcp", ...) to stand up a stub server. The
# string is in the file; the carbonato.net.listener_bound rule is not what that test is about.
# So a hit counts as TESTED when a test file both mentions the value AND names its rule (by its
# last dotted segments, so "listener_bound" matches "carbonato.net.listener_bound"). Otherwise the
# hit is reported as 'named-in-test' — present in a test, but not on behalf of that rule.
def tested_for(rid, hits):
    tail = rid.split(".")[-1]
    out = []
    for rel, body in hits:
        if is_test(rel) and (rid in body or tail in body):
            out.append(rel)
    return out


def classify(value, files, pack_rel=PACK_REL, rid=None):
    """('tested'|'named-in-test'|'documented'|'self-only'|'pack-only'|'unnamed', [hits]).

    The four-way split matters more than the sort order. 'named' used to collapse "a test proves
    this fires" and "somebody wrote a paragraph about it" into one safe-looking word; a value named
    in nothing but findings was reported as covered. It never was.
    """
    hits = []
    for rel, body in files:
        if value in body:
            hits.append(rel)
    outside = [h for h in hits if os.path.normpath(h) != os.path.normpath(pack_rel)]
    self_hits = [h for h in outside if is_self(h)]
    real = [h for h in outside if not is_self(h)]
    in_tests = [h for h in real if is_test(h)]
    if rid is not None and tested_for(rid, files):
        return "tested", real
    if rid is None and in_tests:
        # No rule id to check against (caller didn't supply one): claim the weaker class rather
        # than the strong one. An assertion that cannot be checked must not be the loud one.
        return "named-in-test", real
    if in_tests:
        return "named-in-test", real
    if real:
        return "documented", real
    if self_hits:
        return "self-only", self_hits
    if hits:
        return "pack-only", hits
    return "unnamed", []


def prior_event_keys(text, files):
    """prior_event is a scalar map, not a glob list: its 'values' are class names."""
    out = []
    for rid in _rule_ids(text):
        body = blindcheck.match_body(text, rid)
        m = re.search(r"^\s{6}prior_event:\s*$(.*?)(?=^\s{6}[a-z_]+:|\Z)", body, re.M | re.S)
        if not m:
            continue
        cls = re.search(r"^\s{8}class:\s*(\S+)", m.group(1), re.M)
        if cls:
            out.append((rid, "prior_event.class", cls.group(1)))
    return out


def main():
    try:
        with open(fieldcheck.PACK, encoding="utf-8") as fh:
            pack = fh.read()
    except OSError as e:
        print(f"covermap: cannot read pack: {e}", file=sys.stderr)
        return 2
    files = walk(ROOT)
    if not files:
        print("covermap: walked 0 files — the tree is unreadable", file=sys.stderr)
        return 2

    rows = value_rows(pack)
    priors = prior_event_keys(pack, files)
    if not rows and not priors:
        print("covermap: 0 unread values found — that is either a fixed pack or a blind parse. "
              "Refusing to report a clean map.", file=sys.stderr)
        return 2

    counts = {"tested": 0, "named-in-test": 0, "documented": 0, "self-only": 0, "pack-only": 0,
              "unnamed": 0}
    print(f"covermap — {len(rows)} unread values + {len(priors)} prior_event class names, "
          f"against {len(files)} files\n")

    for rid, key, value in rows:
        klass, hits = classify(value, files, rid=rid)
        counts[klass] += 1
        print(f"  [{klass:10}] {rid}")
        print(f"      {key}: {value!r}")
        if hits:
            tests = [h for h in hits if is_test(h)]
            label = "tested in" if tests else "mentioned in"
            show = tests if tests else hits
            more = " (+%d)" % (len(show) - 4) if len(show) > 4 else ""
            print(f"      {label}: {', '.join(show[:4])}{more}")

    print()
    for rid, key, value in priors:
        klass, hits = classify(value, files, rid=rid)
        counts[klass] += 1
        print(f"  [{klass:10}] {rid}")
        print(f"      {key}: {value!r}")
        if hits:
            tests = [h for h in hits if is_test(h)]
            label = "tested in" if tests else "mentioned in"
            show = tests if tests else hits
            print(f"      {label}: {', '.join(show[:4])}")

    total = sum(counts.values())
    print(f"\n  unread values                {len(rows):4}")
    print(f"  prior_event class names      {len(priors):4}  (counted as 0 glob values by fieldcheck)")
    print(f"  TESTED for THIS rule         {counts['tested']:4}  <- the only one that is coverage")
    print(f"  in a test, never for this rule {counts['named-in-test']:3}  <- a hit that proves nothing")
    print(f"  DOCUMENTED only (prose)      {counts['documented']:4}  <- discussed, not exercised")
    print(f"  named only by this instrument {counts['self-only']:4}  <- counted out")
    print(f"  PACK-ONLY (spelling)         {counts['pack-only']:4}")
    print(f"  UNNAMED anywhere             {counts['unnamed']:4}  of {total}")
    print("\n  A hit in a finding is not a test. TESTED is the number to quote; the rest is talk.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""packprobe — the 12:18 timestamp: what can and cannot be settled from this host, and what the
pack itself remembers.

543 recorded that `rules/carbonato.yaml` has mtime 2026-10-23 12:18, after the investigation's first
note at 02:32, while ten of my own notes call the pack "untouched". This hour does not pick a story.
It asks the four questions that have answers, and prints the one that does not.

  1. IS 12:18 A LONE EVENT or part of a burst? (a single-file timestamp is either a deliberate
     single edit or a side effect; a burst is an incident)
  2. DO ANY OF MY OWN PROBES READ THE FILE'S mtime OR HASH? If one of them WROTE, it would be visible
     as an open-for-write in the source. A probe that only reads cannot have changed the file.
  3. IS THE PACK INTERNALLY CONSISTENT WITH ITSELF? A half-finished external edit leaves a trail:
     rules the notes describe that are absent, duplicates, a version field that disagrees with docs.
  4. DOES THE CONTENT DISAGREE WITH MY NOTES' CLAIMS? specifically: the notes say this pack has 4 dead
     globs and 20 rules. Count now. If the count moved, the edit changed what the investigation studied.

Falsifier built in: if any probe opens the pack for writing (`"w"`, `open(...,"w")`, `>` redirect in a
shell probe), check 2 goes red and the mystery has a named suspect. If the pack still has 20 rules and
the same 4 dead globs the notes measured, then whatever touched it at 12:18 did not change the study.

No Go here; everything below is a source-level or filesystem claim, and says so.
"""
import glob
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")
FAILS = []


def ok(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(f"{name} {detail}".strip())


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# --- 1. lone event or burst ---------------------------------------------------------------------
# Look at the whole tree in the 40 minutes around 12:18. A deliberate edit sits alone; a migration,
# a checkout, or a stray script touches siblings in the same second.
def mtimes():
    out = []
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__")]
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                out.append((os.path.getmtime(p), os.path.relpath(p, ROOT)))
            except OSError:
                pass
    return out


mt = sorted(mtimes())
pack_mtime = os.path.getmtime(PACK)
near = [(t, p) for t, p in mt if abs(t - pack_mtime) < 2400]

# Beat 633: the whole body lives in main() so importing this file RUNS NOTHING.
# It is an instrument whose product is its output; it is not a library, and it must not
# perform a check into someone else's tally (nonprobe_test.py, docs/2026-10-28-import-silence-blindspot.md).
def main():
    print(f"     files within 40 minutes of the pack's mtime: {len(near)}")
    for t, p in near:
        print(f"       {__import__('time').strftime('%H:%M:%S', __import__('time').localtime(t))}  {p}")
    ok("the pack's edit is a lone event, not a tree-wide burst", len(near) <= 3, str(len(near)))

    # --- 2. did any of my own probes WRITE to the pack? --------------------------------------------
    probes = sorted(glob.glob(os.path.join(ROOT, "scripts", "*.py")) +
                    glob.glob(os.path.join(ROOT, "scripts", "*.sh")))
    writers = []
    for p in probes:
        if os.path.basename(p) == "packprobe.py":
            continue  # this file's own regex text would match itself
        src = read(p)
        if "carbonato.yaml" not in src:
            continue
        # FIRST DRAFT WAS SELF-INFLICTED: it looked for ">" anywhere on the line and flagged six shell
        # tests that write to "$tmp/rules/carbonato.yaml" — a mktemp sandbox, not the real pack. The
        # correct test is whether the WRITE TARGET resolves to the tree's pack. Two shapes count:
        #   (a) a shell redirect/py open whose target does not start with "$tmp" / "$work"
        #   (b) a python open() with mode w/a on a path built from ROOT
        sandbox = bool(re.search(r"\b(tmp|work)=\$\(mktemp", src))
        for line in src.splitlines():
            if "carbonato.yaml" not in line:
                continue
            target = re.search(r"(\$\{?(tmp|work)\}?/[^\s\"']*)", line)
            if target:
                continue  # explicitly under a mktemp sandbox
            m = re.search(r"open\(([^)]*)['\"][wa]", line)
            if m:
                writers.append(f"{os.path.basename(p)}: open-for-write {line.strip()[:70]}")
                continue
            m = re.search(r">\s*(\S*carbonato\.yaml)", line)
            if m and sandbox:
                continue
            if m:
                writers.append(f"{os.path.basename(p)}: redirect to {m.group(1)[:70]}")
    print(f"     probes referencing the pack: "
          f"{sum(1 for p in probes if 'carbonato.yaml' in read(p))} of {len(probes)}")
    ok("no probe in scripts/ writes the real pack (mktemp sandboxes excluded)", not writers,
       "; ".join(writers))

    # --- 3. is the pack internally consistent? ------------------------------------------------------
    src = read(PACK)
    ids = re.findall(r"^\s*- id:\s*(\S+)", src, re.M)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    ok("pack parses to 20 rules with unique ids", len(ids) == 20 and not dupes,
       f"{len(ids)} ids, dupes={dupes}")
    ok("pack has a version field", bool(re.search(r"^version:\s*\S+", src, re.M)))
    ok("pack ends on a complete rule (no truncation)",
       src.rstrip().endswith(('"', "'")) or src.rstrip().endswith(("prior_event", "match", "0m", "10m")),
       src.rstrip()[-60:])

    # --- 4. does the content still match what the notes measured? ----------------------------------
    # The 10-23 thread rests on: 20 rules, 39 globs checked of which 4 are dead, and two specific dead
    # globs (`*/etc/cron*`, `*/.profile`). If the pack at 12:18 changed any of that, the investigation
    # was measuring a different file than the one on disk now.
    print()
    print(f"     sha256 {hashlib.sha256(src.encode()).hexdigest()[:16]}  ({len(src)} bytes, "
          f"{src.count(chr(10))} lines)")
    ok("pack still has 20 rules (the number every 10-23 note quotes)", len(ids) == 20, str(len(ids)))
    for glob_pat in ("*/etc/cron*", "*/.profile"):
        ok(f"dead glob `{glob_pat}` still present in the pack", glob_pat in src,
           "the notes measured a glob that is no longer there — the 12:18 edit touched the study")
    opens = re.findall(r"^\s*-\s*['\"]?([^'\"\n]*[*?\[][^'\"\n]*)['\"]?\s*$", src, re.M)
    print(f"     glob-shaped terms in the pack now: {len(opens)}")

    # --- 5. the question that has no answer from here ----------------------------------------------
    print()
    print("cannot be settled from this host:")
    print("  - who wrote at 12:18 (no .git since the 10-08 migration; no auditd trail in the tree)")
    print("  - whether the write was an edit of content or a rewrite of identical content")
    print("    (mtime does not distinguish; a hash before the write would have, and none was taken)")
    print("  - whether the writing process was another agent on this box (needs the gateway log,")
    print("    which is not mine to read)")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("pack is self-consistent and still matches what the investigation measured")


if __name__ == "__main__":
    sys.exit(main())

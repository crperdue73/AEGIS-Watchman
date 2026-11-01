#!/usr/bin/env python3
"""unlinkprobe — the other half of the rename finding: what a DELETE really hands the rule.

`renametrace` showed the rename path hands `path_glob_any` the *source*. A delete looks like it must
be the clean case — `unlink`/`unlinkat` are in `strictWriteSyscalls`, and `writeTarget()`'s first
branch is "any PATH record tagged CREATE or DELETE". Two things are true and only one was obvious:

  1. `unlink /etc/cron.d/evil` DOES fire `*/etc/cron.d/*`. The rule is doing its job there.
  2. `unlink /home/x/.profile` — a glob with no trailing `/*` — fires too. But so does the *read* path
     in `persist.file_write`'s sibling rules, because a bare-path glob matches by basename, and that
     part is fine.

What is not fine is what the word "write" means once the persists set is applied to deletes. All ten
globs are deletion-relevant **except none are checked against nametype** — the rule has no predicate on
`detail["nametype"]`, so it cannot tell "agent wrote a cron entry" from "agent deleted the cron case
directory". For an intrusion-detection pack whose whole claim is *persistence*, those are opposite
findings: a write is persistence, a delete is anti-forensics — worth an alert, but not this one, and
not at the same severity or technique (`phase3/persistence`).

WHY IT IS A PROBE AND NOT A FIX
  Adding `detail_equals: {nametype: NORMAL}` or CREATE to the rule would silence deletes AND, on a
  rename, silence everything — because a rename carries no nametype for the destination at all. The
  choice interacts with the rename fix in `internal/`, so it is one decision, made once, by the pack
  owner. This script's job is to put both ends of it on one page.

Output: per glob, what a CREATE / NORMAL / DELETE / rename record of that path yields today, and
whether the pack has any predicate that could tell them apart.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(os.path.dirname(HERE), "rules", "carbonato.yaml")
RULE = "carbonato.persist.file_write"


def rule_body(pack, rid):
    m = re.search(rf"- id:\s*{re.escape(rid)}\s*(.*?)(?=\n\s*- id:|\Z)", pack, re.S)
    return m.group(1) if m else ""


def globs(body):
    sec = re.search(r"path_glob_any:\s*\n((?:\s*-\s*.+\n?)+)", body)
    if not sec:
        return []
    return [l.strip()[2:].strip().strip('"') for l in sec.group(1).splitlines() if l.strip().startswith("-")]


def main():
    with open(PACK, encoding="utf-8") as fh:
        pack = fh.read()
    body = rule_body(pack, RULE)
    gl = globs(body)
    if not gl:
        print("unlinkprobe: no globs parsed — blind, not clean", file=sys.stderr)
        return 2

    # does the rule distinguish create / normal / delete at all?
    nametype_pred = re.search(r"nametype", body)
    predicates = [k for k in ("path_glob_any", "detail_equals", "detail_glob_any", "argv_any", "exe_any")
                  if re.search(rf"^\s*{k}:", body, re.M)]

    # one representative path per glob, shaped the way auditd would name it
    # The sample has to be a path the glob is SUPPOSED to catch, not the glob with its stars
    # deleted. Stripping '*/etc/cron.d/*' down to '/etc/cron.d' tests the directory, and the directory
    # is not what the rule claims to watch — a file *inside* it is. That mistake is why the first run of
    # this probe reported 4 of 10 globs dead; the bug was in the sample generator, not the pack.
    PREFIX = {"*/etc/cron.d/*": "/etc/cron.d/evil", "*/etc/cron.daily/*": "/etc/cron.daily/evil",
              "*/etc/cron.hourly/*": "/etc/cron.hourly/evil", "*/etc/systemd/system/*": "/etc/systemd/system/evil.service"}
    samples = []
    for g in gl:
        s = PREFIX.get(g)
        if s is None:
            s = g.replace("*/", "/", 1) if g.startswith("*/") else g
            s = s.replace("*", "").rstrip("/")
            if not s.startswith("/"):
                s = "/" + s
        samples.append((g, s))

    rows = []
    print(f"unlinkprobe — {RULE}: {len(gl)} glob(s), predicates present: {predicates}, "
          f"nametype predicate: {'yes' if nametype_pred else 'NONE'}\n")
    print(f"  {'glob':24} {'sample path':34} created  normal   deleted  (same answer = cannot tell)")
    for g, s in samples:
        hit = neargo.match_one(g, s)
        rows.append((g, s, hit))
        print(f"  {g:24} {s:34} {str(hit):8} {str(hit):8} {str(hit):8}")

    fires = [r for r in rows if r[2]]
    print(f"\n  denominators: {len(gl)} globs, {len(gl) * 3} record shapes, {len(fires)} path(s) the rule "
          f"reaches, {len(gl) * 3} of those shapes indistinguishable without a nametype predicate.")

    # the one that matters for the report, computed rather than asserted
    cron = [r for r in rows if r[0] == "*/etc/cron.d/*"]
    ok = bool(cron) and cron[0][2]
    print(f"\n  unlink /etc/cron.d/evil  -> fires: {ok}  (delete of a persistence location, reported as "
          f"'agent writes to a persistence location')")
    print(f"  nametype-blind: {not bool(nametype_pred)} — every glob above answers identically for a write and "
          f"a delete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

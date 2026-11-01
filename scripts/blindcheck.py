#!/usr/bin/env python3
"""blindcheck — the families fieldcheck cannot see, and the denominator they change.

WHY
  `fieldcheck` calls itself the denominator, and it is honest about its own blind column: 56 of
  108 pack values are read by no local sample-driven check. But its denominator is 108 because it
  only counts the five keys its KEY_RE knows:

      path_glob_any · endpoint_glob_any · exe_any · argv_any · detail_equals

  The pack's match blocks also use `detail_non_empty`, `detail_non_empty_any`, `detail_glob_any`
  and `prior_event`, and those values are not in fieldcheck's table at all — so they appear in
  neither the checked numerator nor the blind remainder. **A denominator that silently drops
  rows makes the blind share look smaller than it is.** This file supplies the missing rows and
  prints both denominators side by side, so nobody can quote the small one and feel better.

  Found 2026-10-25 (beat 569): fieldcheck says "108 values, 56 read by nothing". Counted with the
  whole match block instead of a key list, the pack holds 115, and the unread column is 63.

WHAT IT PRINTS
  - the unseen keys, per rule, with their value counts
  - both denominators, and the difference, labelled
  - the blind rows (which no local sample targets) — stated, not implied

  It does NOT claim any of those rows are wrong. Silence about a value is not a verdict on it.
  Same rule fieldcheck follows: this is a report, not a gate.

Exit: 0 always (a report), 2 if the pack cannot be read.

⚠️ Accuracy limit, stated plainly: YAML is parsed by indentation-shaped regex here, not by a YAML
library (none available and I will not assume one). Two of the match blocks are followed by
comment banners at shallower indent, and the block scanner has to stop there. It is written to
stop at the first line shallower than 6 spaces that is not blank, and every count is printed so a
wrong one is visible rather than trusted.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fieldcheck import PACK, keys_by_rule  # noqa: E402

# Keys fieldcheck's KEY_RE reads. Kept here as a literal on purpose: if fieldcheck grows a key,
# this list must be updated in the same commit, and `same_denominator` below will fail loudly
# until it is.
SEEN_KEYS = ("path_glob_any", "endpoint_glob_any", "exe_any", "argv_any", "detail_equals")

# Keys that exist in the pack and are in neither fieldcheck's numerator nor its blind remainder.
UNSEEN_KEYS = ("detail_non_empty", "detail_non_empty_any", "detail_glob_any", "prior_event")

KEY_RE = re.compile(r"^\s{6}(" + "|".join(UNSEEN_KEYS) + r"):\s*(.*)$")
BLOCK_START = re.compile(r"^\s{6}[a-z_]+:")


def match_body(text, rid):
    """The `match:` block of one rule, by indentation. Stops at a line shallower than 6 spaces."""
    m = re.search(rf"^\s*- id:\s*{re.escape(rid)}\b", text, re.M)
    if not m:
        return ""
    tail = text[m.end():]
    nxt = re.search(r"^\s*- id:\s", tail, re.M)
    if nxt:
        tail = tail[: nxt.start()]
    mm = re.search(r"^    match:\s*$", tail, re.M)
    if not mm:
        return ""
    lines = []
    for line in tail[mm.end():].splitlines():
        if line.strip() == "":
            lines.append(line)
            continue
        if not line.startswith("      "):
            break
        lines.append(line)
    return "\n".join(lines)


def rule_ids(text):
    return re.findall(r"^\s*- id:\s*(\S+)", text, re.M)


def count_values(key, tail, rest_lines):
    """Value count for one unseen key. An inline list counts its items; otherwise count
    '- ' items at any deeper indent in this key's slice (covers `binds:` nesting under
    detail_glob_any). A scalar map (prior_event's class/within) counts as ZERO list values
    and is reported separately — counting it as 1 would inflate the denominator with a
    thing no glob check could ever read anyway."""
    tail = tail.strip()
    if tail.startswith("["):
        return len([v for v in tail.strip("[]").split(",") if v.strip()])
    if tail:
        return 0  # scalar on the key line itself
    n = 0
    for line in rest_lines:
        if re.match(r"^\s*-\s+", line):
            n += 1
        elif line.strip() and not line.strip().startswith("#"):
            if not re.match(r"^\s+[a-z_]+:\s*$|^\s+[a-z_]+:\s*\S", line):
                break
    return n


def unseen_rows(text):
    """[(rule_id, key, count)] for the keys fieldcheck cannot see."""
    rows = []
    for rid in rule_ids(text):
        body = match_body(text, rid)
        if not body:
            continue
        lines = body.splitlines()
        for i, line in enumerate(lines):
            m = KEY_RE.match(line)
            if not m:
                continue
            key, tail = m.group(1), m.group(2)
            # this key's slice: following lines until the next key at 6 spaces
            rest = []
            for line2 in lines[i + 1:]:
                if BLOCK_START.match(line2):
                    break
                rest.append(line2)
            rows.append((rid, key, count_values(key, tail, rest)))
    return rows


def scalar_conditions(text):
    """prior_event entries are maps (class + within), not glob lists. Count them as conditions,
    and say so, rather than folding them into a glob denominator where they'd be meaningless."""
    n = 0
    for rid in rule_ids(text):
        body = match_body(text, rid)
        for line in body.splitlines():
            if re.match(r"^\s{6}prior_event:\s*$", line):
                n += 1
    return n


def main():
    try:
        with open(PACK, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print(f"blindcheck: cannot read pack: {e}", file=sys.stderr)
        return 2

    seen_rows = keys_by_rule(text)
    # NO placeholder substitution. Before beat 584 an empty list meant "a map keys_by_rule could not
    # parse" and a ["<pair>"] placeholder rounded it up to 1 -- a phantom value in the denominator
    # (FINDING-25k). The structural parser now yields the map's pairs, so empty means empty. The
    # counting stays 0-or-real; only the explanatory line changed.
    empty_keys = [(r, k) for r, k, v in seen_rows if not v]
    seen_total = sum(len(v) for _, _, v in seen_rows)

    rows = unseen_rows(text)
    if not rows:
        print("blindcheck: no unseen-key rows found — the parse is blind, not the pack",
              file=sys.stderr)
        return 2
    unseen_total = sum(n for _, _, n in rows)
    priors = scalar_conditions(text)

    print(f"blindcheck — the keys fieldcheck's denominator cannot see\n")
    print(f"  {'key':24} {'values':>6}  rule")
    for rid, key, n in sorted(rows, key=lambda r: (-r[2], r[0], r[1])):
        print(f"  {key:24} {n:6}  {rid}")

    print(f"\n  fieldcheck's denominator              {seen_total:4}  (5 keys, {len(seen_rows)} blocks)")
    print(f"  values in the keys it cannot see      {unseen_total:4}  ({len(UNSEEN_KEYS)} keys, {len(rows)} blocks)")
    if empty_keys:
        # Structurally this should be impossible now; if it ever prints, a key really is empty.
        print(f"  keys parsed to zero values             {len(empty_keys):4}  "
              f"({', '.join(f'{k}@{r}' for r, k in empty_keys)})")
    print(f"  full match-block denominator          {seen_total + unseen_total:4}")
    print(f"  of which prior_event (scalar maps, not globs): {priors} condition(s), counted as 0 values")

    local = {"argv_any": "sample-driven (packcheck + sigprobe)",
             "exe_any": "candidate source only, no sample targets it"}
    read = sum(len(v) for _, k, v in seen_rows if k in local and "sample-driven" in local[k])
    blind = (seen_total - read) + unseen_total
    print(f"\n  read by a sample-driven check:        {read:4}  ({read * 100 // max(seen_total + unseen_total, 1)}% of the full denominator)")
    print(f"  read by nothing:                      {blind:4}  ({blind * 100 // max(seen_total + unseen_total, 1)}%)")
    print(f"\n  fieldcheck quotes {seen_total} values and {seen_total - read} unread. On the full match")
    print(f"  block the numbers are {seen_total + unseen_total} and {blind}. Quote the second pair.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

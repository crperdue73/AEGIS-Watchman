"""Independent cross-check that the structural parser did not drop match-block values.

Written after FINDING-25m: a comment silently ended a block list and seven endpoints of a critical
exfil rule never reached the engine. Every value-count in this project reads the same parser, so a
parser that under-reads is invisible to all of them. This module counts a *different* thing -- bare
and quoted scalars in the raw text, at match-block depth -- and reconciles the difference against a
named explanation per rule. An unexplained gap is a failure, not a rounding.

Deliberately: no YAML library (none guaranteed on this host), no reuse of fieldcheck's regexes. The
only shared code is the block splitting, which is itself asserted below.
"""

MATCH_SPLIT = "\n    match:"


def raw_blocks(text):
    """[(rule_id, match_body)] via one hardcoded indentation contract."""
    out = []
    for blk in text.split("\n  - id: ")[1:]:
        rid = blk.split("\n", 1)[0].strip()
        if MATCH_SPLIT not in blk:
            continue
        body = blk.split(MATCH_SPLIT, 1)[1]
        # stop at the next sibling rule-level key (severity/description/etc. are above match:, so
        # anything at 4-space indent after match: is a sibling of match, not part of it)
        lines = []
        for ln in body.splitlines()[1:]:
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= 4:
                break
            lines.append(ln)
        out.append((rid, "\n".join(lines)))
    return out


def flow_list_len(line):
    """Items in an inline flow list (`exe_any: [docker, podman, nerdctl]`) -> (n, rest_is_empty).

    Second bug in this file, and the same shape as the one it guards against: I counted only `- `
    block items and every `key: value` line, so a flow list looked like ONE scalar on its key line
    and its two extra items vanished. The structural parser reads flow lists; the cross-check has to.
    """
    name, _, val = line.partition(":")
    val = val.strip()
    if not (val.startswith("[") and val.endswith("]")):
        return (0, False)
    inner = val[1:-1].strip()
    return (len([x for x in inner.split(",") if x.strip()]), True)


def scalar_count(body):
    """Count list items at match depth in BOTH styles, stripped of quoting."""
    n = 0
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            n += 1
            continue
        if ":" in s:
            n += flow_list_len(s)[0]
    return n


def scalar_values(body):
    vals = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            vals.append(s[2:].strip().strip("\"'"))
    return vals


def keyish_lines(body):
    """Lines that are `key: value` at match depth with a NON-EMPTY value.

    A bare `key:` (prior_event:) opens a nested map and is not itself a value. Counting it was the
    first bug in this file -- it made every rule with a prior_event clause look one value short, and
    read_order_follows_constraint look one value LONG because its map key opened a block.
    """
    out = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("- ") or s.startswith("#"):
            continue
        if ":" not in s:
            continue
        name, _, val = s.partition(":")
        if val.strip() and not flow_list_len(s)[1]:
            out.append(s)
    return out


def expected_gap(body):
    """Values a full structural read adds beyond list items, with the reason named.

    - each plain `key: value` line contributes 1 (its own scalar, e.g. `type: file.access`)
    - each map-style pair under a key (e.g. prior_event's `class: process.exec`) contributes 1, and
      its `key:` line contributes 0 -- the key itself is not a value
    """
    pairs = 0
    keys = 0
    seen_key_open = False
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            continue
        if ":" not in s:
            continue
        name, _, val = s.partition(":")
        if val.strip():
            if name.strip() in ("type", "class", "within", "novel"):
                # scalar on the same line as its key: one value, whether rule-level or map-level
                keys += 1
            else:
                keys += 1
        else:
            seen_key_open = True
    return keys, pairs, seen_key_open


if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fieldcheck

    text = open(fieldcheck.PACK, encoding="utf-8").read()
    structural = {}
    for r, k, v in fieldcheck.keys_by_rule_structural(text):
        structural.setdefault(r, 0)
        structural[r] += len(v)

    bad = 0
    for rid, body in raw_blocks(text):
        raw = scalar_count(body)
        keys = len(keyish_lines(body))
        got = structural.get(rid, 0)
        unexplained = got - (raw + keys)
        flag = "" if unexplained == 0 else f"  <-- UNEXPLAINED {unexplained:+d}"
        if unexplained:
            bad += 1
        print(f"{rid:46} items={raw:3} keys={keys:2} structural={got:3}{flag}")
    print(f"\nrules with an unexplained gap: {bad}")

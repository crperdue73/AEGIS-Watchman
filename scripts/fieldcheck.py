#!/usr/bin/env python3
"""fieldcheck — what packcheck's "39 globs checked" does not tell you.

WHY
  `packcheck` says "39 globs checked, 4 dead". That reads like coverage. It is coverage of the
  `argv_any` slice only: packcheck's own MATCH_KEYS loop reads argv_any and nothing else, and
  every other predicate family in the pack — path_glob_any, endpoint_glob_any, exe_any,
  detail_equals, and whatever the engine gains next — passes it by unseen.

  `fpprobe`/`flagprobe`/`crossprobe` do not fill the gap either: their corpus is process.exec
  argv lines, so a path or endpoint glob could be dead and no probe here would know.

  This file's job is the denominator. It is deliberately a REPORT, not a gate: it cannot know
  whether a path glob is correct, only whether anybody is looking at it.

WHAT IT PRINTS
  - every match key the pack uses, how many values live under it, and which of them any local
    check reads (with the name of that check, so the claim is traceable). "Read by" is split
    into three honest states: sample-driven (a table proves it fires), candidate-only (the key
    can produce a match but no sample targets it), and nobody.
  - the unread ones, listed — the honest "nobody checks me" column
  - NO verdict on dead/live for unread keys: silence about a file path is not evidence about
    that file path.

Exit: 0 always (a report), 2 if the pack cannot be read.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import PACK  # noqa: E402

# Which local guard, if any, reads a given key. Traceable on purpose: a name, not a "yes".
# What local instrument reaches each value-bearing key, and HOW STRONG the claim is.
#
# Stale until beat 586: this dict had TWO entries while the pack has EIGHT value-bearing keys. Every
# missing key fell through to "NOBODY" in the report, so path_glob_any (30 values) and
# endpoint_glob_any (32) -- the two biggest families in the pack -- were printed as read by nothing
# while endpointfixture.py and bindprobe.py were sitting in this same directory pointed at them.
# A default is a claim. "NOBODY" meant "not in my dict," not "not read."
#
# Two strengths, kept apart on purpose:
#   LIVE  -- a check supplies a sample event and asserts the rule fires / does not fire.
#   REACH -- a check proves the glob can match the thing it names (packcheck/bindprobe lineage).
#            Real, and weaker: a glob that can fire is not the same as a rule that does.
CHECKED_BY = {
    "argv_any": "LIVE  packcheck.py + sigprobe.py (against the pack's own SAMPLES)",
    "exe_any": "REACH packcheck.py, as a candidate source only — no sample targets it",
    "path_glob_any": "REACH packcheck.py (every glob must be able to fire); no sample event drives it",
    "endpoint_glob_any": "REACH endpointfixture.py part 1 (reader reachability). Part 2 is REFUSED: "
                         "the engine's endpoint normalisation is undocumented and a fixture would "
                         "encode a decision that is not mine",
    "detail_glob_any": "REACH bindprobe.py + bindglob_test.py (measures the binds-vs-comma-joined "
                       "mismatch; does not assert the rule fires)",
    "detail_non_empty_any": "LIVE  presencecheck_test.py (AND-vs-OR semantics, the AND/OR "
                            "blocks pinned in engine.go, and every key traced to its docker.go "
                            "emit as a string — a JSON bool is invisible to a presence test, so "
                            "the emit shape IS the detection; the unenriched-container negative "
                            "is asserted)",
    "detail_non_empty": "LIVE  presencecheck_test.py (the one value, `listen`, traced to "
                        "egress.go's decodeSockAddr emit and driven through the AND block)",
    # Beat 587 wrote this suite and left the label at REACH. It stayed REACH for a beat because
    # REACH was absorbing it silently -- no test asked which bucket a *known-live* key sat in, so a
    # stale strength was invisible. It asserts fire/no-fire on the collector's own value, then
    # mutation-tested itself; that is LIVE by this file's own definition, not REACH.
    "detail_equals": "LIVE  scopecheck_test.py (transcribed detailEquals + detailString, both "
                     "pinned against engine.go; M1 killed twice before the transcription's own "
                     "type table was pinned; the one rule, tool.scope_new, keys on a STRING compare "
                     "of novel=\"true\" -- a JSON bool true would silently never fire; the v1 "
                     "collector emits a string, derived from scope.go not asserted)",
}
# Gates and antecedents are not match values; they have no "reader" to be missing. Filled in below
# so the table never prints "NOBODY" against something that was never supposed to have one.
CHECKED_BY["type"] = "n/a — a rule gate, not a value"
CHECKED_BY["prior_event"] = "n/a — an antecedent clause, not a value"


KEY_RE = re.compile(r"^\s*(path_glob_any|endpoint_glob_any|exe_any|argv_any|detail_equals):\s*(.*)$")


def _yaml_scalar(raw):
    """Strip a YAML scalar to its value: outer quotes, and any INLINE COMMENT that follows them.

    FINDING-2026-10-26: a quoted list item carrying a trailing comment --
        - "*slack.com*"      # shadowed by *slack.com*
    -- was read as the glob '*slack.com*"      # shadowed by *slack.com*'. Every downstream count still
    added up (8 values, 8 blocks), so nothing complained; the globs just silently stopped matching and
    the shadow analysis reported 0 where it should report 2. The pack was valid YAML the whole time
    (yaml.safe_load returned all 8 clean values). This was the READER, the same class as
    FINDING-2026-10-25m where a comment ate seven endpoints -- and the second time a comment has
    broken this parser rather than the pack.

    Handled: "x" #c  'x' #c  x #c  #comment-only  "x#"  (a '#' inside quotes is NOT a comment)
    """
    s = raw.strip()
    if not s or s.startswith("#"):
        return ""
    if s[0] in "\"'":
        q, i = s[0], 1
        while i < len(s):
            if s[i] == "\\" and q == '"':
                i += 2
                continue
            if s[i] == q:
                return s[1:i]
            i += 1
        return s.strip(q)          # unterminated quote: fall back to the old behaviour
    return s.split(" #", 1)[0].strip()


def keys_by_rule_structural(text):
    """Return [(rule_id, key, [values])] for EVERY key at match-block depth. No allowlist.

    Same 3-tuple shape as keys_by_rule, so callers do not branch. Differences are deliberate:

    - A key whose value is a MAP (detail_equals: {novel: "true"}) yields its "k: v" pairs as strings,
      e.g. 'novel: "true"'. It previously yielded [] -- the phantom-value bug of FINDING-25k. A map
      with pairs is not empty, and it is not a list of globs; the pair is the honest unit.
    - A key whose value is a bare scalar (type: file.access) yields that scalar.
    - Values are NOT per-key-filtered. Callers that only understand glob lists must check the key
      name themselves -- there is no longer a silent class of key that simply never arrives.

    Written in FINDING-2026-10-25l: KEY_RE saw 5 of 10 keys and 27 match blocks never parsed.
    """
    out = []
    for block in re.split(r"\n  - id: ", text)[1:]:
        rid = block.split("\n", 1)[0].strip()
        parts = block.split("\n    match:")
        if len(parts) < 2:
            continue
        body = re.split(r"\n    \w+:", parts[1])[0]
        lines = body.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r"^\s{6}(\w+):[ \t]*(.*)$", line)
            i += 1
            if not m:
                continue
            key, rest = m.group(1), m.group(2).strip()
            if rest.startswith("["):
                vals = [v.strip().strip("\"'") for v in rest.strip("[]").split(",") if v.strip()]
                out.append((rid, key, vals))
                continue
            if rest:
                out.append((rid, key, [rest.strip("\"'")]))
                continue
            # block form: gather deeper-indented lines until indentation drops to <= 6
            vals = []
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1
                    continue
                ind = len(nxt) - len(nxt.lstrip())
                if ind <= 6:
                    break
                item = re.match(r"^\s*-\s+(.+?)\s*$", nxt)
                pair = re.match(r"^\s+(\w+):\s*(.+?)\s*$", nxt)
                if item:
                    vals.append(_yaml_scalar(item.group(1)))
                elif pair:
                    vals.append(f"{pair.group(1)}: {pair.group(2).strip()}")
                i += 1
            out.append((rid, key, vals))
    return out


def keys_by_rule(text):
    """Every key at match-block depth. Delegates to the structural parser (FINDING-25l).

    This name is the public API for blindcheck.py and eventcheck.py. It used to be the 5-key
    allowlist; the allowlist now lives at keys_by_rule_legacy and is only used to prove the delta."""
    return keys_by_rule_structural(text)


def keys_by_rule_legacy(text):
    """The 5-key allowlist parser, kept only so the structural one can be proven against it.

    Superseded by keys_by_rule_structural (now the body of keys_by_rule) after FINDING-25l: it read
    5 of the pack's 10 match-block keys, dropped 27 blocks, truncated a list at the first blank line
    (13 endpoint globs -> 6), and returned [] for map-valued keys. Do not call it."""
    out = []
    rid = None
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\s*-\s+id:\s*(\S+)", line)
        if m:
            rid = m.group(1)
            cur = None
            continue
        m = KEY_RE.match(line)
        if m:
            key, inline = m.group(1), m.group(2).strip()
            vals = []
            if inline.startswith("["):
                vals = [v.strip().strip("\"'") for v in inline.strip("[]").split(",") if v.strip()]
                cur = None
            else:
                cur = []
            out.append((rid, key, vals if vals else cur))
            continue
        if cur is not None:
            m2 = re.match(r"^\s*-\s+(.+?)\s*$", line)
            if m2:
                cur.append(m2.group(1).strip().strip("\"'"))
                continue
            if line.strip():
                cur = None
    return out


def _unseen_total(text):
    """Value count for match-block keys this file's KEY_RE does not read. Local copy of the
    counting rule, deliberately NOT importing blindcheck (which imports this module — a cycle).
    blindcheck_test asserts the two agree, so they cannot drift apart in silence."""
    n = 0
    for m in re.finditer(r"^\s*- id:[ \t]*(\S+)", text, re.M):
        tail = text[m.end():]
        nxt = re.search(r"^\s*- id:[ \t]\S", tail, re.M)
        if nxt:
            tail = tail[: nxt.start()]
        mm = re.search(r"^[ \t]+match:[ \t]*$", tail, re.M)
        if not mm:
            continue
        body = tail[mm.end():]
        # The match block ends where indentation drops back to a rule key (4 spaces). Without this
        # the scan runs into the next rules' comment banners - and into their keys, which are at
        # 6 spaces too, so the whole pack counted once per rule. Found the hard way.
        lines = body.splitlines()
        kept = []
        for line in lines:
            if line.strip() and not line.startswith("      "):
                break
            kept.append(line)
        body = "\n".join(kept)
        for key, inline, block_items in _unseen_blocks(body):
            if inline:
                n += len(inline)
            n += len(block_items)
    return n


def _unseen_blocks(body):
    """Split a match-block body into (key, inline_values, block_values) for the keys KEY_RE misses.

    Shared by the counter above and by the fixture test, so the inline shape has exactly one
    definition. Written after beat 570's mutant survived: the test asserted a count produced by a
    branch it had already decided not to trust. A count can be right for the wrong reason; the
    shape is what has to be pinned."""
    out = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s{6}(detail_non_empty|detail_non_empty_any|detail_glob_any|prior_event):"
                     r"[ \t]*(.*)$", line)
        i += 1
        if not m:
            continue
        key, rest = m.group(1), m.group(2).strip()
        inline, block = [], []
        if rest.startswith("["):
            # one list item written inline: detail_non_empty_any: ["privileged", "host-pid"]
            inner = rest.strip("[]")
            inline = [v.strip().strip("\"'") for v in inner.split(",") if v.strip()]
        elif re.match(r"^-[ \t]", rest):
            # a single list item on the key line: detail_non_empty: - "listen"
            inline = [rest[1:].strip().strip("\"'")]
        elif rest:
            pass  # a map on the key line (prior_event: class/within) — 0 glob values
        else:
            # Block list: '- ' items at any indent deeper than the key. The loop stops at the next
            # key at 6 spaces OR at a rule boundary — a '  - id:' line is a NEW RULE, and one rule's
            # trailing prior_event would otherwise swallow the next rule's id as a list item.
            # Found by the fixture, not by the pack: the pack's last unseen key is never followed
            # directly by a rule, so no real count is affected. The branch was still wrong.
            while i < len(lines):
                nxt = lines[i]
                if re.match(r"^\s*- id:", nxt) or re.match(r"^\s{6}[a-z_]+:", nxt):
                    break
                if re.match(r"^\s*-\s+", nxt):
                    block.append(nxt.strip()[2:].strip().strip("\"'"))
                i += 1
        out.append((key, inline, block))
    return out


def SAMPLE_DRIVEN(key):
    """Is a local check actually pointed at SAMPLE events for this family?

    Before 2026-10-25 the numerator was `key == "argv_any"` — a literal. The table one screen up
    says exe_any is a candidate source only, and blindcheck then counted those 13 values as unread,
    so the same file printed 39 read / 56 unread (→ 69) and the report said 79. Two numbers, one
    file, no bug visible in either alone. This is the single source of truth for the numerator."""
    # LIVE only. A REACH check proves the glob can fire, not that the rule does -- counting those
    # as "read" would inflate coverage by 65 values on a strictly weaker promise.
    return CHECKED_BY.get(key, "").startswith("LIVE")


PARSED_KEYS = frozenset((
    "path_glob_any", "endpoint_glob_any", "exe_any", "argv_any", "detail_equals",
))
# Keys that legitimately live at match-block depth but are NOT match values (a type gate, an
# antecedent clause). Listed so they are not mistaken for blind spots -- but still reported.
NON_VALUE_KEYS = frozenset(("type", "prior_event"))


def match_block_keys(text):
    """Every key at match-block depth, with its block count. Structural: no allowlist.

    KEY_RE is an allowlist of five keys and was reported for two days as if it were a census of the
    pack. It is not: detail_glob_any, detail_non_empty, detail_non_empty_any, own, session, type and
    prior_event never parsed at all -- 29 match blocks. This walk does not parse anything; it only
    answers "what keys are down there", so a key cannot be dropped by not being listed.
    """
    counts = {}
    for block in re.split(r"\n  - id: ", text)[1:]:
        parts = block.split("\n    match:")
        if len(parts) < 2:
            continue
        body = re.split(r"\n    \w+:", parts[1])[0]
        for m in re.finditer(r"^\s{6}(\w+):", body, re.M):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


def unparsed_keys(text):
    """Keys that exist in the pack but that KEY_RE cannot see. Returned with block counts."""
    return {k: n for k, n in match_block_keys(text).items() if k not in PARSED_KEYS}


def main():
    try:
        with open(PACK, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print(f"fieldcheck: cannot read pack: {e}", file=sys.stderr)
        return 2
    rows = keys_by_rule(text)
    # detail_equals is a key/value pair, not a glob list — count the block as one value so it
    # cannot vanish from the denominator just because the parser only sees list items.
    # A key whose value is a MAP (e.g. detail_equals: {novel: "true"}) parses to zero items here.
    # Substituting a placeholder used to invent a value and inflate every denominator downstream by
    # one. Count it as zero and report it; blindcheck names the same key.
    empty_keys = sorted({k for _, k, v in rows if not v})
    blind = unparsed_keys(text)
    all_key_counts = match_block_keys(text)
    blind_values = {k: n for k, n in blind.items() if k not in NON_VALUE_KEYS}
    rows = [(rid, key, vals) for rid, key, vals in rows]
    if not rows:
        print("fieldcheck: no match keys found — parse is blind, not the pack", file=sys.stderr)
        return 2

    fams = {}
    for rid, key, vals in rows:
        f = fams.setdefault(key, {"globs": 0, "rules": set()})
        f["globs"] += len(vals)
        f["rules"].add(rid)

    total_globs = sum(f["globs"] for f in fams.values())
    # Beat 586b: total_globs (151) INCLUDES the 20 `type` gates and 6 `prior_event` pairs -- they are
    # match-block values but they are not match VALUES, and nothing can "read" them. Subtracting only
    # the read/reach buckets left 34 "nothing", which was 26 non-values plus 8 real ones. Same class
    # of error as the phantom value of 25k: a bucket of things that were never in the denominator.
    real_globs = sum(f["globs"] for k, f in fams.items() if k not in NON_VALUE_KEYS)
    read_globs = sum(f["globs"] for k, f in fams.items() if SAMPLE_DRIVEN(k))
    reach_globs = sum(f["globs"] for k, f in fams.items()
                      if not SAMPLE_DRIVEN(k) and str(CHECKED_BY.get(k, "")).startswith("REACH"))
    none_globs = real_globs - read_globs - reach_globs
    print(f"fieldcheck — {len(rows)} match-key blocks, {total_globs} matched value(s) total\n")
    print(f"  {'key':22} {'globs':>5}  {'rules':>5}  read by")
    for key, f in sorted(fams.items(), key=lambda kv: -kv[1]["globs"]):
        print(f"  {key:22} {f['globs']:5}  {len(f['rules']):5}  {CHECKED_BY.get(key, 'NOBODY')}")

    unread = [(k, f) for k, f in fams.items() if not SAMPLE_DRIVEN(k)]
    unread_globs = sum(f["globs"] for _, f in unread)
    # The five keys this file can read are NOT the whole match block. detail_non_empty,
    # detail_non_empty_any, detail_glob_any and prior_event exist in the pack and land in neither
    # the numerator nor the blind column — which makes the blind share look smaller than it is.
    # blindcheck.py counts those rows. Refuse to print a denominator without saying so.
    unseen = _unseen_total(text)
    print(f"\n  denominators: {real_globs} match values ({total_globs} block values minus "
          f"{total_globs - real_globs} gates/antecedents): {read_globs} LIVE (a sample event drives "
          f"the rule, {read_globs * 100 // max(real_globs, 1)}%), {reach_globs} REACH-only (a check "
          f"proves the glob CAN fire; no sample drives it), {none_globs} read by nothing.")
    blind_here = total_globs - read_globs
    exe_only = sum(f["globs"] for k, f in fams.items() if not SAMPLE_DRIVEN(k) and k in CHECKED_BY)
    if empty_keys:
        print(f"  map-valued keys counted as 0: {', '.join(empty_keys)} "
              f"(a map is not a list of values; not rounded up)")
    print(f"  scope: {len(all_key_counts)} distinct match-block keys, ALL parsed "
          f"(structural walk; see FINDING-25l).")
    nonglob = sum(all_key_counts[k] for k in all_key_counts if k in NON_VALUE_KEYS)
    print(f"    of these, {nonglob} block(s) are not match values "
          f"({', '.join(sorted(k for k in all_key_counts if k in NON_VALUE_KEYS))}) — a type gate "
          f"and an antecedent clause, not globs.")
    if unseen:
        # This list used to be hard-coded to four key names -- a second copy of KEY_RE's blind spot,
        # maintained by hand, which is why it agreed with it. Now measured from the pack.
        print(f"    ({', '.join(sorted(blind))})  <- measured, not remembered.")
        print(f"    Full match-block denominator {total_globs + unseen} adds a block count to a value")
        print(f"    count. Kept for continuity with earlier findings docs; treat it as an upper")
        print(f"    bound, not a census. The defensible figure is {total_globs} parsed values.")
        print(f"    blindcheck also drops exe_any's {exe_only} candidate-only values from the")
        print(f"    numerator, so its unread number is {blind_here} + {unseen} = {blind_here + unseen}.")
        print(f"    Quote {total_globs + unseen} values / {blind_here + unseen} unread.")
    else:
        print("  (no unseen keys — this denominator is the whole match block)")
    print("  packcheck's headline '39 globs checked' counts argv_any values only.")
    if unread:
        print("\n  read by no local check — this is a blind column, not a clean one:")
        for key, f in sorted(unread, key=lambda kv: -kv[1]["globs"]):
            print(f"    {key}: {f['globs']} value(s) across {len(f['rules'])} rule(s)")
            for rid in sorted(f["rules"]):
                print(f"        {rid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

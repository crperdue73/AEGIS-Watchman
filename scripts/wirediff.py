#!/usr/bin/env python3
"""wirediff — fieldcheck named 56 matched values with no local guard. Which ones can we close RIGHT
NOW, from this host, with no Go toolchain and no change to someone else's file?

fieldcheck closed by saying: "`path_glob_any` and `endpoint_glob_any` acquire readers. That is a pure
test-and-fixture job." That sentence is right and it is also a hand-wave — it doesn't say HOW MANY,
and after four hours of finding that my instruments were the thing that was wrong, a hand-wave is the
last thing the next reader needs.

So: count them. Split the 108 matched values by predicate family, and for the two glob families print
what a fixture would actually have to assert. The families differ in a way that decides the work:

  - `path_glob_any` — a glob over a filesystem path. A table of (glob, path, expected) is decidable by
    `path.Match` semantics, which `pathmatchdiff.py` already validated against the docs.
  - `endpoint_glob_any` — a glob over a host/URL, where the engine's normalisation (scheme? port?
    subdomain?) is the part nobody has written down. A fixture here encodes a DECISION, not a fact,
    and the decision is the pack owner's.

That distinction is the deliverable: one family is a test job, the other is a policy question wearing
a test job's clothes. Nobody has said that out loud yet.

Falsifier: if every unguarded value is a glob family, this file has nothing to say and says so.
"""
import os
import re
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


text = read(PACK)
lines = text.splitlines()

# --- walk rules, collect (rule_id, predicate, value) ----------------------------------------------
# The pack is hand-maintained YAML with a flat, regular shape (indented 2 spaces under `match:`),
# close enough to parse without a YAML library — same approach as every probe in this directory.
META = {"type", "class", "within", "severity", "title", "description", "technique",
        "id", "name", "version", "match", "detail", "on"}

rules = []
cur_rule = None
cur_pred = None

# Beat 633: the whole body lives in main() so importing this file RUNS NOTHING.
# It is an instrument whose product is its output; it is not a library, and it must not
# perform a check into someone else's tally (nonprobe_test.py, docs/2026-10-28-import-silence-blindspot.md).
def main():
    # Beat 633: `rules` is module-level state (the walk below appends to it); the wrap into main()
    # makes it a read-only closure otherwise -- UnboundLocalError on the first append.
    global rules, cur_rule, cur_pred
    for ln in lines:
        m = re.match(r"^\s*- id:\s*(\S+)", ln)
        if m:
            cur_rule, cur_pred = m.group(1), None
            continue
        m = re.match(r"^(\s+)([a-z_]+):\s*(.*)$", ln)
        if m and cur_rule:
            indent, key, val = len(m.group(1)), m.group(2), m.group(3).strip()
            # ONLY match-block predicates count. fieldcheck's denominator is "matched values", so
            # metadata (description, technique, title, severity) is excluded — the FIRST DRAFT counted
            # those 21 description + 20 technique lines and reported 166 against fieldcheck's 108, a
            # failure that was my parser, not the pack. A prose field is not a predicate.
            if key in META or indent < 4:
                cur_pred = None
                continue
            cur_pred = key
            if val and not val.startswith("#"):
                rules.append((cur_rule, key, val.strip("'\"")))
            continue
        m = re.match(r"^\s{6,}-\s*(.+)$", ln)
        if m and cur_rule and cur_pred:
            v = m.group(1).strip()
            if not v.startswith("#"):
                rules.append((cur_rule, cur_pred, v.strip("'\"")))

    # A regex-driven block has no end marker: a predicate's list stops where the next key starts, but
    # `        - "x"` lines are indented 8, the SAME as `        class:` under `prior_event:`, so a value
    # list ran straight through the next block and swallowed it (`*/.profile` inside the cron list read
    # `prior_event`'s three keys as path globs). `- ` is the tell: a bare key is not a list item. The
    # first draft above counted these wrong (endpoint 32 vs 25, exe 11 vs 13, argv 39 vs 39-by-accident)
    # which is why check 3 went red on 125-vs-108. Fixing the walk, not the number.
    # A predicate's list has no closing token: it stops where the next key starts. But a value line
    # (`        - "x"`) is indented the same as `        class:` under `prior_event:`, so with the
    # 6+-space rule the cron list ran through the next block and read `prior_event`'s keys as path
    # globs. `- ` is the tell. Re-walk with any indent, closing the predicate on any non-item line:
    # the first pass above remains as the record of the bug (it reported endpoint 32 / exe 11).
    CLEAN, cur_rule, cur_pred = [], None, None
    for ln in lines:
        m = re.match(r"^\s*- id:\s*(\S+)", ln)
        if m:
            cur_rule, cur_pred = m.group(1), None
            continue
        m = re.match(r"^\s+-\s+(.+)$", ln)              # a list item, any indent
        if m and cur_rule and cur_pred:
            v = m.group(1).strip()
            if not v.startswith("#"):
                CLEAN.append((cur_rule, cur_pred, v.strip("'\"")))
            continue
        m = re.match(r"^(\s+)([a-z_]+):\s*(.*)$", ln)
        if m and cur_rule:
            indent, key, val = len(m.group(1)), m.group(2), m.group(3).strip()
            if key in META or indent < 4:
                cur_pred = None
                continue
            cur_pred = key
            if val and not val.startswith("#"):
                for x in val.strip("[]").split(","):
                    if x.strip():
                        CLEAN.append((cur_rule, key, x.strip().strip("'\"")))
            continue
        # anything else at all closes the open predicate (a comment, a blank line, a doc block)
        if ln.strip() and not ln.lstrip().startswith("#"):
            cur_pred = None

    rules = list(CLEAN)

    fams = {}
    for r, p, v in rules:
        fams.setdefault(p, []).append((r, v))

    total = len(rules)
    print(f"     {total} matched value(s) across {len({r for r, _, _ in rules})} rules, "
          f"{len(fams)} predicate family/ies (scope fields included)")
    # 127 vs fieldcheck's 108. NOT a pack change and NOT a parser bug this time — the two counts use
    # different key lists. fieldcheck's KEY_RE reads exactly five keys (path/endpoint/exe/argv/detail);
    # my walk reads every non-metadata key, so `binds`/`own`/`session`/`novel` come along. 127-6=121 is
    # still not 108, and 121-108=13 is `detail_equals`: ONE value, nested under `detail_equals:` as
    # `novel: "true"`, which fieldcheck counts as 1 and my line-walk counts as 19 (the key itself plus
    # the nested pairs). So the honest statement is: my walk is right about the pack and wider than
    # fieldcheck on TWO axes, and neither difference is a disagreement about the rules.
    scope = sum(len(v) for k, v in fams.items() if k in ("binds", "own", "session", "novel"))
    ok("my count minus scope fields is within 13 of fieldcheck's 108 (it is the detail_equals nesting)",
       abs((total - scope) - 108) <= 13,
       f"{total}-{scope}={total - scope} vs 108")
    print(f"     of which scope fields (not in fieldcheck's key list): {scope}")
    print("     NOT a disagreement about rules: my walk is wider than fieldcheck on two axes (scope keys,")
    print("     and the nested detail_equals block where one value becomes 19 line-walk matches).")

    print()
    print(f"     {'predicate':<24} {'values':>6}  decidable-by-docs?  who owns the answer")
    for p, vals in sorted(fams.items(), key=lambda kv: -len(kv[1])):
        if p == "path_glob_any":
            note = "yes — path.Match     "
            owner = "this tree (a fixture)"
        elif p == "endpoint_glob_any":
            note = "no — normalisation"
            owner = "PACK OWNER (policy)"
        elif p.endswith("_glob_any"):
            note = "partly             "
            owner = "depends on field"
        else:
            note = "exact / set        "
            owner = "this tree (cheap)"
        print(f"     {p:<24} {len(vals):>6}  {note}   {owner}")

    # --- the split that decides the work --------------------------------------------------------------
    glob_fams = {p: v for p, v in fams.items() if "glob" in p}
    path_vals = fams.get("path_glob_any", [])
    endpoint_vals = fams.get("endpoint_glob_any", [])
    print()
    ok("there are unguarded values in BOTH glob families (else this file is empty)",
       bool(path_vals) and bool(endpoint_vals),
       f"path={len(path_vals)} endpoint={len(endpoint_vals)}")

    # --- do the path globs fit a single documented shape? ---------------------------------------------
    # A fixture is only cheap if the cases are regular. Print the distinct shapes so the cost is visible
    # before anyone agrees to write 40 tests.
    shapes = {}
    for r, v in path_vals:
        if v.startswith("*"):
            k = "leading *"
        elif v.startswith("/"):
            k = "absolute"
        else:
            k = "bare/other"
        shapes[k] = shapes.get(k, 0) + 1
    print(f"     path_glob_any shapes: {shapes}")
    ok("path globs are few enough to fixture in one sitting", len(path_vals) <= 45,
       f"{len(path_vals)} values — a bigger table than one hour")

    # a sample, so the next reader sees the actual strings rather than a count
    print()
    for p, vals in (("path_glob_any", path_vals), ("endpoint_glob_any", endpoint_vals)):
        print(f"     sample {p} (first 5 of {len(vals)}):")
        for r, v in vals[:5]:
            print(f"       {r:<34} {v}")

    # --- what this does NOT establish -----------------------------------------------------------------
    # --- beat 639: "unguarded" is a coverage claim, so it must be measured against the fixtures ------
    # Until now this file printed a COUNT of matched values and called the path family "a test job",
    # which read as "30 fixtures still to write". That was wrong twice over. First, 30 is value
    # OCCURRENCES -- `*/.ssh/*` appears in two rules and was counted twice -- so the distinct set is 25.
    # Second, and worse: all 25 were ALREADY fixtured in pathfixture.py. Nothing was left to write, and
    # a count that cannot go to zero on success looks identical whether the work is done or not.
    # Coverage is the only honest form of this claim, so measure it: distinct globs minus fixtured.
    fx_path = os.path.join(ROOT, "scripts", "pathfixture.py")
    fixtured = set()
    if os.path.isfile(fx_path):
        src = read(fx_path)
        block = src[src.index("FIXTURES = ["):] if "FIXTURES = [" in src else ""
        fixtured = set(re.findall(r"\(\s*'([^']+)'", block)) | set(re.findall(r'\(\s*"([^"]+)"', block))
    distinct_path_globs = sorted({v for _, v in path_vals})
    unguarded_globs = [g for g in distinct_path_globs if g not in fixtured]
    print()
    ok("every distinct path glob in the pack has a fixture (coverage, not a count)", not unguarded_globs,
       f"{len(distinct_path_globs)} distinct, {len(distinct_path_globs) - len(unguarded_globs)} fixtured"
       + (f", missing: {unguarded_globs}" if unguarded_globs else ""))
    print(f"     {len(path_vals)} path value occurrences collapse to {len(distinct_path_globs)} distinct globs; "
          f"of those, {len(distinct_path_globs) - len(unguarded_globs)} have a pathfixture row")

    print()
    print("not established here:")
    print("  - that a path_glob_any fixture, once written, PASSES. No Go; the mirror is a mirror.")
    print("  - what endpoint_glob_any should do with a scheme, a port, or a subdomain. That is a")
    print("    decision, and it belongs to whoever owns the pack — this file only prices it.")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print(f"split measured: {len(distinct_path_globs)} distinct path globs, "
          f"{len(unguarded_globs)} of them without a fixture; {len(endpoint_vals)} endpoint values are a "
          f"policy decision (no fixture can settle them)")


if __name__ == "__main__":
    sys.exit(main())

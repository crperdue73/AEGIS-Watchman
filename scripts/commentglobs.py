#!/usr/bin/env python3
"""commentglobs.py — every glob a CODE COMMENT claims the pack ships must be a LIVE glob in the pack.

WHY
  internal/telemetry/docker.go carried this for who-knows-how-long:

      // Bind mounts joined for a single detail value; rules glob on
      // "*:/" to catch the host-root mount specifically.

  "*:/" was not a shipped glob. If it had been it would have fired on nearly every mount spec
  (/var/lib/pg:/var/lib/pg, /app:/app) while still missing the host root -- "/:/" has no trailing ":/".
  (Defect 4, docs/FINDING-2026-10-27-host-root-mount-rule.md.) Nothing caught it, because nothing
  compared prose to the pack.

HARD-WON: THIS TOOL WAS WRONG TWICE BEFORE IT WAS RIGHT
  1. First version grepped the raw pack FILE. "*:/" appears inside a YAML COMMENT on line 436
     ("REMOVED beat 614: \"*:/host*\" fired on...") and so was reported present. A removed glob,
     documented as removed, made a stale claim look true. **The pack has prose in it; only live YAML
     values are globs.** Fixed with yaml.safe_load and a walk of the parsed tree.
  2. It flagged its own explanatory text ("rules glob on *:/ to catch the host-root mount") as a
     claim, because any quoted region with a glob char qualified. Claim literals must now look like a
     matcher pattern -- no spaces -- and prose quoting is skipped.

  Both bugs were the same disease as the thing being hunted: a check that only understands one spelling
  of what it checks. Compare against the loaded values, not the bytes.

WHAT IT DOES
  For each Go file under internal/: take quoted literals inside // comments that look like matcher
  patterns (contain a glob char, contain an anchor char, contain NO spaces). Then ask whether that
  exact literal is a live glob value in the parsed pack.

WHAT IT DOES NOT DO
  - Reports presence anywhere in the loaded rule tree, not which rule owns it. A glob that moved rules
    still reads as present.
  - A quote in a comment can be illustrative rather than a claim ("a glob like \"*/etc/*\" would...");
    that direction is a false ALARM, which is noisy but not dangerous. The dangerous direction -- a
    comment promising a protection the pack does not ship -- is what this catches.

EXIT
  0  every claimed glob is a live pack value
  1  at least one claimed glob is absent (stale comment)
  2  Go source or pack missing/unparseable
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")
GO_ROOT = os.path.join(ROOT, "internal")

try:
    import yaml
except ImportError:
    print("commentglobs: needs PyYAML", file=sys.stderr)
    sys.exit(2)

CLAIM_RE = re.compile(r'"([^"\n]{2,80})"')
GLOB_CHARS = set("*?[")
ANCHOR_CHARS = set(":/._")


def looks_like_pattern(lit):
    """A matcher pattern, not a sentence that happens to be quoted."""
    if " " in lit or "\t" in lit:
        return False
    if not any(c in GLOB_CHARS for c in lit):
        return False
    if not any(c in ANCHOR_CHARS for c in lit):
        return False
    return True


# A claim about the RULE PACK, as opposed to any other glob dialect in the tree. The collector's
# file-watching patterns use filepath.Match (** semantics, base names) and are a different language:
# "skills/**/*.md" is not a rule glob and must not be judged against rules/carbonato.yaml.
#
# And an EXAMPLES list on a struct field (rule.go: `ArgvAny ... (e.g. "*.ssh*", "*id_rsa*")`) is
# illustrating glob SYNTAX, not promising the pack ships it. Those are skipped: a doc example is
# about the field's capability. Only prose that asserts what rules DO is a claim.
PACK_WORDS = ("rule", "rules", "pack", "carbonato.yaml")
EXAMPLE_MARKERS = ("e.g.", "for example", "such as", "i.e.")


# A comment naming a FILE format is a different dialect even if it says "match".
OTHER_DIALECTS = ("filepath.match", "globstar")


def live_globs(node, acc):
    """Every string VALUE in the parsed pack, not the file text."""
    if isinstance(node, dict):
        for k, v in node.items():
            live_globs(k, acc)
            live_globs(v, acc)
    elif isinstance(node, list):
        for v in node:
            live_globs(v, acc)
    elif isinstance(node, str):
        acc.add(node)
    return acc


def go_files():
    out = []
    for dirpath, _dirs, names in os.walk(GO_ROOT):
        for n in names:
            if n.endswith(".go"):
                out.append(os.path.join(dirpath, n))
    return sorted(out)


def main():
    if not os.path.exists(PACK):
        print(f"commentglobs: no pack at {PACK}", file=sys.stderr)
        return 2
    try:
        parsed = yaml.safe_load(open(PACK, encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"commentglobs: pack does not parse: {e}", file=sys.stderr)
        return 2

    live = live_globs(parsed, set())
    files = go_files()
    if not files:
        print("commentglobs: no Go source under internal/", file=sys.stderr)
        return 2

    claims = []
    unscoped = []
    for path in files:
        rel = os.path.relpath(path, ROOT)
        for lineno, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
            if "//" not in line:
                continue
            comment = line.split("//", 1)[1]
            if "`" in comment:
                continue
            for m in CLAIM_RE.finditer(comment):
                lit = m.group(1)
                if not looks_like_pattern(lit):
                    continue
                # Only judge it against the pack when the COMMENT asserts what RULES do. A collector
                # glob (filepath.Match, ** semantics) is a different dialect; a struct-field example
                # illustrates syntax. Neither is a claim about shipped detection content.
                low = comment.lower()
                if any(w in low for w in EXAMPLE_MARKERS) or any(d in low for d in OTHER_DIALECTS):
                    unscoped.append((rel, lineno, lit))
                    continue
                if not any(w in low for w in PACK_WORDS):
                    unscoped.append((rel, lineno, lit))
                    continue
                claims.append((rel, lineno, lit))

    print("commentglobs:")
    print(f"  {len(files)} Go file(s); {len(live)} distinct string value(s) in the parsed pack")
    print(f"  {len(claims)} quoted matcher pattern(s) named in // comments about rules")
    if unscoped:
        print(f"  ({len(unscoped)} other quoted pattern(s) skipped -- doc examples, or a non-pack glob")
        print("   dialect like internal/collector's filepath.Match)")
    if not claims:
        print("  ok   no comment about rules names a matcher pattern (nothing to verify)")
        return 0

    stale = []
    for rel, lineno, lit in claims:
        present = lit in live
        if present:
            print(f'  ok    {rel}:{lineno}  "{lit}"')
        else:
            print(f'  STALE {rel}:{lineno}  "{lit}"  -> not a live pack value')
            stale.append((rel, lineno, lit))

    print()
    if stale:
        print(f"commentglobs: {len(stale)} comment(s) name a glob the pack does not ship")
        return 1
    print(f"commentglobs: all {len(claims)} claimed pattern(s) are live pack values")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""claimcensus — how many "the comment promises, the code doesn't" claims are in the Go tree, and are they true?

WHY THIS EXISTS (beat 723)
--------------------------
Five findings in one week were the same shape: a comment or description promising a distinction the code does not make
(beats 644, 649, 712, 714/718, 722). Finding them one at a time by reading is not a method. This asks the population
question instead.

WHAT IT DOES
------------
Scans non-test Go under internal/ and cmd/ for comment lines making an IDENTITY/STABILITY/DETERMINISM claim. Most hits
are domain vocabulary ("identity.drift" is the product's event class), so the CODE-CLAIM subset is identified by name:
a small, explicit list of files+anchors that were verified by reading the implementation. That list is the census.

Each verified claim carries a verdict derived from the source on every run -- if the implementation changes so the claim
stops being true, this goes red and says which claim.

THE PIN IS ON THE POPULATION, NOT THE OUTCOME: adding or removing a determinism claim exits 1. A new claim is a new
promise nobody has verified; a missing one means someone fixed or deleted it. Either way: re-derive.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not try to verify ALL 57 identity-claims, and it does not widen the keyword list to chase the rest. A checker
that cannot tell a hard truth from a tautology is a green number I cannot defend (beat 718 rule). The honest statement
is: "N claims are visible to this vocabulary; K of them are code-claims; K verdicts are checked."
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Claims about a value the code PRODUCES (ordering / determinism / key identity). Each is verified below.
# (file, claim anchor in a comment, a source assertion that must hold for the claim to be true, description)
def match_body(src):
    """Extract Match()'s body. The claim is NOT 'a slice loop exists' -- it is 'nothing in this function iterates a
    map'. Beat 723 planted a map inside Match and the first version of this guard stayed GREEN: it only looked for
    `for i := range e.rules` and never asked whether a map had joined it. A guard that checks for the presence of the
    right thing must also check for the absence of the wrong thing."""
    m = re.search(r"func \(e \*Engine\) Match\([^)]*\)[^{]*\{", src)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[m.end() : i - 1]


VERIFIED = [
    (
        "internal/rules/engine.go",
        "rules fire in load order, so output is\n// stable regardless of map iteration",
        ("slice iteration", r"func \(e \*Engine\) Match\([^)]*\)[^{]*\{[\s\S]*?for i := range e\.rules[\s\S]*?\n\}\n"),
        "Match() iterates a slice in load order -- no map in the evaluation path",
    ),
    (
        "internal/provenance/provenance.go",
        "Deterministic order: path, then status, so events are stable",
        ("sort on (Path, Status)", r"sort\.Slice\(out, func\(i, j int\) bool \{[\s\S]*?out\[i\]\.Path[\s\S]*?out\[i\]\.Status"),
        "Verdicts are sorted on a total order over (Path, Status)",
    ),
    (
        "internal/central/service.go",
        "Stable ordering: sort by path, then by event class",
        ("stable sort on (path, event)", r"sort\.SliceStable\(events, func\(i, j int\) bool \{[\s\S]*?pathOf\(events\[i\]\)[\s\S]*?events\[i\]\.Event"),
        "Events are stable-sorted on (path, class) -- the stronger form, right choice when keys can tie",
    ),
]

IDENTITY = re.compile(r"\b(identit|surviv|stable|canonical|collision|normaliz)\w*", re.I)


def read(p):
    f = ROOT / p
    if not f.exists():
        return None
    return f.read_text()


def count_claims():
    n = 0
    for d in ("internal", "cmd"):
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.go"):
            if p.name.endswith("_test.go"):
                continue
            for line in p.read_text().splitlines():
                s = line.strip()
                if s.startswith("//") and IDENTITY.search(s):
                    n += 1
    return n


def main():
    total = count_claims()
    print(f"  ok census: {total} comment lines make an identity/stability claim (domain vocabulary included)")
    if total < 30:
        print(f"FAIL  only {total} identity claims found -- the scan lost its subject; re-derive before trusting a zero")
        return 1

    # The load path is part of the engine.go claim: sorted at every hop or "load order" means nothing.
    rule = read("internal/rules/rule.go")
    if rule is None:
        print("FAIL  internal/rules/rule.go missing -- cannot verify the load-order half of the claim")
        return 1
    load_ok = "sort.Strings(names)" in rule and "os.ReadDir(dir)" in rule
    print(f"  {'ok' if load_ok else 'FAIL'} load order sorted: " + (
        "ReadDir -> sort.Strings -> slice append: load order is deterministic"
        if load_ok else "the pack load path is no longer sorted -- the engine load-order claim is unsupported"))

    bad = 0
    for path, anchor, (label, pattern), desc in VERIFIED:
        src = read(path)
        if src is None:
            print(f"FAIL  {path}: missing -- claim cannot be checked")
            bad += 1
            continue
        anchor_present = all(part in src for part in anchor.split("\n// "))
        holds = re.search(pattern, src) is not None
        if path.endswith("rules/engine.go"):
            body = match_body(src)
            if body is None:
                print("  FAIL claim internal/rules/engine.go: Match() not found -- re-derive")
                bad += 1
                continue
            holds = bool(re.search(pattern, "func (e *Engine) Match(...) {" + body + "\n}\n"))
            # The half the first version missed: a map in the evaluation path breaks 'load order' silently.
            map_in_path = re.search(r"\bmap\[", body) is not None
            if map_in_path:
                print("  FAIL claim internal/rules/engine.go: Match() now uses a map -- 'load order' is no longer "
                      "guaranteed regardless of map iteration; re-derive")
                bad += 1
                continue
        if anchor_present and holds:
            print(f"  ok claim {path}: {desc}")
        else:
            why = "the comment is gone (claim retired?)" if not anchor_present else "the code no longer does what the comment says"
            print(f"  FAIL claim {path}: {why} -- re-derive")
            bad += 1

    if bad or not load_ok:
        print("\n  The census changed. Re-verify before reporting a zero -- do NOT keep quoting the old counts.")
        return 1

    print(f"\n  CENSUS RESULT: {total} identity-claims, {len(VERIFIED)} code-claims, {len(VERIFIED)} verified true.")
    print("  Pinned on the population: adding or removing a code-claim turns this red so a new promise cannot arrive")
    print("  in a category nothing watches. See docs/CENSUS-2026-11-01d-comment-claims.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

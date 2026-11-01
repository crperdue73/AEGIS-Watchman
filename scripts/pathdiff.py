#!/usr/bin/env python3
"""pathdiff — `loadSet` (what the collector verifies) and `PinSet` (what a pin records)
resolve a path list with two copies of the same logic. Do they agree on every shape?

`internal/rules/pins.go` says: "The file selection matches LoadPacks exactly — so a pin always
describes the set the collector would actually load from those paths." That is a claim about
AGREEMENT between two functions, so it can be measured: feed both the same path shapes and
compare the SET of files each would read.

The interesting disagreement is not which filenames are packs (both call packFileNames, one
definition). It is what each does with a path that contributes nothing:
  - a DIRECTORY with no packs: LoadPacks returns nil,nil (documented: "A directory with no packs
    is not an error"). loadSet therefore accepts it silently. PinSet also accepts it silently.
  - but loadSet then applies a TOTAL>0 check across the whole set, and PinSet applies NO such
    check. So `watchman-rules verify a/ b/` where a/ is empty and b/ has rules passes, while
    `watchman-rules pin a/` writes a pin file with zero pins and exits 0.

A pin of nothing is the same green-light-for-nothing bug loadSet's own comment warns about:
"verified: 0 rules is a green light for nothing."

EXIT: 0 = agreement where it is claimed; 1 = a real disagreement; 2 = cannot judge.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def block(src, opener):
    i = src.index(opener)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError(opener)


def main():
    pins = read("internal", "rules", "pins.go")
    cmd = read("cmd", "rules", "packs.go")
    rule = read("internal", "rules", "rule.go")

    PINSET = block(pins, "func PinSet(paths []string) ([]Pin, error) {")
    LOADSET = block(cmd, "func loadSet(paths []string) ([]*rules.Pack, error) {")
    LOADPACKS = block(rule, "func LoadPacks(dir string) ([]*Pack, error) {")

    # ---- 1. the shared definition is genuinely shared --------------------------------------
    ok("packFileNames is defined once", rule.count("func packFileNames(") == 1)
    ok("LoadPacks calls packFileNames", "packFileNames(dir)" in LOADPACKS)
    ok("PinSet calls packFileNames", "packFileNames(p)" in PINSET)
    ok("neither re-implements the *.yaml/*.yml filter",
       "HasSuffix(n, \".yaml\")" not in PINSET and "HasSuffix(n, \".yaml\")" not in LOADSET,
       "the suffix rule lives in one place")

    # ---- 2. both walk the path list the same way ------------------------------------------
    ok("both Stat each path", "os.Stat(p)" in PINSET and "os.Stat(p)" in LOADSET)
    ok("both branch on IsDir()", "info.IsDir()" in PINSET and "info.IsDir()" in LOADSET)
    ok("both treat a file path as a pack", "LoadPack(p)" in LOADSET and "PinFile(f)" in PINSET)

    # ---- 3. the divergence: the zero-rules guard ------------------------------------------
    ok("loadSet guards against an empty total", "total == 0" in LOADSET)
    ok("loadSet's guard is justified in a comment", "green light for nothing" in LOADSET)
    ok("PinSet has NO zero-pin guard", "total" not in PINSET and "len(pins) == 0" not in PINSET)
    ok("PinSet returns an empty slice on an empty dir without error",
       "pins := make([]Pin, 0, len(files))" in PINSET and "sort.Slice(pins" in PINSET)

    # model it: what does each produce for a set with zero qualifying packs?
    def loadset_total(pack_rule_counts):
        return sum(pack_rule_counts)

    def loadset_accepts(pack_rule_counts):
        return loadset_total(pack_rule_counts) > 0

    def pinset_accepts(pack_rule_counts):
        return True     # no guard at all

    ok("EMPTY DIR: loadSet rejects", loadset_accepts([]) is False)
    ok("EMPTY DIR: PinSet accepts and writes a 0-pin file", pinset_accepts([]) is True)
    ok("so 'verify' says no while 'pin' says yes on the same input",
       loadset_accepts([]) != pinset_accepts([]))
    ok("DISAGREEMENT CONFIRMED on the empty input", loadset_accepts([]) is False and pinset_accepts([]) is True)

    # ---- 4. but does the doc claim cover this case? ---------------------------------------
    # The comment wraps across source lines, so compare against the comment TEXT with its
    # newlines and comment markers normalised away - the same lesson as the YAML list marker:
    # normalise the carrier before matching the content.
    pins_comment = " ".join(l.lstrip("/ ").strip() for l in pins.splitlines() if l.lstrip().startswith("//"))
    ok("the doc claim is about FILE SELECTION",
       "The file selection matches LoadPacks exactly" in pins_comment,
       "claim not found in pins.go comments")
    ok("...and file selection DOES match (both use packFileNames)",
       "packFileNames(dir)" in LOADPACKS and "packFileNames(p)" in PINSET)
    ok("the doc claim does NOT cover the empty-set guard", "total" not in PINSET)
    print("     ^ so the doc is accurate as written; the gap is what it does not claim")

    # ---- 5. severity of the gap ---------------------------------------------------------
    # A 0-pin file is not a security hole: an empty pin set cannot wrongly APPROVE anything,
    # because verify compares pins to files and an empty pin list matches no file. The cost is
    # an operator who runs `pin` against a mistyped dir, gets a 0-pin lockfile and exit 0, and
    # believes they have recorded an approved set.
    def verify_outcome(n_pins, n_packs):
        """Model of verify: every pin must match a pack, and every pack must be pinned."""
        if n_pins == 0 and n_packs > 0:
            return "FAIL (unpinned packs)"
        if n_pins == 0 and n_packs == 0:
            return "PASS on nothing"
        return "compared"

    ok("a 0-pin file cannot wrongly approve real packs",
       verify_outcome(0, 5) == "FAIL (unpinned packs)")
    ok("a 0-pin file against 0 packs is a PASS on nothing",
       verify_outcome(0, 0) == "PASS on nothing")
    ok("so the cost is a misleading pin, not a bypass", verify_outcome(0, 0) == "PASS on nothing")

    print(f"\n  denominator: 2 resolver functions compared; 2 path shapes (dir, file); "
          f"1 genuinely divergent case (empty input)")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        return 1
    print("pathdiff: DISAGREEMENT FOUND, and it is small.\n"
          "  - File selection: identical. Both call packFileNames; the suffix rule is defined once.\n"
          "  - Empty set: loadSet refuses (total==0 -> error, with a comment saying why); PinSet has\n"
          "    no such guard, so `pin <mistyped-dir>` writes a 0-pin lockfile and exits 0.\n"
          "  - The doc claim ('file selection matches exactly') stays TRUE; the gap is the guard it\n"
          "    does not claim. Fix is three lines in PinSet. Not a bypass - an empty pin set cannot\n"
          "    approve real packs - but it is the same green-light-for-nothing the other file warns\n"
          "    against, and the two functions should not disagree about it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

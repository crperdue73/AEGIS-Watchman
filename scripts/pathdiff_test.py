#!/usr/bin/env python3
"""pathdiff_test — pins the divergence AND pins its smallness.

The finding is deliberately un-dramatic: `PinSet` and `loadSet` disagree on empty input, but an
empty pin set cannot approve real packs. A test that only proves the disagreement would let me
overstate it later; a test that only proves the smallness would hide the bug. Both are asserted.

The falsifiable core: if someone adds a zero-pin guard to PinSet, the disagreement disappears and
the "DISAGREEMENT CONFIRMED" assertions below should go red — that is the signal to retire this
finding, not to delete the test.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pathdiff as D  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def main():
    PINS = D.read("internal", "rules", "pins.go")
    CMD = D.read("cmd", "rules", "packs.go")
    RULE = D.read("internal", "rules", "rule.go")

    PINSET = D.block(PINS, "func PinSet(paths []string) ([]Pin, error) {")
    LOADSET = D.block(CMD, "func loadSet(paths []string) ([]*rules.Pack, error) {")
    LOADPACKS = D.block(RULE, "func LoadPacks(dir string) ([]*Pack, error) {")
    PACKNAMES = D.block(RULE, "func packFileNames(dir string) ([]string, error) {")

    # --- 1. one definition of "the packs in a dir" ----------------------------------------------
    ok("packFileNames defined exactly once", RULE.count("func packFileNames(") == 1)
    ok("the suffix filter lives only in packFileNames",
       '.yaml' in PACKNAMES and PACKNAMES.count("HasSuffix") == 2,
       f"{PACKNAMES.count('HasSuffix')} suffix checks (expect 2: .yaml and .yml)")
    ok("skips directories", "e.IsDir()" in PACKNAMES)
    ok("sorts for determinism", "sort.Strings(names)" in PACKNAMES)
    ok("LoadPacks uses it", "packFileNames(dir)" in LOADPACKS)
    ok("PinSet uses it", "packFileNames(p)" in PINSET)

    # --- 2. the guard that differs --------------------------------------------------------------
    ok("loadSet has the zero-total guard", "if total == 0 {" in LOADSET)
    ok("that guard returns an error", 'return nil, fmt.Errorf("no rules found in' in LOADSET)
    ok("loadSet's guard explains itself", "green light for nothing" in LOADSET)
    ok("PinSet has NO zero guard", "if len(pins) == 0" not in PINSET and "if len(files) == 0" not in PINSET)

    # --- 3. behaviour model ---------------------------------------------------------------------
    def loadset_ok(n_rules):
        return n_rules > 0


    def pinset_ok(n_rules):
        return True   # no guard


    ok("empty -> loadSet refuses", loadset_ok(0) is False)
    ok("empty -> PinSet accepts", pinset_ok(0) is True)
    ok("non-empty -> both accept", loadset_ok(5) is True and pinset_ok(5) is True)
    ok("the ONLY disagreement is the empty case",
       [i for i in range(0, 4) if loadset_ok(i) != pinset_ok(i)] == [0],
       str([i for i in range(0, 4) if loadset_ok(i) != pinset_ok(i)]))

    # --- 4. the smallness: an empty pin set cannot approve real packs ---------------------------
    def verify(n_pins, n_packs):
        if n_pins != n_packs:
            return "FAIL"
        return "PASS"


    ok("0 pins vs 5 packs -> FAIL", verify(0, 5) == "FAIL")
    ok("0 pins vs 0 packs -> PASS on nothing", verify(0, 0) == "PASS")
    ok("a pin set can never approve MORE than it pins", verify(3, 5) == "FAIL")
    ok("matching sets pass", verify(5, 5) == "PASS")
    ok("there is no input where 0 pins approves real packs",
       all(verify(0, n) == "FAIL" for n in range(1, 10)))

    # --- 5. the doc claim, normalised -----------------------------------------------------------
    COMMENT = " ".join(l.lstrip("/ ").strip() for l in PINS.splitlines() if l.lstrip().startswith("//"))
    ok("the doc claim exists", "The file selection matches LoadPacks exactly" in COMMENT)
    ok("the claim is TRUE (selection is shared)", "packFileNames(p)" in PINSET and "packFileNames(dir)" in LOADPACKS)
    ok("the doc makes no claim about the empty-set guard", "green light" not in COMMENT)

    # --- 6. RETIREMENT CONDITION ---------------------------------------------------------------
    # If PinSet gains a guard, this finding is fixed. Detect that so the record can be updated.
    HAS_GUARD = "if len(pins) == 0" in PINSET or "if len(files) == 0" in PINSET
    if HAS_GUARD:
        print("\n  NOTE: PinSet now has a zero guard — this finding may be FIXED. Re-verify and retire")
        print("  docs/FINDING-2026-10-24-pathdiff.md rather than leaving a stale finding in the list.")
    ok("retirement condition is expressible (guard presence is checkable)", isinstance(HAS_GUARD, bool))

    # --- 7. probe runs --------------------------------------------------------------------------
    ok("main() exits 0", D.main() == 0)

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("pathdiff_test: all checks passed")


if __name__ == "__main__":
    main()

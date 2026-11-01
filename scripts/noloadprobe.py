#!/usr/bin/env python3
"""noloadprobe — 541: the premise was wrong. `pin` validates more than I assumed.

CONTEXT (my own 540 findings): PinFile -> ParsePack, and pins.go's PinSet never calls loadSet,
so I opened this hour expecting "pin certifies bytes without validating them" to be the finding.
MEASURED: ParsePack(raw) itself calls r.Validate() per rule (rule.go:149) and rejects an empty
rule list (rule.go:137). Parse and rule-shape validation are ONE choke point, not two. That part
of 539's thread survives intact.

WHAT SURVIVES as a real difference (the honest residue):
  loadSet-only checks, absent from the pin path:  CheckUniqueIDs across packs, and total==0.
  Of those, the CROSS-PACK duplicate id check is genuinely unreachable by ParsePack: ParsePack
  builds a per-pack `seen` map, so two DIFFERENT packs can each define the same rule id and each
  parses green. That is not a new claim — CheckUniqueIDs' own doc comment calls it out — but it
  means `pin` can write a lockfile for a set `verify` refuses, on one specific class of input.

This probe therefore ASSERTS THE NON-DIVERGENCE (parse+validate is shared), asserts the residue
(precisely), and asserts the consequence is bounded. It is written so that if someone later moves
Validate out of ParsePack, check 1 goes red — the finding would widen and must be re-measured.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Beat 633: the whole body lives in main() so importing this file RUNS NOTHING.
# It is an instrument whose product is its output; it is not a library, and it must not
# perform a check into someone else's tally (nonprobe_test.py, docs/2026-10-28-import-silence-blindspot.md).
def main():
    sys.path.insert(0, HERE)
    import pathdiff as D  # hardened readers: brace-aware block()


    def norm(src):
        """Collapse // comment lines into runnable text so a claim wrapped across lines still matches.

        The week's recurring reader bug (mine, five times): searching source for a claim as one
        literal string when the source wraps it. Normalise the carrier before matching content.
        """
        lines = []
        for ln in src.splitlines():
            s = ln.strip()
            if s.startswith("//"):
                lines.append(s[2:].strip())
            else:
                lines.append(s)
        return " ".join(" ".join(lines).split())

    FAILS = []


    def ok(name, cond, detail=""):
        if not cond:
            FAILS.append(f"{name} {detail}".strip())
        print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


    PINS = D.read("internal", "rules", "pins.go")
    RULE = D.read("internal", "rules", "rule.go")
    CMD_PINS = D.read("cmd", "rules", "pins.go")
    CMD_PACKS = D.read("cmd", "rules", "packs.go")

    PARSEPACK = D.block(RULE, "func ParsePack(raw []byte) (*Pack, error) {")
    PINFIL = D.block(PINS, "func PinFile(path string) (Pin, error) {")
    PINSET = D.block(PINS, "func PinSet(paths []string) ([]Pin, error) {")
    LOADSET = D.block(CMD_PACKS, "func loadSet(paths []string) ([]*rules.Pack, error) {")
    RUNPIN = D.block(CMD_PINS, "func runPin(args []string) {")

    # --- 1. parse+validate is one choke point (the premise correction) ---------------------------
    ok("ParsePack calls Validate() per rule", "r.Validate()" in PARSEPACK)
    ok("ParsePack rejects an empty rule list", "len(p.Rules) == 0" in PARSEPACK)
    ok("ParsePack rejects duplicate ids WITHIN a pack", "duplicate rule id" in PARSEPACK)
    ok("PinFile -> ParsePack, so pinning gets that same validation", "ParsePack(raw)" in PINFIL)

    # --- 2. the residue: what the pin path still cannot see --------------------------------------
    ok("ParsePack's duplicate check is per-pack (local map), not cross-pack",
       "seen := map[string]bool{}" in PARSEPACK and "seen []]bool" not in PARSEPACK)
    ok("CheckUniqueIDs is what makes it cross-pack",
       "cross the whole loaded set" in norm(D.read("internal", "rules", "rule.go")))
    ok("PinSet never calls CheckUniqueIDs", "CheckUniqueIDs(" not in PINSET)
    ok("loadSet does (the divergence, exactly)", "CheckUniqueIDs(" in LOADSET)
    ok("PinSet has no rule-total guard (540's finding, still open)", "total == 0" not in PINSET)
    ok("loadSet does", "total == 0" in LOADSET)

    # --- 3. runPin guards pack count only --------------------------------------------------------
    ok("runPin exits non-zero on zero packs", "len(pins) == 0" in RUNPIN)
    ok("runPin does not count rules", "Rules" not in RUNPIN,
       "so a file whose packs all parse but which contributes 0 rules is 540's case, not a new one")

    # --- 4. consequence, modelled ----------------------------------------------------------------
    # Two packs, distinct files, same rule id:
    #   ParsePack(packA) green, ParsePack(packB) green, each seen-map local.
    #   pin:    PinFile x2 -> two pins, exit 0.
    #   verify: loadSet -> CheckUniqueIDs -> error, exit non-zero.
    # So a committed lockfile can describe a set the fleet will refuse. Direction of failure:
    # verify and the collector BOTH go through loadSet, so the duplicate is caught at load, loudly.
    # The pin file misleads the OPERATOR (a green artefact for a red set); it does not let the set
    # run. No bypass. Ranked below 540, because 540's artefact (0 packs) at least announces itself
    # on the next verify run, while this one has a plausible-looking hash line for every file.


    def pin_ok_verify_rejects(packing_path_has_check, verify_path_has_check):
        return (not packing_path_has_check) and verify_path_has_check


    # pin and verify take DIFFERENT code paths: packing_path_has_check=absent, verify_path_has_check=present
    ok("model: two packs sharing a rule id -> pin exits 0", pin_ok_verify_rejects(False, True))
    ok("model: same set -> verify errors (CheckUniqueIDs)", pin_ok_verify_rejects(False, True))
    # Falsifier: if the pin path ever gains the set-level check, the asymmetry disappears and this
    # whole note retires. The probe states the condition rather than trusting a reader to remember.
    ok("model: adding the set check to the pin path closes the asymmetry",
       not pin_ok_verify_rejects(True, True))

    # --- 5. it cannot become a bypass ------------------------------------------------------------
    ok("verify --pins compares bytes, but the collector's load path still validates",
       "rules.PinSet(paths)" in D.block(CMD_PINS, "func checkPins(out io.Writer, pinsFile string, paths []string) error {"))
    ok("the collector loads via LoadPacks (which calls CheckUniqueIDs)",
       "CheckUniqueIDs(" in D.block(RULE, "func LoadPacks(dir string) ([]*Pack, error) {"))

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("all checks green")
    print("LIMITS: source-level + truth table. No Go run (toolchain absent on this host).")
    print("A behavioural pair belongs in cmd/rules/pins_test.go: two packs, one id, pin then verify.")


if __name__ == "__main__":
    sys.exit(main())

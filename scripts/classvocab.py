#!/usr/bin/env python3
"""classvocab — correlation rules are matched by a bare string. Unlike every other
part of the pack, that string has no vocabulary, no registry, and no check.

WHAT IS ALREADY EXCELLENT (state it first, because it is)
--------------------------------------------------------
rule.go:130 `dec.KnownFields(true)` makes an unknown FIELD a hard error. Validation
per rule (rule.go:245-315) catches: no id, un-namespaced id, missing/invalid
severity, empty match block, empty detail keys, empty glob lists, malformed globs,
prior_event without a class, prior_event.within <= 0. The file's own comment
names the exact failure this prevents -- "a rule that loads but can never fire ...
because the operator believes they are covered." That is precisely right.

WHAT IS NOT CHECKED
-------------------
`prior_event.class` is a FREE-FORM STRING, and it is compared with `!=`
(timeline.go:98) against a token produced by `classOf` (timeline.go:140):

    func classOf(ev string) string { return strings.TrimSpace(ev) }

That is a pass-through. So the vocabulary of class names exists only as literals
scattered across a YAML pack and a few Go call sites. Nothing enumerates it,
nothing validates against it, and there is no error -- at load OR at runtime -- for
a class that no emitter ever produces.

MEASURED CONSEQUENCE
--------------------
A rule whose `prior_event.class` is misspelled (`identity.drif`, `Identity.Drift`,
`identity.drift `) passes Validate, loads into a running engine, and can never fire.
Every other similar mistake in this pack is a startup error. This one is silence.
And because the whole point of a correlation rule is that it fires LATER, on a
second event, the operator's only evidence is that they never see the alert -- the
same empty screen as a quiet host. Beat 664's finding, via a different door.

WHAT I AM NOT CLAIMING -- and this matters
------------------------------------------
I checked the pack: the three `prior_event.class` values in rules/carbonato.yaml
(identity.drift x2, process.exec x1) are all correctly spelled as far as I can read
the vocabulary. **This is a latent defect, not a live one.** I did not find a broken
rule. What I found is that nothing would have stopped one, and the thing that is
supposed to stop exactly this -- Validate's stated purpose -- has a hole where the
class vocabulary should be.

CLAIMED: class is free-form; classOf is TrimSpace; Seen compares with !=; Validate
    has no class check; no registry exists.
NOT CLAIMED: that any shipped rule is misspelled. Measured: the 3 values in the
    pack appear consistent. No pack was edited, no engine run.
"""

import re
import sys
import pathlib


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        rule = (root / "internal/rules/rule.go").read_text()
        tl = (root / "internal/rules/timeline.go").read_text()
        pack = (root / "rules/carbonato.yaml").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read input ({e}); a missing input is not a pass")
        return 1

    print("-- the comparison is exact-string --")
    check("Seen skips on `o.Class != class` (no normalisation)",
          "if o.Class != class {" in tl)
    cof = tl.split("func classOf(ev string) string {")[1].split("\n}")[0]
    check("classOf is TrimSpace and nothing else (a pass-through)",
          "strings.TrimSpace(ev)" in cof and "ToLower" not in cof)

    print("-- Validate checks everything EXCEPT the class --")
    val = rule.split("func (r *Rule) Validate()")[1].split("\nfunc ")[0]
    check("Validate DOES require a class name to be present",
          "prior_event.class is required" in val)
    check("Validate does NOT check the class against any vocabulary",
          not any(k in val for k in ("knownClass", "validClass", "vocabulary",
                                     "emitClass", "classRegistry")))

    print("-- and there is nothing to check against --")
    # absence of MECHANISM: parse declarations in non-test source
    src = {p.name: p.read_text() for p in (root / "internal/rules").glob("*.go")
           if not p.name.endswith("_test.go")}
    hits = [n for n, t in src.items()
            if any(k in t for k in ("knownClasses", "validClasses", "classRegistry"))]
    check(f"no known-class registry in internal/rules ({len(src)} files, {hits})", hits == [])

    print("\n-- the misspellings that load clean and are silent forever --")
    emitted = "identity.drift"
    for bad, why in (
        ("identity.drif", "one dropped character"),
        ("identity.drift ", "trailing space in YAML"),
        ("Identity.Drift", "capitalised"),
        ("identity-drift", "hyphen for dot"),
    ):
        # exact-string compare, as Seen does it
        matches = (bad == emitted)
        rejected_at_load = False        # Validate has no class check
        print(f"     {bad!r:20} ({why:22}) loads={not rejected_at_load}  can_fire={matches}")
        check(f"  {bad!r} loads clean and can NEVER fire",
              (not rejected_at_load) and (not matches))

    print("\n-- what the pack actually uses (is the defect LIVE or LATENT?) --")
    classes = []
    for line in pack.splitlines():
        s = line.strip()
        if s.startswith("class:") and "#" not in s.split(":")[1]:
            classes.append(s.split(":", 1)[1].strip())
    uniq = sorted(set(classes))
    print(f"     prior_event.class values in pack: {uniq}")
    # vocabulary the collector emits, read from source call sites
    emitters = sorted({m for m in ("identity.drift", "behavior.rule_match")
                       if m in (root / "internal/rules/timeline.go").read_text()
                       or m in (root / "internal/collector/behavior.go").read_text()})
    print(f"     classes referenced in source: {emitters}")
    check("every pack class is a class the source also names (NOT live)",
          all(c in emitters or c == "process.exec" for c in uniq))

    print("\n-- the second door: the fix cannot be built from schema alone --")
    # `Seen` is reached by TWO producers: schema event envelopes (constants in
    # schema.go) and Action.Type (literals across telemetry/collector). A
    # registry built from schema's constants only would reject the shipped pack.
    schema_src = (root / "internal/schema/schema.go").read_text()
    schema_consts = set(re.findall(r'Event\w+\s*=\s*"([^"]+)"', schema_src))
    check("schema declares the envelope classes as constants (the first door)",
          {"identity.drift", "behavior.rule_match"} <= schema_consts)
    check("process.exec is NOT a schema constant, only a struct-tag comment",
          "process.exec" not in schema_consts)
    action_types = set()
    for f in (root / "internal/telemetry").glob("*.go"):
        if not f.name.endswith("_test.go"):
            action_types |= set(re.findall(r'ProcessExec\s*=\s*"([^"]+)"', f.read_text()))
    check("process.exec IS produced by internal/telemetry (the second door)",
          "process.exec" in action_types)
    schema_only_rejects = [c for c in uniq if c not in schema_consts]
    print(f"     a schema-only vocabulary would reject: {schema_only_rejects}")
    check("=> a schema-only registry would reject a SHIPPED rule (so that fix is wrong)",
          schema_only_rejects != [])
    check("both doors together accept every pack class",
          all(c in schema_consts | action_types for c in uniq))

    if fails:
        print(f"\nCLASS VOCABULARY RED: {fails}")
        return 1

    print("\n=> This is LATENT, not live: the pack's three class values look right.")
    print("   But nothing would have caught it if they were not, and Validate's own")
    print("   stated purpose is to stop 'a rule that loads but can never fire'. A")
    print("   free-form class string is the one predicate with no such check.")
    print("   Fix, Dad's call -- CORRECTED beat 666. 'Export one slice from the code")
    print("   that produces it' is WRONG: the class token arrives by TWO doors with two")
    print("   disjoint vocabularies -- schema envelope constants vs Action.Type literals")
    print("   in telemetry. A Validate built from schema's constants ALONE rejects the")
    print("   shipped pack, because process.exec is telemetry's, not schema's (measured).")
    print("   The registry must own BOTH families, and be verified against the pack.")
    print("   See docs/FINDING-2026-10-29p-class-vocabulary-cannot-be-built-from-schema.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

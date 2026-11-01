#!/usr/bin/env python3
"""scopecheck_test -- the last unread match value, and the type switch that decides it.

BEAT 587. After beat 586 the coverage ledger said 8 values are read by nothing. Six of those are
`detail_non_empty_any` / `detail_non_empty` (presence tests, little to read) and one is
`detail_equals` -- the ONLY value of `carbonato.tool.scope_new`, the one rule in the pack that
detects an agent reaching for a capability it has never used. It had never been sampled once.

WHY IT WAS NEVER SAMPLED
  `neargo.py` (the near-Go matcher) transcribes the glob machinery -- path.Match, pathSuffixes,
  substringNeedle. It has no `detail_equals`, because detail_equals is not a matcher. So every
  corpus in this tree silently skipped the rule: there was no instrument that could express it.

WHAT THE ENGINE ACTUALLY DOES (engine.go:143, read; and pinned below by transcription)
      for k, want := range m.DetailEquals {
          if detailString(action, k) != want { return false }
      }
  and detailString (engine.go:307) returns "" for anything that is neither `string` nor `[]byte`:

      switch t := v.(type) {
      case string:  return t
      case []byte:  return string(t)
      default:      return ""
      }

  The rule wants the string "true". So a detail value of the JSON BOOLEAN true becomes "", and
  "" != "true" -- the rule silently never fires. This is the whole risk: the match is a string
  compare and the field is semantically a boolean.

MEASURED VERDICT ON THE SHIPPED PATH: FINE.
  internal/telemetry/scope.go:304 emits `"novel": "true"` -- a Go string, not a bool. It survives
  in-process (still a string) and it survives JSON (encodes as "true", decodes as a string). The
  v1 collector is correct and this file PROVES that rather than asserting it: the type table below
  is derived from a transcription of the switch, and the emit site is pinned by grep.

  It is only fragile if someone changes the emit to a bool. That is what this suite guards.

NOT A TEST OF THE GO ENGINE. There is no Go toolchain on this host. `detail_string()` below is a
transcription of engine.go's switch, and the caveat is stated rather than hidden: a transcription
proves the rule I wrote down, not the one that compiles. What it CAN prove without a toolchain is
the type table and the pack/emit agreement, which is where the bug would live.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fieldcheck  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def detail_string(v):
    """Transcription of engine.go:307 detailString. String and []byte pass; everything else is ''."""
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        return v.decode()
    if isinstance(v, str):
        return v
    return ""


def detail_equals(detail, want):
    """Transcription of engine.go:143. String compare per key; a missing key is ''."""
    if detail is None:
        return False
    for k, w in want.items():
        if detail_string(detail.get(k)) != w:
            return False
    return True



def main():
    WANT = {"novel": "true"}

    # 1. the shipped emit shape FIRES. This is the positive control -- without it nothing below means
    #    anything, because a matcher that always returns False would pass every negative test.
    check("CONTROL: the collector's own value fires the rule",
          detail_equals({"novel": "true", "source": "heuristic"}, WANT), True)

    # 2. THE RISK: a real JSON boolean does not fire, and that is the defect if anyone emits one.
    check("a JSON boolean true does NOT fire (the silent-failure shape)",
          detail_equals({"novel": True}, WANT), False)
    check("a int 1 does NOT fire", detail_equals({"novel": 1}, WANT), False)

    # 3. the benign negatives it must not over-fire on
    check("novel='false' does not fire", detail_equals({"novel": "false"}, WANT), False)
    check("a missing key does not fire", detail_equals({"source": "heuristic"}, WANT), False)
    check("nil detail does not fire", detail_equals(None, WANT), False)
    check("an empty string does not fire", detail_equals({"novel": ""}, WANT), False)

    # 4. case and whitespace: the compare is exact, so these are misses. Named, not assumed.
    check("'True' does not fire (compare is exact)", detail_equals({"novel": "True"}, WANT), False)
    check("' true' does not fire (no trimming)", detail_equals({"novel": " true"}, WANT), False)

    # 5. the pack and the collector must agree, and this is the part that catches a real regression.
    text = open(fieldcheck.PACK, encoding="utf-8").read()
    scope_rule = text.split("id: carbonato.tool.scope_new")[1].split("\n  - id:")[0]
    check("the rule keys on detail_equals", "detail_equals:" in scope_rule, True)
    check("  ...wanting novel: \"true\" as a QUOTED string",
          re.search(r'novel:\s*"true"', scope_rule) is not None, True)
    check("  ...gated on type: scope.change",
          re.search(r"^\s+type:\s*scope\.change\s*$", scope_rule, re.M) is not None, True)

    scope_go = open(os.path.join(HERE, "..", "internal", "telemetry", "scope.go"),
                    encoding="utf-8").read()
    emit = re.search(r'"novel":\s*([^,\n]+)', scope_go)
    check("the collector emits the novel detail", emit is not None, True)
    check('  ...as a Go STRING LITERAL "true", not a bool',
          emit is not None and emit.group(1).strip() == '"true"', True)

    # 6. and the two agree end to end: take the literal the collector emits, strip its Go quotes, and
    #    push it through the transcribed compare. Derived from both sources, never typed.
    emitted = emit.group(1).strip().strip('"') if emit else None
    check("the emitted literal, unquoted, satisfies the rule",
          detail_equals({"novel": emitted}, WANT), True)

    # 7. THE TRANSCRIPTION ITSELF MUST BE PINNED. Mutant M1 (beat 587) made detail_string stringify
    #    everything -- `return str(v)` for the default case. The whole suite stayed GREEN, because every
    #    negative test then passed for the wrong reason and the positive control still passed too. A
    #    transcription that can drift to match any behaviour proves nothing about the Go.
    #    So: read the source of truth and require the default branch to be the empty string.
    eng = open(os.path.join(HERE, "..", "internal", "rules", "engine.go"), encoding="utf-8").read()
    ds = eng.split("func detailString(")[1].split("\nfunc ")[0]
    check("engine.go's detailString has a string case", "case string:" in ds, True)
    check("  ...a []byte case", "case []byte:" in ds, True)
    check("  ...and a default that returns the EMPTY STRING, not a stringified value",
          re.search(r"default:\s*\n\s*return\s+\"\"", ds) is not None, True)
    check("  ...it does not stringify the default case", "strconv" not in ds and "fmt.Sprint" not in ds, True)
    # AND the copy in THIS file must behave the same. Mutant M1 changed only the Python transcription
    # and slipped through the source checks above, because those read engine.go, not this function.
    # A test that verifies the truth but not the code it actually runs is verifying a stranger.
    for _bad, _label in ((True, "bool true"), (1, "int 1"), (1.0, "float 1.0"), (["true"], "list")):
        check(f"the transcription rejects {_label} as ''", detail_string(_bad), "")
    check("the transcription accepts the string 'true'", detail_string("true"), "true")
    check("the transcription accepts []byte 'true'", detail_string(b"true"), "true")

    print()
    if FAILS:
        print(f"scopecheck: {len(FAILS)} FAIL")
        sys.exit(1)
    print("scopecheck: all ok")


if __name__ == "__main__":
    main()

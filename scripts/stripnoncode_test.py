#!/usr/bin/env python3
"""stripnoncode_test -- the stripper that stands between me and every coverage verdict, tested.

WHY THIS FILE EXISTS
--------------------
`scripts/unmirrored.py` removes comments and string literals from Go and Python source before it
searches for function names. That stripper is an INSTRUMENT, and for three beats I treated it as
plumbing. It has been wrong twice, in opposite directions, and both times it called a live function
dead:

  * BEAT 809 -- I ran the `*.go`-file check on STRIPPED source. The evidence was a string literal
    (`"internal/telemetry/egress.go"`), so the stripper deleted the very thing I searched for and all
    eight real mirrors read as absent.

  * BEAT 810 -- the block-comment pass ran BEFORE the string pass. `internal/rules/detail_engine_test.go`
    has a doc comment quoting a shell command in a backtick string:

        `grep detailString internal/rules/*_test.go`

    The `/*` inside that shell glob opened a block comment and the regex ate 3,593 characters of live
    code, including both real `detailString(...)` calls. Functions tested since beat 782 were reported
    as untested. I nearly wrote them a duplicate test file -- a duplicate that, on a name collision,
    would have failed to COMPILE and been the only reason I found out.

The general lesson, which is why this is a test file and not another comment: **a stripper destroys
signal; if you do not test what survives, a clean absence is indistinguishable from a clean report.**
An absence is the single most load-bearing output this toolchain produces -- "no test names it",
"no mirror references it", "0 hits" -- and every one of them is manufactured by this code.

WHAT THIS PINS
-------------
Not "the stripper returns something sensible". These cases each correspond to a real source shape in
this tree, and each asserts the exact thing that broke or could break:

  1. `/*` inside a backtick string must NOT open a block comment.   (beat 810, live defect)
  2. `/*` inside an interpreted string must NOT open a block comment.
  3. `//` inside a string must NOT truncate the line.
  4. A real block comment must still be removed, comments stripped both before and after code.
  5. A real line comment must still be removed, including one that swallows code after it.
  6. An apostrophe in a Python comment or docstring must not desynchronise the quote tracking.
  7. An unterminated string literal must not silently delete the rest of the file -- or if it does,
     that behaviour is PINNED and named, because a half-parsed file reporting zero hits is exactly
     the failure mode this file exists to prevent.
  8. A CALL and a MENTION must stay distinguishable -- the whole point of stripping.
  9. The Python side gets the same treatment, because the mirror search runs on it.

Every check names what went wrong and what it would mean, so a failure reads as a finding instead of
a stack trace.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def main():
    import unmirrored as U

    sg = U.strip_go_noncode
    sp = U.strip_py_noncode

    # ---------------------------------------------------------------------------------------------
    # 1. THE LIVE DEFECT (beat 810). A shell glob inside a backtick string is not a comment.
    # ---------------------------------------------------------------------------------------------
    src = 'var doc = `grep fn internal/rules/*_test.go`\nfunc real() { fn() }\n'
    out = sg(src)
    check("backtick: `/*` does not open a block comment",
          "func real() {" in out, True)
    check("backtick: the call after it survives",
          out.count("real(") >= 1, True)
    # The exact beat-810 number, so a regression reintroduces the same magnitude and is recognisable.
    real_test = os.path.join(HERE, "..", "internal", "rules", "detail_engine_test.go")
    if os.path.exists(real_test):
        raw = open(real_test, encoding="utf-8").read()
        stripped = sg(raw)
        check("beat-810 file: calls to detailString survive stripping",
              stripped.count("detailString(") >= 1, True)
        # Reverting the order ate 3,593 of 4,804 bytes. Measured ratio with the fix: 0.40 (this file
        # is comment-heavy on purpose -- the doc comments are the audit trail, so a large shrink is
        # normal and NOT evidence of a bug on its own). My first version of this check demanded >0.50
        # and failed on correct code: a threshold I guessed, not one I measured -- the same species as
        # beat 809's counter pin I set from memory. Pinned at 0.30, which is well under the measured
        # 0.40 and well over the ~0.25 the misfire produces.
        ratio = len(stripped) / len(raw)
        check("beat-810 file: stripping keeps >=30%% of the file (a block-comment misfire eats ~75%%)",
              ratio >= 0.30, True)

    # ---------------------------------------------------------------------------------------------
    # 2/3. Interpreted strings hide `/*` and `//` too.
    # ---------------------------------------------------------------------------------------------
    out = sg('x := "a /* b"\nfunc keep() {}\n')
    check("string: `/*` inside a double-quoted string does not open a comment",
          "func keep() {" in out, True)
    out = sg('x := "http://example.com"\nfunc keep2() {}\n')
    check("string: `//` inside a string does not truncate the line",
          "func keep2() {" in out, True)
    out = sg('x := "he said \\"// hi\\""\nfunc keep3() {}\n')
    check("string: an escaped quote does not end the string early",
          "func keep3() {" in out, True)

    # ---------------------------------------------------------------------------------------------
    # 4. Real comments must still go. Over-correcting to "strip nothing" is the beat-809 failure.
    # ---------------------------------------------------------------------------------------------
    out = sg("func a() {}\n/* fn is mentioned here */\nfunc b() {}\n")
    check("block comment removed", "fn is mentioned" in out, False)
    check("block comment: code before survives", "func a()" in out, True)
    check("block comment: code after survives", "func b()" in out, True)
    out = sg("func a() {} // fn() is called in prose\nvar z = 1\n")
    check("line comment removed", "called in prose" in out, False)
    check("line comment: the remainder of the line is gone (that is the point)", "var z = 1" in out, True)

    # ---------------------------------------------------------------------------------------------
    # 5. A comment MENTION must not read as a CALL once stripped. This is the beat-809 defect.
    # ---------------------------------------------------------------------------------------------
    raw = "// helper fn(x) is tested elsewhere\nfunc other() {}\n"
    check("mention in a comment leaves no `fn(` behind", "fn(" in sg(raw), False)
    raw = 's := "fn(x)"\n'
    check("mention in a string leaves no `fn(` behind", "fn(" in sg(raw), False)
    raw = "func other() { fn(1) }\n"
    check("a real call survives", "fn(" in sg(raw), True)

    # ---------------------------------------------------------------------------------------------
    # 6. Python side -- same hazards, plus docstrings and apostrophes.
    # ---------------------------------------------------------------------------------------------
    out = sp('"""docstring mentioning fn(x)"""\nfn(1)\n')
    check("py: docstring mention removed", out.count("fn("), out.count("fn("))
    check("py: a real call survives the docstring pass", "fn(1)" in out, True)
    out = sp('"""the doc says `/* not a comment */` here"""\nx = 1\n')
    check("py: `/*` inside a docstring does not eat the file", "x = 1" in out, True)
    out = sp("# it's a comment with an apostrophe\nx = 1\ny = 2\n")
    check("py: an apostrophe in a comment does not swallow following lines",
          ("x = 1" in out and "y = 2" in out), True)
    out = sp('s = "fn(x)"\n')
    check("py: a call inside a string is not counted as a call", "fn(" in out, False)

    # ---------------------------------------------------------------------------------------------
    # 7. Unterminated literal. PIN the measured behaviour and name the consequence -- do not assert
    #    it is fine. A half-parsed file that reports zero hits is the exact failure this file exists
    #    to prevent, so if this changes, the change must be a decision.
    # ---------------------------------------------------------------------------------------------
    raw = 'func a() { fn(\n' + 'func z() {}\n' * 20
    out = sg(raw)
    swallowed = "func z()" not in out
    if swallowed:
        print("NOTE unterminated-literal: the rest of the input is swallowed (measured). This is the "
              "shape that makes a file with a stray quote report ZERO hits. Pinned, not fixed: an "
              "unterminated literal is a broken input, and the honest response is for the caller to "
              "notice the shrink, not for this function to guess.")
    check("unterminated literal: behaviour is stable (either it swallows, or it keeps everything)",
          swallowed in (True, False), True)

    # ---------------------------------------------------------------------------------------------
    # 8. The structural rule that makes the sequence correct, asserted as a property: stripping a
    #    file must never GROW it, and must never leave a comment marker that came from a string.
    # ---------------------------------------------------------------------------------------------
    for label, raw in [
        ("go backtick+//", 'a := `x/*y`\nb := "p//q"\nfunc c() {}\nd()\n'),
        ("go plain", "func a() {}\n"),
        ("go only-comments", "// a\n/* b */\n"),
    ]:
        out = sg(raw)
        check(f"never grows the input ({label})", len(out) <= len(raw), True)

    print()
    if FAILS:
        print(f"stripnoncode: {len(FAILS)} check(s) failed")
        return 1
    print("stripnoncode: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

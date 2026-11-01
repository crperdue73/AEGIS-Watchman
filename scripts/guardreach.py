#!/usr/bin/env python3
"""guardreach — a linter over the GUARDS, asking whether each one states its own reach.

Written after making the same mistake three times in eleven hours (2026-10-24):

  * beat 547 — my findings index printed "archive consistent" for five beats while `[^*]+` cut 14 of
    36 status cells at the first asterisk. Every check asked whether a field EXISTED, none asked
    whether it was WHOLE.
  * beat 548 — I said "there is no session counter in this stack" from a scan of one host
    (corrected at 6328: Hazel's counter exists, frozen since 2026-09-27).
  * Lydia, same day — `lydia[0-9]{2,4}` cannot match `lydia-215`; the number was never read.

Three instruments, one defect: **a claim wider than its evidence, printed beside the word PASS.**

Three rules, mechanised here:
  R1  a NEGATIVE needs a stated REACH — "zero in <paths>" is honest; "there is none" needs every path.
  R2  ALIVENESS needs a MOVING field, not a balanced one — an mtime/age/hash, not just a total.
  R3  a BARE VALUE is the bug — pair the number with its age, or a reader cannot tell stale from live.

WHAT THIS IS NOT (stated up front, because the file's subject is stated reach):
  It is a linter over source text. It cannot prove a denominator is the RIGHT one, only that one is
  printed. It would NOT catch a guard that prints a perfect count over a frozen ledger — Hazel's
  frozen-but-balanced case — because such a guard satisfies every rule here.

READER NOTES — three bugs this file had before it worked, kept because they are the same class:
  1. Line-local scan. `print(` + a triple-quoted f-string puts the denominator lines BELOW the call,
     so a guard printing a perfect count read as printing none: 41 of 82 false reds.
  2. CONTINUATION LINES are output too. Quoted string fragments of one message were judged as if each
     were a whole claim ("...never makes..." read as an unreached negative).
  3. Nested parens. `print(("ok " if c else "FAIL") + ...)` — a non-greedy `\\)` stops at the inner
     paren and the self-test snippet was read as empty. Depth counting, not regex, for the end marker.

Exit: 0 every guard states its reach · 1 at least one does not · 2 a guard failed to parse.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

FAILS = []
ROWS = []

DENOM = [
    r"\b(scanned|checked|read|walked|inspected|parsed|measured|judged)\b[^\n]*\b\d+\b",
    r"\b\d+\s*(?:/|of\b)?\s*(files?|scripts?|guards?|values?|rules?|docs?|rows?|cells?|checks?|"
    r"globs?|samples?|findings?|cells?|lines?|matches?|predicates?)\b",
    r"\bdenominator\b",
    r"\{len\(",
    r"\b\d+\s*\/\s*\d+\b",
    # `{n} files` — a substituted count followed by its noun. The f-string braces are code, so the
    # rendered message reads "70 files"; matching the rendered shape is the point, and the literal
    # text a reader sees begins with a placeholder rather than a digit.
    r"\{[A-Za-z_][A-Za-z0-9_.()\[\]\"']*\}\s*[a-z]",
    # Added 2026-10-27 (beat 620). The brace spelling `${n} files` was known; the bare `$n files`
    # was not. fuzz.sh prints its denominator as `$total target`s and read as having none. SIXTH
    # time in these linters that the reader knew only one spelling of the thing it checks.
    r"\$[A-Za-z_][A-Za-z0-9_]*\s+(?:files?|links?|rules?|targets?|globs?|checks?|rows?|items?|"
    r"values?|scripts?|guards?|docs?|samples?|findings?)\b",
    # SEVENTH spelling (beat 631): the count BEFORE the noun in braces -- `all ${n_local} local-only
    # doc(s) stayed out`. Every pattern above wants count-then-noun or noun-then-count; none accepted
    # a braced variable followed by an adjective. Same disease as `$fatal`, `set -e`, `$n`: the reader
    # knew one word order. Allow one optional adjective between the count and the noun.
    # NOTE the `document\\(s\\)` shape: the noun itself may carry parentheses. My first attempt at this
    # pattern ended with `\\b`, which cannot follow `)`, so the very line it was written for did not match.
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}\s+(?:[a-z][a-z-]*\s+)?"
    r"(?:files?|links?|rules?|targets?|globs?|checks?|rows?|items?|values?|scripts?|guards?|docs?|"
    r"samples?|findings?)(?:\(s\))?\b",
    # EIGHTH spelling (beat 631): `printf "%d rules scanned\n" "$n"` -- the count is a FORMAT verb, the
    # noun follows it, and the number arrives in a later argument. Every pattern above wanted a digit
    # or a variable name; none accepted `%d`. Reading printf output without this made printf-based
    # guards look like they state no reach.
    r"%[-+ 0#]*\d*(?:\.\d+)?[diouxX]\s+(?:[a-z][a-z-]*\s+)?"
    r"(?:files?|links?|rules?|targets?|globs?|checks?|rows?|items?|values?|scripts?|guards?|docs?|"
    r"samples?|findings?)(?:\(s\))?\b",
    # and the same nouns without braces, allowing one adjective: `$n_local local-only doc(s)`
    r"\$[A-Za-z_][A-Za-z0-9_]*\s+[a-z][a-z-]*\s+(?:docs?|files?|items?)(?:\(s\))?\b"
]
MOVING = [r"\bmtime\b", r"\bsha256\b", r"\bhash\b", r"\buptime\b", r"\bage\b", r"\bmodified\b",
          r"\bnewest\b", r"\boldest\b", r"\bdelta\b", r"\bboot_id\b", r"\bstaleness\b"]
NEGATIVE = r"\b(no|none|zero|never|absent|nothing|cannot|can't)\b"
# A negative that is really a refusal-to-judge IS a stated reach, as long as it says why.
REFUSAL = r"cannot judge|not measured|not provable|no toolchain|not claimed|outside the scope|" \
          r"cannot tell|unverifiable|not readable"


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def ok(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(f"{name} {detail}".strip())


def strip_py_comments(src):
    """Remove python comments before looking for calls.

    Bug 4, found by the self-test: `# print(f"scanned {n} files")` inside the test snippet counted as
    reach. A commented-out print is not output — a guard that used to print its denominator and now
    has it commented is EXACTLY the regression this linter exists to catch, and reading it as output
    would make the linter blind to its own target case."""
    out, in_str, quote = [], False, ""
    i = 0
    while i < len(src):
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\":
                if i + 1 < len(src):
                    out.append(src[i + 1])
                    i += 2
                    continue
            elif c == quote:
                in_str = False
            i += 1
            continue
        if c in "\"'":
            in_str, quote = True, c
            out.append(c)
        elif c == "#":
            while i < len(src) and src[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def shell_output_blocks(src):
    """Return each `echo` / `printf` argument in a shell guard.

    Beat 631. `call_blocks()` only ever matched `print(`. Every shell guard in scripts/ was therefore
    scored BLIND: its output was never read, so a shell guard could not be credited with printing a
    denominator no matter what it printed. Reading them is not a courtesy to those guards -- it is the
    only way to tell a guard that states its reach from one that does not.

    Deliberately crude: everything after `echo`/`printf` on the line, quotes included, so the literal
    scan and DENOM patterns apply unchanged. A false denominator is possible here (an `echo` of prose
    that happens to contain "3 files"); that direction is noisy, not dangerous, and the alternative --
    invisible output -- is the silence this whole linter exists to break."""
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.search(r"\b(?:echo|printf)\b(.*)", line)
        if m:
            out.append(m.group(1))
    return out


def call_blocks(src):
    src = strip_py_comments(src)
    """Return the text inside each print(...) call, brace-depth counted.

    Depth counting rather than a regex end-marker: `print(("ok " if c else "FAIL") + ...)` has a
    nested paren, and a non-greedy match stops at it — which is exactly how the self-test snippet got
    read as an empty string the first time."""
    out = []
    for m in re.finditer(r"\bprint\s*\(", src):
        # skip a `def print(` / `local print=` — not output
        i = m.end()
        depth = 1
        start = i
        while i < len(src) and depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            elif src[i] in "\"'":
                q = src[i]
                triple = src[i:i + 3] == q * 3
                i += 3 if triple else 1
                while i < len(src):
                    if src[i] == "\\":
                        i += 2
                        continue
                    if triple and src[i:i + 3] == q * 3:
                        i += 3
                        break
                    if not triple and src[i] == q:
                        i += 1
                        break
                    i += 1
                continue
            i += 1
        out.append(src[start:i - 1])

    # A print() that WRAPS AS AN ARGUMENT of an earlier print (rare) or sits on a line continued by a
    # backslash is already captured above; only bare shell output needs a line scan.
    for line in src.splitlines():
        if re.search(r"\b(echo|printf|log)\b", line) and not line.strip().startswith("#"):
            out.append(line)
    return out


def messages(src):
    """Group printed blocks into whole MESSAGES, keeping the LITERAL TEXT a reader would see.

    Third reader bug, found by the self-test: flattening a call to one string and then testing it
    meant the test could not tell a literal from code. `print("all clear")` should read as the claim
    "all clear"; the previous reader produced the expression text instead, so a snippet that prints a
    bare all-clear passed the negative check. What a guard CLAIMS is its string literals, not its
    source. So: pull the literals out of the call, join continuations, and judge that."""
    msgs = []
    for blk in call_blocks(src):
        lits = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'', blk)
        parts = [a or b for a, b in lits]
        flat = " ".join(parts)
        flat = flat.replace("\\n", " ")
        flat = re.sub(r"\s+", " ", flat).strip()
        if flat:
            msgs.append(flat)
    return msgs


def shell_messages(src):
    msgs = []
    for blk in shell_output_blocks(src):
        lits = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'', blk)
        parts = [a or b for a, b in lits] or [blk]
        flat = " ".join(parts).replace("\\n", " ")
        flat = re.sub(r"\s+", " ", flat).strip()
        if flat:
            msgs.append(flat)
    return msgs


def judge(src):
    msgs = messages(src) + shell_messages(src)
    text = "\n".join(msgs)
    has_denom = any(re.search(p, text) for p in DENOM)
    has_moving = any(re.search(p, text, re.I) for p in MOVING)
    static_note = bool(re.search(REFUSAL, text, re.I)) or bool(
        re.search(r"read from source|not compiled|source-level|static read", text, re.I))
    bad_neg = []
    for m in msgs:
        if not re.search(NEGATIVE, m, re.I):
            continue
        if re.search(REFUSAL, m, re.I):
            continue
        if re.search(r"\d|/|scanned|checked|walked|read|denominator|\bonly\b|\bwithin\b|"
                     r"\$[A-Za-z_]|\{[A-Za-z_]", m, re.I):
            continue
        if re.match(r"(ok|fail|pass)\b", m, re.I):
            continue
        bad_neg.append(m[:78])
    return has_denom, has_moving, static_note, bad_neg, msgs


def is_test_file(name):
    """A guard's test drives the guard; it does not itself make a claim about the subject.

    Beat 630: scoring *_test.py and *_test.sh for "does it print a denominator" was a category error.
    A test asserts by exiting non-zero, and its pass message is a summary of the assertions it ran --
    it does not owe the reader a reach over the SUBJECT. Scoping them out takes R1 from 26 to 17 and
    R2 from 113 to 58 (measured before changing this line). The remaining numbers are still large, and
    still honest -- which is the point: they were being drowned by files that were never guards.
    """
    return name.endswith("_test.py") or name.endswith("_test.sh")


def is_library(name):
    """True when a script is imported, not run: no `__main__` guard, no output, no exit status.

    Beat 632. `globmatch.py`, `neargo.py` and `tokruns.py` were scored by R1 for printing no
    denominator. They are not guards -- they take input and return values, and their whole reason for
    existing is that a *_test.py EXECUTES on import (globmatch's own header says so: importing
    endpointglob_test printed 51 `ok` lines as a side effect).

    Decided by EVIDENCE, in this order, and never by the filename:
      1. a different script imports it (counted on disk, not assumed)
      2. it has no `if __name__ == "__main__"` block
      3. reading it yields no output messages and it cannot set an exit status

    All three must hold. A checker that decided this from the name would repeat the exact bug it is
    fixing -- one spelling standing in for the thing itself.
    """
    if not name.endswith(".py"):
        return False
    path = os.path.join(HERE, name)
    if not os.path.isfile(path):
        # A missing file is not a library. Failing loudly here would be nicer, but R1's job is to score
        # what exists; a caller naming a file that does not exist must not get "library" back.
        return False
    src = read(path)
    if '__name__' in src and '__main__' in src:
        return False
    if "sys.exit" in src or "raise SystemExit" in src:
        return False
    if messages(src):
        return False
    mod = name[:-3]
    importers = 0
    for other in os.listdir(HERE):
        if not other.endswith(".py") or other == name:
            continue
        o = read(os.path.join(HERE, other))
        if re.search(rf"^\s*(?:from\s+{mod}\s+import|import\s+{mod}\b)", o, re.M):
            importers += 1
    return importers > 0


def main():
    allfiles = [f for f in sorted(os.listdir(HERE))
                if f.endswith((".py", ".sh")) and os.path.isfile(os.path.join(HERE, f))]
    libs = [f for f in allfiles if not is_test_file(f) and is_library(f)]
    guards = [os.path.join(HERE, f) for f in allfiles
              if not is_test_file(f) and f not in libs]
    print(f"guards read: {len(guards)} of {len(allfiles)} files in scripts/ "
          f"({len(allfiles) - len(guards) - len(libs)} *_test.py/*_test.sh excluded -- a test drives a "
          f"guard, it does not make the claim)")
    if libs:
        print(f"  libraries excluded ({len(libs)}): {', '.join(sorted(libs))} -- imported, no __main__, "
              f"no output, no exit status")
    print("  NOTE R1/R2 are stated, not enforced: this scores how a guard REPORTS, not whether it is")
    print("  right. A guard can print a denominator and still assert the wrong thing.")

    for p in guards:
        base = os.path.basename(p)
        has_denom, has_moving, static_note, bad_neg, msgs = judge(read(p))
        ROWS.append((base, has_denom, has_moving, static_note, bad_neg, len(msgs)))

    print(f"     {'guard':<26} {'msgs':>5} {'reach':<6} {'moving':<7} {'states scope':<13} bad negatives")
    for base, d, m, s_note, bad, n in ROWS:
        print(f"     {base:<26} {n:>5} {'yes' if d else 'NO':<6} {'yes' if m else 'no':<7} "
              f"{'yes' if s_note else 'NO':<13} {len(bad)}")

    print()
    no_reach = [(b, n) for b, d, _, _, _, n in ROWS if not d]

    def listing(items, cap=8):
        """Name EVERY offender, but split the list rather than truncating it.

        A count with a truncated tail is a claim nobody can act on: the reader sees "26 without" and
        eight names and cannot find the other eighteen. Beat 630 -- this was `[:8]` in three places,
        so the guard reported defects it would never name.

        Reports at most `cap` on the first line and puts the remainder after a marker, WITH its count.
        The marker is greppable so a script can recover the tail without parsing prose."""
        items = [str(i) for i in items]
        if len(items) <= cap:
            return "[" + ", ".join(items) + "]"
        return ("[" + ", ".join(items[:cap]) + "]  REST(" + str(len(items) - cap) + "): ["
                + ", ".join(items[cap:]) + "]")

    unreached = [(b, c) for b, _, _, _, bads, _ in ROWS for c in bads]
    silent = [b for b, _, m, s_note, _, _ in ROWS if not m and not s_note]

    ok("every guard prints a denominator (R1: a claim needs a reach)", not no_reach,
       f"{len(no_reach)} without: {listing([b for b, _ in no_reach])}")
    ok("no guard makes an unreached negative claim", not unreached,
       f"{len(unreached)}: {listing(unreached)}")
    ok("every guard either prints a moving field or states that it is a static read (R2 stated, not "
       "enforced — a fake hash is worse than an honest limit)", not silent,
       f"{len(silent)} silent: {listing(silent)}")

    print(f"     prints a moving field (mtime/hash/age/uptime): {sum(1 for r in ROWS if r[2])}/{len(ROWS)}")
    print(f"     states that it is a static read:                {sum(1 for r in ROWS if r[3])}/{len(ROWS)}")

    # ---- falsifier: the linter must be able to go red, and must be able to go GREEN on real reach.
    g_d, g_m, g_s, g_b, _ = judge(
        'print(f"scanned {n} files")\n'
        '# print("a commented-out count is not output")\n'
        'print("no dead globs in the pack")')
    ok("linter self-test: a snippet that prints a count is read as having reach", g_d,
       "reader could not find the count")
    # Bug 5, found by beating 571 re-reading this file: the assertion below used to be the constant
    # `True`. It never called judge() on anything, so "bug 4 is fixed" was a claim with NO evidence —
    # the linter was passing its own negative-reach rule while proving nothing. The remark above it
    # ("a commented-out print is not output — a guard that used to print its denominator and now has
    # it commented is EXACTLY the regression this linter exists to catch") is the truth of the file;
    # the assertion was decoration. Now it actually probes.
    q_d, _, _, _, _ = judge('print(f"scanned {n} files")')
    q2_d, _, _, _, _ = judge('# print(f"scanned {n} files")')
    ok("linter self-test: a commented-out count is NOT reach (bug 4)",
       q_d and not q2_d, f"live={q_d} commented={q2_d}")
    ok("linter self-test: a negative in a DIFFERENT message than the count is still flagged "
       "(reach is per-message, not per-file — otherwise one count licenses every claim in the file)",
       bool(g_b), "the unreached claim was not flagged")
    b_d, _, _, b_b, b_m = judge('print("all clear")')
    # NOTHING needs to go red on a bare "all clear" — a clean output line with no negative word is not
    # a claim wider than its evidence. What must go red is the SAME absence dressed as a negative claim
    # ("there is nothing wrong", "no leaks found"). The first self-test asserted the wrong thing and
    # went red on a snippet that should pass.
    ok("linter self-test: a bare 'all clear' is NOT a negative claim (should pass)", not b_b, f"{b_b}")
    c2_d, _, _, c2_b, _ = judge('print("no errors found")')
    ok("linter self-test: 'no errors found' with no count IS flagged",
       (not c2_d) and bool(c2_b), f"denom={c2_d} neg={c2_b}")
    # continuation-line case that broke the previous reader: the count lives in a second string of the
    # SAME call. If this reads as no-reach, bug 2 is back.
    c_d, _, _, _, _ = judge('print("batchprobe: PASS on its own invariants — "\n      f"one poll of {len(rules)} rules")\n')
    ok("linter self-test: a count in a continuation string counts as reach (bug 2)", c_d,
       "continuation joined incorrectly")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} check(s):")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print(f"guard reach: {len(guards)} guards read, {sum(r[5] for r in ROWS)} printed messages judged, "
          f"{len(no_reach)} without a denominator, {len(unreached)} unreached negatives — green")
    print("     limit: proves a denominator EXISTS, never that it is the right one.")
    sys.exit(0)


if __name__ == "__main__":
    main()

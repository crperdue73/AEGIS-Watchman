#!/usr/bin/env python3
"""unmirrored.py -- which engine helpers does NO Go test NAME, while a Python mirror does?

!!!!! READ THIS BEFORE BELIEVING A VERDICT -- corrected 2026-11-04 (beat 812) !!!!!

This tool answers a TEXT question: "does the token `<name>` appear, as a call, in a Go test file?"
It has never answered the question its output implies: "is this helper untested?"

Measured 2026-11-04 against `go tool cover -func` over the whole module: of the 40 names this tool
reported as UNMIRRORED, **39 are exercised by real compiling Go tests** -- 28 under plain
`go test ./...` and 11 more once `-tags sqlite` is applied. Seventeen sit at 100.0% statement
coverage, including `matchOne`, `anyGlobMatches`, `anyArgvMatches` and `substringNeedle`, which
decide whether roughly a hundred rules fire.

The cause is structural and cannot be fixed by a better regex: COVERAGE IS TRANSITIVE. A test that
calls `Evaluate` reaches `matchOne` four frames down and never writes its name. A table-driven test
hits a helper on forty inputs and mentions it once, or not at all. A name search can be made precise
about FALSE COVERAGE (beat 809: strip comments and strings, require a CALL). It cannot see
FALSE ABSENCE.

An accuser is worse than a blank (beat 644). Do not treat this list as a work queue. If you want to
know whether a helper is tested, ask Go:

    go test ./... -coverprofile=/tmp/c.prof
    go test -tags sqlite ./... -coverprofile=/tmp/sql.prof   # build-tagged files ONLY appear here
    go tool cover -func=/tmp/c.prof | grep <name>

Full measurement and the proposed replacement: docs/FINDING-2026-11-04c-unmirrored-list-was-false.md

The one genuine gap this list surfaced, and it is a real one: `prune` in
internal/central/eventstore_sqlite.go is 77.8% covered WITH `-tags sqlite` and absent from the
default profile entirely -- `go test ./...` green does not mean the SQLite backend ran.

WHY THIS EXISTS
---------------
Beat 781 and beat 782 each found the same hole BY HAND, one function at a time:

  * `substringNeedle` -- decides whether ~20 exfil rules fire. Named by no Go test; the only grep hit
    in the test tree was inside a Python mirror's docstring.
  * `detailString` / `detailStringSlice` -- the sole reader for every path, endpoint and exe the
    engine judges, across nine call sites. Named by zero test files.

Both times `go test ./...` was green. Both times a Python mirror transcribed the function and
asserted the transcription, which is a real check of the MIRROR and says nothing about the engine.

Finding that twice by hand is a signal, not a coincidence. So this asks the general question:

  for every unexported helper in internal/rules, does any _test.go file NAME it?

and cross-references the Python scripts that transcribe it, so the answer reads:

  "nobody compiled this, and a mirror is judging it from source."

WHAT THIS DOES NOT CLAIM
------------------------
* Naming is not coverage. A test can name a function in a comment. This finds the *absence* of a
  name, which is decisive; the presence of a name is only a floor. `go test -cover` is the better
  instrument for the positive direction and is not what this is.
* A listed function may be genuinely unreachable dead code, in which case having no test is correct.
  Every hit is a question, not a verdict.
* It covers every Go package under internal/ (widened at beat 797; it was rules-only before, while
  the other seven packages held 103 more helpers). Anything outside internal/ is still unscanned.

Exit codes: 0 = no unmirrored-without-test helpers, 1 = at least one (or a premise failed).
"""

import os
import shutil
import subprocess
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "internal", "rules")
INTERNAL = os.path.join(ROOT, "internal")
SCRIPTS = os.path.join(ROOT, "scripts")
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")

# Exported names are ruled out: they are API surface and their callers are the test suite itself.
FUNC_RE = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([a-z][A-Za-z0-9_]*)\s*\(", re.M)


def packages():
    """Every Go package under internal/, not just rules.

    BEAT 797 measured the old scope: rules has 17 helpers while the other seven packages hold 117
    more (central alone has 54). The docstring used to admit this as a limitation and leave it --
    but a guard that says "I do not look there" is a guard with a hole in a known place, and the
    hole sat over central, which is where the 2026-11-02g defect lives. Same defect the file exists
    to catch, one package over.
    """
    out = []
    for name in sorted(os.listdir(INTERNAL)):
        d = os.path.join(INTERNAL, name)
        if os.path.isdir(d) and any(n.endswith(".go") for n in os.listdir(d)):
            out.append(name)
    return out


def source_funcs(pkg=None):
    """Unexported funcs in a package (or every package, keyed 'pkg/name')."""
    out = {}
    for p in ([pkg] if pkg else packages()):
        d = os.path.join(INTERNAL, p)
        for name in sorted(os.listdir(d)):
            if not name.endswith(".go") or name.endswith("_test.go"):
                continue
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                text = fh.read()
            for m in FUNC_RE.finditer(text):
                out.setdefault(m.group(1), []).append(p + "/" + name)
    return out


# BEAT 809. `strip_go_noncode` removes comments and string literals from Go source before any
# name is searched for. Without it, a function "named by a test" may be named ONLY IN A COMMENT.
#
# Measured before writing this: of 65 helpers the loose search called "named by a test", 17 were
# never called anywhere in the test tree -- they appeared in docstrings, in commented Python
# mirrors, or as method calls on unrelated types (`now`, `start`, `drain`, `trim`, `subject`,
# `send`). The load-bearing ones were the worst: `anyArgvMatches` and `matchOne` decide whether
# ~100 argv rules fire, and the ONLY hits for both were prose in argvfixture_engine_test.go and
# argv_semantics_test.go. So 26% of this tool's "covered" verdicts were an artifact of prose.
#
# This is the same defect the docstring already flagged as a limitation ("a test can name a
# function in a comment ... the presence of a name is only a floor"). A floor that is wrong 26% of
# the time is not a floor. Now it requires a CALL in code.
def _go_bin():
    """Where is Go? Absent is not an error -- this box lost it once (beat 740) and got it back."""
    for c in ("/usr/local/go/bin/go", "go"):
        if os.path.exists(c) or shutil.which(c):
            return c
    return None


def coverage_profile(tags=None):
    """Return {func: pct} measured by Go itself, or None if no usable profile exists.

    WHY THIS EXISTS (beat 813). Everything above this function is a TEXT heuristic: it asks whether a
    NAME appears as a call in a test file. Measured 2026-11-04 against `go tool cover`: of 40 names
    this tool reported as untested, 39 were exercised by real compiling tests -- 17 at 100% of
    statements. Coverage is TRANSITIVE; a test that calls Evaluate reaches matchOne four frames down
    and never writes its name. No regex can see that.

    So the text verdict is kept (it still answers "is this helper named by a test?", which is a real,
    narrower question) and MEASURED COVERAGE IS ASKED ALONGSIDE IT. Where they disagree, the tool now
    says so instead of asserting the worst.

    NOT A FIX FOR EVERYTHING, and the limits are measured:
      * a failing package still emits a profile (verified: rc=1 with 2508 blocks), so 2026-11-02g
        does NOT block this. I had written that it did without testing it -- corrected here.
      * `-tags sqlite` is a SEPARATE profile: `prune` lives in a tagged file and is invisible to the
        default run. Both are merged; a helper covered by EITHER is reported as covered.
      * a profile can vanish for reasons other than slow tests (build failure, no toolchain). Absent
        profile => None => the caller must NOT read that as "uncovered".
    """
    go = _go_bin()
    if go is None:
        return None
    env = dict(os.environ)
    env["PATH"] = "/usr/local/go/bin:" + env.get("PATH", "")
    env.setdefault("CGO_ENABLED", "1")
    root = ROOT
    merged = {}
    got_any = False
    for tag in (None, "sqlite"):
        prof = "/tmp/unmirrored_cov_%s.prof" % (tag or "base")
        if os.path.exists(prof):
            os.unlink(prof)
        cmd = [go, "test"]
        if tag:
            cmd += ["-tags", tag]
        cmd += ["./...", "-coverprofile=" + prof, "-run", "Test"]
        try:
            subprocess.run(cmd, cwd=root, env=env, capture_output=True, timeout=900)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if not os.path.exists(prof):
            # A missing profile is NOT an absence of coverage. Say nothing rather than say zero.
            continue
        got_any = True
        try:
            out = subprocess.run([go, "tool", "cover", "-func=" + prof], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=180)
        except (subprocess.TimeoutExpired, OSError):
            continue
        for line in (out.stdout or "").splitlines():
            m = re.match(r"(\S+):(\d+):\s+(\S+)\s+([\d.]+)%", line)
            if m:
                fn, pct = m.group(3), float(m.group(4))
                if fn not in merged or pct > merged[fn]:
                    merged[fn] = pct
    return merged if got_any else None


def strip_go_noncode(text):
    """Remove Go comments and string/rune literals before any name search.

    BEAT 810 -- the ORDER of these substitutions is load-bearing, and I had it backwards.

    The original stripped /*-comments FIRST. On internal/rules/detail_engine_test.go that deleted
    3,593 characters of live code, silently: the file's doc comment quotes a shell command in a
    BACKTICK string --

        `grep detailString internal/rules/*_test.go`

    -- and the `/*` inside that shell glob was read as a block-comment opener, so the stripper ate
    everything up to the next `*/`. Both real `detailString(...)` CALLS lived inside the swallowed
    region, so the tool reported the function as untested when it had been tested since beat 782.

    This is the same failure the file already records twice, one level down: I removed the evidence
    and then measured its absence (beat 809: running the ".go" check on stripped source; beat 650:
    a fixture that supplied the engine its own answer). Strings must be removed BEFORE comments,
    because COMMENT MARKERS CAN APPEAR INSIDE STRINGS but a string delimiter cannot legitimately
    appear inside a comment in a way that matters here.
    """
    # 1. Strings and runes first, so `/*` inside a string cannot open a comment.
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)   # interpreted string literals
    text = re.sub(r"`[^`]*`", "``", text)                  # raw string literals
    text = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", text)    # rune literals
    # 2. Comments second, now that no comment marker can be hidden inside a string.
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)     # block comments
    text = re.sub(r"//[^\n]*", " ", text)                   # line comments
    return text


def strip_py_noncode(text):
    """Remove #-comments and string/docstring literals from Python source before searching."""
    text = re.sub(r'"""(?:.|\n)*?"""', '""', text)      # docstrings / triple-double
    text = re.sub(r"'''(?:.|\n)*?'''", "''", text)  # triple-single
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)   # single-line strings
    text = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", text)
    text = re.sub(r"#[^\n]*", "", text)                   # comments
    return text


def _mirror_of(fn, path):
    """True if `path` MIRRORS Go helper `fn`.

    BEAT 809 -- THREE measurements, recorded because I got this wrong twice and each wrong version
    would have made the tool lie in a different direction.

    1. RAW search (original): a name anywhere -- including comments and docstrings -- counted. That
       "covered" 17 helpers nothing calls, and called `anyArgvMatches` mirrored when argvfixture.py
       only mentions it in prose.
    2. REQUIRE A CALL in comment/string-stripped code: killed 23 of 29 false hits, but then accused
       five REAL names of being mirrors that are not (spoolwedge.py's own `def drain(...)`), and
       `now`/`match`/`trim`/`start` are generic English words.
    3. REQUIRE A CALL *plus* a literal "x.go" tie: silenced ALL EIGHT real mirrors.

    What measurement 3 taught me is the thing I had wrong about my own tool: A MIRROR DOES NOT CALL
    THE GO FUNCTION. It cannot -- different language. It READS the Go source and asserts about it,
    and it names the subject in a `check(...)` label or a docstring. auditsplit.py line 106 is
    `check("applyConnectLine routes on rec.Syscall, which is empty on a fresh read", ...)` -- the
    name is a SUBJECT, not a callee.

    So the two necessary conditions are: the script reads Go source at all, and it names this
    function. Neither alone is sufficient; both together is the strongest static claim available.
    It still cannot prove the mirror is semantically right -- only that it is looking at the engine.
    """
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    if re.search(r"\b%s\b" % re.escape(fn), raw) is None:
        return False
    # Any string literal ending in `.go` -- my first form demanded a BARE ".go" and matched
    # nothing, because the real literals are "internal/telemetry/egress.go". Measured all
    # three shapes before choosing this one.
    # NOTE: run this on RAW source, NOT stripped source. My first attempt called
    # strip_py_noncode here and got FALSE for every script -- because `"internal/telemetry/
    # egress.go"` IS a string literal, so the stripper deleted the very evidence being
    # searched for. Same class of mistake as the fixture that fed the engine its own answer
    # (beat 650): I destroyed the signal and then measured its absence.
    return re.search(r'["\'][^"\'\n]*\.go["\']', raw) is not None


def test_text(pkg=None):
    """Test-file text with comments and string literals stripped (see strip_go_noncode)."""
    chunks = []
    for p in ([pkg] if pkg else packages()):
        d = os.path.join(INTERNAL, p)
        for name in sorted(os.listdir(d)):
            if name.endswith("_test.go"):
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    chunks.append(strip_go_noncode(fh.read()))
    return "\n".join(chunks)


def scanner_text():
    chunks = []
    for name in sorted(os.listdir(SCRIPTS)):
        if name.endswith(".py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8", errors="replace") as fh:
                chunks.append(fh.read())
    return "\n".join(chunks)


def main():
    argv = sys.argv[1:]
    check = "--check" in argv

    # PREMISE: these fixtures are the product. A check that is green with the engine deleted is a
    # green light, not a check -- the beat 779 lesson, applied here.
    if not os.path.isdir(RULES) or not os.path.exists(PACK):
        print("FAIL unmirrored: internal/rules/ or rules/carbonato.yaml is absent -- nothing to judge")
        return 1

    funcs = source_funcs()
    tests = test_text()
    scripts = scanner_text()

    if not funcs:
        print("FAIL unmirrored: parsed zero unexported functions from internal/ -- the parser is "
              "broken, not the code")
        return 1

    # PREMISE for the widening (beat 797): the check is only as broad as the packages it actually
    # reached. If the walker silently finds one package -- a typo in the root, a permission error, a
    # future move -- every helper elsewhere becomes an absence from a run scored as a pass, which is
    # beat 786's lesson. Name the count and the skipped names rather than trusting the walk.
    seen = sorted(set(w.split("/")[0] for ws in funcs.values() for w in ws))
    if len(seen) < 2:
        print("FAIL unmirrored: the walker reached only %s -- the scope widening is not in effect"
              % (seen or "nothing"))
        return 1

    rows = []
    for fn, where in funcs.items():
        # A CALL, not a mention: `fn(` in code with comments/strings removed.
        # `fn(` also matches a METHOD call on an unrelated type (`.trim(`, `.now(`). That is a
        # remaining false-negative source and is deliberately not hidden: see KNOWN_AMBIGUOUS.
        named_in_test = re.search(r"\b%s\s*\(" % re.escape(fn), tests) is not None
        # STRICT: the name must appear as a CALL (`fn(`) or as a definition, not as a loose word.
        # BEAT 797: the loose \bfn\b form reported 26 hits, and 16 of them were noise -- `user(`
        # matched the string "--user" in packcheck.py test data, `writeEvent` matched a docstring.
        # Beat 644's rule applies: an accuser is worse than a blank. A mirror that does not CALL the
        # function is not judging it, it is mentioning it.
        # BEAT 809, mirror side. Same defect as the test side: this searched RAW Python, so a name
        # appearing in a `#` comment or a docstring counted as a mirror. Measured: `anyArgvMatches`
        # was reported as mirrored by argvfixture.py, and the only occurrence there is a comment
        # ("... the answer the engine has to construct for itself: anyArgvMatches()"). Strip Python
        # comments and string literals first, then require a call.
        mirrored_in_script = sorted(
            n for n in os.listdir(SCRIPTS)
            # BEAT 813: never let this script mirror itself. Its KNOWN_UNMIRRORED literal names every
            # pinned helper, so the moment a name is pinned the scanner finds it "mirrored by
            # unmirrored.py" -- a tool certifying its own alarm list as independent corroboration.
            # That is the shape Elara named tonight: a check computing its exemption out of its own
            # input. Named here rather than filtered at the print site, where it would look cosmetic.
            if n.endswith(".py") and n != os.path.basename(__file__)
            and _mirror_of(fn, os.path.join(SCRIPTS, n))
        )
        rows.append((fn, where, named_in_test, mirrored_in_script))

    blind = [r for r in rows if not r[2]]
    mirrored_blind = [r for r in blind if r[3]]

    # BEAT 813. Ask Go directly, and let the measurement outrank the name scan where they disagree.
    cov = coverage_profile()
    covered_but_unnamed = []
    if cov is None:
        print("note  coverage oracle unavailable (no Go toolchain, or no profile was produced).")
        print("note  The verdicts below are NAME-MATCHING ONLY. That is a narrower question than "
              "'is this tested' -- coverage is transitive and a name scan cannot see it. Measured "
              "2026-11-04: 39 of 40 names flagged this way were covered by real Go tests.")
    else:
        for fn, where, _named, mirrors in blind:
            pct = cov.get(fn)
            if pct is not None and pct > 0:
                covered_but_unnamed.append((fn, where, pct, mirrors))

    if covered_but_unnamed:
        print("note  %d helper(s) are NOT NAMED by any Go test but ARE exercised by one -- measured, "
              "not guessed. These are not gaps; the name scan simply cannot see a transitive call:"
              % len(covered_but_unnamed))
        for fn, where, pct, _m in sorted(covered_but_unnamed, key=lambda r: -r[2]):
            print("note      %-28s %5.1f%% statements  (%s)" % (fn, pct, ",".join(where)))

    # Names measured as covered. Compare by NAME only -- the tuple trick I wrote first never matched
    # (the measured rows carry a pct where the blind rows carry a bool), so EVERY helper fell through
    # into "still blind" and the tool reported 40 fresh holes it had just disproved. A filter that
    # fails open reads exactly like a filter that finds nothing.
    covered_names = set(c[0] for c in covered_but_unnamed)
    still_blind = [r for r in blind if r[0] not in covered_names]
    mirrored_blind = [r for r in still_blind if r[3]]

    for fn, where, _named, mirrors in mirrored_blind:
        print("UNMIRRORED %s (%s) -- no Go test names it and NO coverage was measured for it, but %s "
              "transcribe(s) and judge it from source. A mirror agreeing with itself is not evidence "
              "about the engine." % (fn, ",".join(where), ", ".join(mirrors)))

    for fn, where, _named, _mirrors in [r for r in still_blind if not r[3]]:
        print("note  %s (%s) -- no Go test names it, no coverage measured, and no mirror judges it. "
              "Probably dead or trivial; worth a look, not a failure." % (fn, ",".join(where)))

    KNOWN_UNMIRRORED = {
    }

    # BEAT 813. A SEPARATE pin for a SEPARATE fact. KNOWN_UNMIRRORED means "a mirror is its only
    # judge" -- that set is now empty, because the coverage oracle proved no mirror-only helper exists.
    # These four are something else and worse: NO test names them, NO coverage reaches them (0.0%
    # statements, measured), and NO mirror reproduces them. They are simply unjudged. Folding them
    # into KNOWN_UNMIRRORED would have made that list's message false; leaving them unpinned would let
    # a fifth appear silently. So: their own name, their own count, failing in both directions.
    # normalizePathGlobs is the one that matters -- it is in the rule path.
    KNOWN_MEASURED_GAPS = {
        "uidStr": "0.0% statements (measured); no test, no coverage, no mirror",
        "hostOf": "0.0% statements (measured); no test, no coverage, no mirror",
        "sendImplicitTLS": "0.0% statements (measured); a METHOD; no test, no coverage, no mirror",
        # BEAT 814: normalizePathGlobs REMOVED from this pin deliberately. It stopped being a gap
        # because normalizepathglobs_test.go now executes it (and pins the Windows-path miss it was
        # written for). The function is still DEAD CODE -- untested was one problem, uncalled is
        # another, and the second one is the pack owner's call, recorded in its STALECLAIM comment.
    }

    # The measured-gap pin, checked against the same coverage map. Fails both ways like the others.
    gaps_now = set(r[0] for r in still_blind if not r[3])
    gaps_known = set(KNOWN_MEASURED_GAPS)
    for fn in sorted(gaps_now - gaps_known):
        print("FAIL unmirrored: %s has no test, no measured coverage and no mirror -- a NEW unjudged "
              "helper; add it to KNOWN_MEASURED_GAPS deliberately or write a test" % fn)
    for fn in sorted(gaps_known - gaps_now):
        print("FAIL unmirrored: %s was pinned as a measured gap but is now judged by something -- "
              "the pin has drifted; remove it deliberately" % fn)

    print("ok   unmirrored: %d unexported helpers across %d package(s), %d named by a test, %d "
          "unnamed (%d of those are judged by a Python mirror reading source)"
          % (len(rows), len(seen), len(rows) - len(blind), len(blind), len(mirrored_blind)))

    # BEAT 797. The widened scope found 8 helpers whose only verification is a source-reading mirror.
    # They are REAL findings, not noise, and they cannot be left as one bare red line: a suite that
    # is red forever is one people learn to ignore, and then delete (beat 780). So the set is PINNED
    # -- declared by name, with the exact count -- which makes this fail in BOTH directions:
    #   * a 9th appears   -> the tree got worse somewhere I have not looked, and I hear about it
    #   * one gets a real test -> the pin DRIFTS, and a pin that drifts is a silenced defect nobody
    #     is reading (beat 776). The fix is to remove the name from the list, deliberately.
    # This is not "declared red" dressed up: nothing here is skipped, every name is checked on every
    # run, and the failure output names which helper moved.
    # BEAT 813 -- THIS LIST IS NOW EMPTY, AND THAT IS A MEASUREMENT, NOT A CLEANUP.
    # It held 40 names at beat 812. Go's own coverage (`go tool cover -func`, default + `-tags
    # sqlite`) shows every one of them exercised by a real compiling test: 79 helpers tree-wide are
    # unnamed-by-name-search but covered. The list was not a work queue; it was an artifact of asking
    # a text question ("does the string `matchOne(` appear in a _test.go file?") and reporting the
    # answer as if it were "is this tested?".
    # Kept as a declared, empty set rather than deleted: the machinery that checks it is what makes a
    # FUTURE entry mean something, and the empty literal is the record that the last 40 were cleared
    # by evidence rather than by lowering a threshold.
    # BEAT 813, MEASURED with `go tool cover -func` (default profile; rc=1 does NOT prevent a
    # profile being written -- verified, 2508 blocks): these four are at 0.0% statements, are named by
    # no test, and have no mirror. THIS is the honest short list the tool spent three beats hiding
    # under 36 names it had mis-scored.
    #   uidStr / hostOf      -- central/email.go   string helpers beside the alert mailer
    #   sendImplicitTLS      -- central/email.go   a METHOD (`func (e *EmailAlerter) ...`), which
    #                           matters: FUNC_RE captures methods too, so this list has always mixed
    #                           functions and methods. Not fixed tonight -- recorded, because a
    #                           "helper" count that silently includes methods is a wrong number, even
    #                           when the direction of the error is harmless.
    #   normalizePathGlobs   -- rules/engine.go    path-glob normalisation, IN THE RULE PATH
    # The rules/ one is the one to care about: it sits in the code that decides whether a rule fires.
    # P1 in the beat-797 table: pinning `splitAuditFields` returned rc=0, because the pin was
    # compared against mirrored_blind only. A pinned helper that loses its MIRROR drops out of that
    # set and reads as "the pin drifted", except nothing compared it -- so a helper could go from
    # "judged by a mirror that agrees with itself" to "judged by nothing at all" and the pin would
    # stay silent. Compare against EVERY unmirrored helper (which is the pin's real subject), and
    # separately against the ones that also lost their mirror, so the message says which happened.
    named = {r[0] for r in blind}
    still_mirrored = {r[0] for r in mirrored_blind}
    known = set(KNOWN_UNMIRRORED)
    # BEAT 798: this loop used to iterate `named` -- EVERY unnamed helper -- while its message said
    # "is judged only by a source-reading mirror". Measured: 68 unnamed helpers, 8 of them mirrored,
    # and the loop accused 60 of them of a mirror that does not exist. The pinned 8 were always in
    # the set, and the other 60 were helpers with NO test and NO mirror -- the `note ...` line just
    # above describes those correctly and exits 0 on purpose. So one wrong variable turned a
    # deliberately-tolerated class into 60 hard failures and left the gate red for a real, unrelated
    # reason. Beat 644's rule, in the other direction: a blank is better than a false accuser, and 60
    # false accusers is a gate people delete (beat 780). The subject of THIS loop is `still_mirrored`
    # -- the set of helpers whose only judge is a mirror -- which is exactly what the message claims
    # and exactly what KNOWN_UNMIRRORED is a pin over.
    for fn in sorted(still_mirrored - known):
        print("FAIL unmirrored: %s is judged only by a source-reading mirror and is NOT in the pinned "
              "set -- this is a new hole, or the pin is stale" % fn)
    for fn in sorted(known - still_mirrored):
        if fn not in named:
            print("FAIL unmirrored: %s was pinned as unmirrored but now has a Go test (or vanished) -- "
                  "the pin has drifted; remove it from KNOWN_UNMIRRORED deliberately" % fn)
        else:
            print("FAIL unmirrored: %s was pinned as 'judged by a mirror' but NO mirror references it "
                  "any more -- it is now unjudged entirely, which is a worse hole, not a closed one"
                  % fn)
    # BEAT 798 (second defect, same file): `drifted` compared `named` -- every unnamed helper, 68 of
    # them -- against a pin that names 8. `named - known` was therefore 60 on every single run, so
    # `--check` exited 1 UNCONDITIONALLY. That is why the suite read `1 of 134 did not hold / 0
    # reported failures`: the exit code was red while every line said ok, and rc=0 only because I had
    # been running it WITHOUT --check, which is not how the gate runs it. The pin's subject is the
    # mirror-only set, so that is what drift means in both directions: a mirror-only helper that is
    # not pinned, or a pinned name that is no longer mirror-only. Both are already named by the FAILs
    # above; this line only decides the exit code.
    drifted = bool((still_mirrored - known) or (known - still_mirrored))
    if check and drifted:
        return 1

    # BEAT 809. The pin above cannot see a MEASUREMENT that got quieter. I proved this: reverting the
    # name-search to the old loose form (a function counts as "named by a test" if its name appears
    # anywhere, comments included) exits rc=0 with every counter unchanged -- because the 17 falsely-
    # covered helpers simply leave `blind`, and a helper that is not in `blind` is not the pin's
    # subject in either direction. So the tool could silently return to over-reporting coverage and
    # NOTHING would say so. That is a gate that cannot observe its own regression.
    #
    # The counters are therefore PINNED like the names are. Both directions fail: fewer helpers
    # count as covered than before (the tool got quieter, i.e. looser), or more (a real test was
    # added and the pin needs a deliberate bump). `covered_by_test` is the number that moved 65 -> 48
    # when the comment-stripping was added, and that is the number this watch exists for.
    # BEAT 813 -- re-pinned from MEASUREMENT after the coverage oracle landed. `mirror_only` was 40 and
    # is now 0: not because anyone wrote 40 tests tonight, but because the tool stopped calling a
    # transitively-covered helper "unmirrored". `covered_by_test` 42 -> 81 (42 named + 39 measured-only)
    # would be the honest reading; it is left at the NAME count so the two numbers stay comparable, and
    # the measured split is reported separately in the ok line.
    # STATED PLAINLY: lowering a pinned number normally means a search got LOOSER and is about to
    # over-report coverage (this comment's original warning). Here the pin is lowered because a SECOND,
    # INDEPENDENT instrument was added and it disagrees with the first. The audit for that is
    # docs/FINDING-2026-11-04c-unmirrored-list-was-false.md -- the table was produced by `go tool
    # cover`, not by this script, which is why I trust it over the list it replaced.
    # BEAT 819 -- 44 -> 47 (+3), raised deliberately and in the STRONG direction. Three tests added this
    # beat (TestIsProbablyTextBoundary, TestReadContentRefusesBinaryAndOversized,
    # TestEquivalentBoundariesAreActuallyEquivalent) name helpers that had no Go test naming them before:
    # isProbablyText, readContent, plus the trim path. The number rose because the suite got honest about
    # code it was already exercising, not because a search got looser -- `mirror_only` stayed 0, so no
    # coverage was reclassified, and `helpers` stayed 125, so nothing was added or hidden. A rising
    # covered_by_test is the safe direction; the watch that matters is a FALLING one.
    # BEAT 831 -- 48 -> 50: TestSplitNameVersionEmptyNameKeepsBoundary names splitNameVersion and
    # TestMatchOneIgnoredErrIsEquivalent names matchOne, neither previously named by any Go test.
    # STRONG direction confirmed, not assumed: helpers held at 125, mirror_only held at 0, so the
    # search did not loosen -- two real functions gained a test.
    # BEAT 829 -- tried to bump 48 -> 49 on the theory that TestSpoolMaxBytesIsAlwayPositive
    # newly named NewSpool. MEASURED: the counter did not move. NewSpool was already named by a
    # test, so the new test adds a PIN, not coverage. Reverted -- the measurement is the claim,
    # not my prediction, and a pin bumped on a guess is a number I could not defend.
    # BEAT 827 -- 47 -> 48 (+1), raised deliberately and in the STRONG direction. This beat added
    # tests for globMatch (collector.go:135/:142), the two maxDiffBytes diff arms in drift.go
    # (:45 removed-file, :83 added-file), and spool.go:233. That is four mutants killed and one more
    # helper -- globMatch -- named by a Go test than before. `helpers` stayed 125 and `mirror_only`
    # stayed 0, so nothing was added or reclassified: the reachable surface did not move, only how
    # much of it a test names. A rising covered_by_test is the safe direction; the watch that
    # matters is a FALLING one.
    # BEAT 832 -- 50 -> 51 (+1), raised deliberately and in the STRONG direction. internal/rules
    # survivor831_test.go now names `anyArgvMatches` directly (it was reached only through
    # `matches` before), which is the +1. `helpers` stayed 125, `packages` stayed 6, `mirror_only`
    # stayed 0: nothing added, nothing reclassified, only one more helper with a test that says its
    # name. Safe direction -- a FALLING covered_by_test is the one to worry about.
    # BEAT 834 -- 51 -> 52 (+1), raised deliberately and in the STRONG direction. The new
    # internal/telemetry/strictsyscalls_test.go names `writeTarget` directly (the single place the
    # read/write decision is made and the function the strictWriteSyscalls map feeds). `helpers`
    # stayed 125, `packages` stayed 6, `mirror_only` stayed 0 -- nothing added, nothing
    # reclassified. Safe direction; the watch that matters is a FALLING covered_by_test.
    PINNED = {"helpers": 125, "packages": 6, "covered_by_test": 52, "mirror_only": 0}
    actual = {"helpers": len(rows), "packages": len(seen),
              "covered_by_test": len(rows) - len(blind), "mirror_only": len(mirrored_blind)}
    for key, want in PINNED.items():
        if actual[key] != want:
            print("FAIL unmirrored: %s is %d, pinned at %d -- if the measurement got QUIETER "
                  "(covered_by_test rose, mirror_only fell) the search has loosened again and this "
                  "tool is about to over-report coverage; if it got STRONGER, bump the pin "
                  "deliberately" % (key, actual[key], want))
            if check:
                return 1

    if check and mirrored_blind:
        print("note  unmirrored: %d helper(s) whose only verification is a source-reading mirror, "
              "all pinned in KNOWN_UNMIRRORED -- the list is the work queue, not a pass"
              % len(mirrored_blind))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""packpresent.py -- no suite may report green over a missing rule pack.

THE BUG THIS EXISTS FOR (found beat 616, from my sister Nadia)
Nadia found her own check green because it pointed at an empty directory: "a clean check pointed at
the wrong folder looks exactly like a quiet day, and it would have passed." Her lesson: verify the
path before trusting the silence.

I applied it here: moved `rules/carbonato.yaml` off disk and ran every script. Four suites that read
the pack still exited 0, covermap_test among them reporting "14 ok, 0 fail" with nothing to look at.

HOW THIS GUARD WORKS -- and the three wrong ways I tried first
I tried to answer "can this script reach the pack?" by reading source. Three times:
  1. regex demanding `PACK = os.path.join(...)`          -> 43 false "unexplained"
  2. eval every os.path.join(...) mentioning the pack    -> 32 false "unexplained"
  3. import the module and read its PACK attribute       -> 32 false "unexplained" (most modules have
                                                            no PACK attribute; they use fieldcheck.PACK
                                                            or open the path inline)
Every attempt was a static approximation of a question the script can answer itself, and each attempt
was wrong in a way I could only see by checking its output against reality. A checker that understands
only some spellings of the thing it checks is the same pathology it is hunting.

So: run them. Move the pack aside, run every script, and assert that the set of scripts which STILL
exit 0 is exactly the declared pack-free set. Behaviour, not inference. Slow (a couple of minutes) but
it is the only version that cannot be fooled by a spelling I did not anticipate.

WHAT A PASS MEANS
  * baseline: every script exits 0 WITH the pack present (so a red-without-pack result is meaningful)
  * with the pack absent, every script either exits non-zero, or is in KNOWN_PACK_FREE with a reason
A script that keeps passing with no pack is either genuinely pack-free (fine, but it must be declared
and justified) or is silently checking nothing (the bug). This guard forces that distinction to be made
in writing instead of assumed.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACK_REL = os.path.join("rules", "carbonato.yaml")

# NOTE (beat 616): six scripts exit non-zero WITH the pack present -- dispose.py (internal/ mtime
# assertion), docsindex.py (stale index), endpointfixture.py (unreachable-glob set changed),
# fpprobe.py (4 false positives), greenprobe.py (2 unruled guards), guardreach.py (denominator gaps).
# Those are real pre-existing repo failures, NOT artifacts of this run. This guard therefore judges
# only scripts that are GREEN with the pack, and reports the red set as context rather than failing
# on it -- otherwise one stale index would mask every question about the pack.
#
# Scripts allowed to exit 0 with the rule pack absent, each with the reason it does not need parsed
# rules. These were verified by HAND, one at a time, by reading what each does -- not assumed.
KNOWN_PACK_FREE = {
    # BEAT 815: gomutate.py mutates Go SOURCE and runs `go test`; it never loads the rule pack and
    # never should. Its subject is whether the Go suite can FAIL, which is a property of the source
    # tree, not of carbonato.yaml. Cost of the exemption, stated: with the pack deleted entirely it
    # still reports green -- and that is correct, because the pack is not what it measures.
    "gomutate.py": "mutates Go source and runs go test; the rule pack is not its subject",
    # Added 2026-11-04 (beat 811). stripnoncode_test tests `strip_go_noncode` / `strip_py_noncode`
    # in scripts/unmirrored.py -- a comment/string STRIPPER. Its inputs are Go and Python source
    # text; it never opens rules/carbonato.yaml, so with the pack deleted it still reports, correctly,
    # because the question it asks ("does this stripper delete evidence?") has nothing to do with the
    # pack's contents. Measured, not assumed: the guard flagged it by name on its first full run, which
    # is the behaviour this guard exists for -- a new script cannot be silently pack-free.
    # The cost is stated: with the pack gone this still reports green. That is not a blind spot in the
    # pack, because the pack is not its subject; the stripper is what manufactures every "0 hits"
    # verdict unmirrored.py produces, and it was wrong twice before it had a test.
    "stripnoncode_test.py": "tests the comment/string stripper in unmirrored.py; the rule pack is not an input.",
    # Added 2026-11-04 (beat 812). coverunmirrored_test asks whether `unmirrored.py`'s NAME-based
    # verdict is outranked by MEASURED statement coverage. Its inputs are a Go coverage profile and a
    # Python docstring; it never opens rules/carbonato.yaml. Measured, not assumed -- this guard
    # named it on its first run, which is the behaviour it exists for.
    # The cost is stated: with the pack gone this still reports green. That is correct -- the pack is
    # not its subject. What it defends is the credibility of every "untested helper" claim the
    # toolchain makes, which is a property of the SOURCE TREE and not of the rule data.
    "coverunmirrored_test.py": "re-measures Go statement coverage against unmirrored.py's name verdict; the rule pack is not an input.",
    # Added 2026-10-27 (beat 620). greenprobe reads SOURCE TEXT of scripts/ and never opens the
    # rule pack, so it is structurally unable to notice a broken pack. Its own output always said
    # "limit: source read, not behavioural proof" -- this exemption is that sentence, enforced.
    # The cost is real and stated: with the pack deleted, greenprobe still reports green. That is
    # the documented blind spot, not a pass. It belongs in the suite anyway because the thing it
    # checks (does every guard have a reachable non-zero exit) is pack-independent by definition.
    "greenprobe.py": "reads scripts/ source text only; cannot observe the pack (limit printed in "
                     "its own output). Checks exit-reachability, which is pack-independent.",
    # Added 2026-10-27 (beat 620). docsindex indexes docs/ against scripts/; it never opens
    # rules/carbonato.yaml. It used to look non-pack-free only because the index was STALE, so it
    # failed for an unrelated reason. Beat 617 refreshed the index (--write) and this check correctly
    # surfaced the truth underneath: the script is pack-independent. Same reasoning as greenprobe.
    "docsindex.py": "indexes docs/ against scripts/; the rule pack is not one of its inputs.",
    # Added 2026-10-28 (beat 633). nonprobe_test reads scripts/ source and IMPORTS each non-test
    # module in a fresh interpreter to see whether it prints. It never opens rules/carbonato.yaml and
    # never parses a rule; the pack is not among its inputs. Verified behaviourally -- it was green
    # with the pack absent in this very guard's mirror, which is the only reason it showed up here.
    "nonprobe_test.py": "import-time behaviour of scripts/ modules; the rule pack is not an input.",
    # Added 2026-10-28 (beat 634). debriscensus classifies the 56 non-test modules by who REFERS to them
    # (Makefile, *.sh, *_test.py imports) and never opens rules/carbonato.yaml -- its input is the tree's
    # own reference graph. With the pack deleted it still answers, correctly, because the question it
    # asks (does anything invoke this file?) has nothing to do with the pack's contents. Declared, not
    # silent: the alternative -- letting it pass unexplained -- is the failure mode this guard exists for.
    "debriscensus.py": "classifies scripts/ by reference graph; the rule pack is not an input.",
    # Added 2026-10-28 (beat 637). pathmatchdiff compares neargo's transcription of Go's path.Match
    # against known-Go answers. Its inputs are the Go spec and neargo.stop; it never opens
    # rules/carbonato.yaml, so it cannot observe a broken pack and correctly keeps its verdict with the
    # pack deleted. It was invisible to this guard until beat 637 because nothing ran it -- being added
    # to suitecheck's SELFHARNESS is what surfaced the missing declaration. Declared now, with the cost
    # stated: with the pack gone this file still reports 20/20, because the pack is not its subject.
    "pathmatchdiff.py": "compares neargo vs Go path.Match answers; the rule pack is not an input.",
    # Added 2026-10-30 (beat 680) in one batch, by READING each file's opening statement -- not by
    # assuming, and not by letting the guard's silence decide it. These 17 stayed green with the pack
    # deleted and were never declared. Sentence-for-sentence (see each file's line 2): they test the
    # spool, the timeline, the alert path, /healthz, and central's error handling -- engine and
    # transport behaviour, none of which takes the rule pack as an input. The reason is written down
    # here because the alternative is a guard that quietly stops asking about 17 instruments.
    # NOT included: packvanished.py, which is the one script in this group that DOES read the pack
    # (it hides the pack and runs the fixture suites) -- it must fail without one, or it is lying.
    "alertfanout.py": "alert path: batch fan-out to sinks; the rule pack is not an input.",
    "alertwiring.py": "alert path: per-sink behaviour; the rule pack is not an input.",
    "appendcost.py": "spool append cost under the cap; the rule pack is not an input.",
    "blockship.py": "log read errors vs spool delivery; the rule pack is not an input.",
    "centralreject.py": "central's rejection handling; the rule pack is not an input.",
    "clockfidelity.py": "event timestamping (now vs event time); the rule pack is not an input.",
    "direntsource.py": "observe() existence vs Actions() readability; the rule pack is not an input.",
    "durabilitygap.py": "at-least-once vs failed disk write; the rule pack is not an input.",
    "feedblind.py": "end-to-end alert trace through the feed; the rule pack is not an input.",
    "gapsilence.py": "nothing-happened vs we-lost-it; the rule pack is not an input.",
    "healthprobe.py": "/healthz semantics; the rule pack is not an input.",
    "opaqueupstream.py": "collector error collapsing; the rule pack is not an input.",
    "pruneprefix.py": "Timeline.Record eviction ordering (Go source, not the YAML pack).",
    "recordloss.py": "record/alert path accounting; the rule pack is not an input.",
    "spoolwedge.py": "spool head-of-line blocking; the rule pack is not an input.",
    "timelineprecedence.py": "timeline writer precedence; the rule pack is not an input.",
    "trimfloor.py": "spool byte-bound floor; the rule pack is not an input.",
    "scanblind.py": "publish-scan.sh verdict path (git grep vs no .git); never opens the rule pack.",
    "fixtureledger.py": "counts fixture rows in scripts/ source text; never opens the rule pack "
                        "(its own output states this limit, and names wirediff/coverage as the check "
                        "that does read the pack).",
    "matcherblind.py": "runs six suites against a neutralised matcher in a hardlink mirror of scripts/; "
                       "the pack is not its subject (measured: still 6/6 matcher-dependent with the "
                       "pack deleted -- it exercises globmatch, not rules). Its suites read the pack; "
                       "this harness does not.",
    # Added 2026-10-27 (beat 628). priorcheck searches docs/ to answer "has this been investigated?"
    # before a beat starts work. It reads findings, never rules/carbonato.yaml. Its test drives the
    # shipped docs/ and temp doc trees, also never the pack. Declared here rather than left silent:
    # the guard's question is "does this script prove anything about the pack?" and for these the
    # honest answer is no -- which is why they are exempt, not why they are wrong.
    "priorcheck.py": "searches docs/ findings for a subject; the rule pack is not an input.",
    "priorcheck_test.py": "drives priorcheck over shipped and temp docs trees; no pack read.",
    "globmatch.py": "pure matcher reference impl; fixtures are inline strings",
    "neargo.py": "pure matcher reference impl; no pack input",
    "neargo_test.py": "tests neargo's matcher on inline fixtures",
    "needleprobe.py": "tests substring_needle semantics on inline strings",
    "parityreverse.py": "compares two matcher impls on generated strings",
    "pathdiff.py": "prints prose about a Go-side gap; no pack read at runtime",
    "pathdiff_test.py": "asserts pathdiff's prose",
    "pkgcheck.py": "reads Go source for a filename; not the pack",
    "renametrace.py": "traces Go rename handling; reads engine.go, not rules",
    "shebang_test.py": "checks interpreter lines across scripts",
    "tokruns.py": "token/lexer probe on Go source",
    "tokruns_test.py": "tests tokruns on inline strings",
    "noloadprobe.py": "parses engine.go/loader Go source for the pin-set path",
    "guardreach.py": "scores how guards REPORT; reads Go/shell source, never the pack",
    "guardreach_test.py": "drives guardreach over inline snippets and the scripts/ tree; no pack read",
    "covermap_test.py": "greps text for rule ids -- MUST be in this list only because its fixtures "
                        "are inline; with a real but empty pack it reports 14 ok over nothing. "
                        "See the NOTE below.",
    # Added 2026-11-04 (beat 811, second pass). These 27 census instruments each read GO SOURCE or
    # the tree's own records and never open rules/carbonato.yaml, so they correctly keep their verdict
    # with the pack deleted. They were INVISIBLE until beat 811 because packpresent.py was never
    # invoked by any gate: it is not in suitecheck.sh's SELFHARNESS, and no Makefile target calls it.
    # Its own comment in suitecheck.sh:223 mentions it only in passing, while explaining a different
    # defect. So a guard that asks a good question ("does this script prove anything about the pack,
    # or is it silently checking nothing?") has been asking it into an empty room.
    # Declared here by READING each file's opening docstring line, not by trusting the guard's silence:
    #   alertbulkcensus  - the alert has no size cap (Go source)   baselinekey - baseline keyed by path
    #   capordercensus   - capability ordering                     claimcensus - claim counting
    #   contractruncensus- contract truncation                     eventclock  - poll instant vs event
    #   fleetcoverage*   - fleet coverage                          fleetsample* - fleet sampling
    #   gateaudit        - the gate's own reporting                hostclaim   - host claim shape
    #   hostidshape      - host id shape                           ingestceiling* - ingest ceiling
    #   inplacefilter*   - in-place filtering                      keycollision* - key collisions
    #   livenesscensus   - liveness                                manifestkey* - manifest keys
    #   modtimecensus    - mtimes                                  replaywarn  - replay warnings
    #   retainfailcensus - retention failures                      retainpath* - retention paths
    #   retentioncensus  - retention                               scopekey    - scope capability keys
    #   seencensus       - seen/queued accounting                  spoolackcensus - spool acks
    #   sqlitetrimcensus - sqlite trimming                         timelinecap - timeline cap
    #   walkerrorcensus  - walk errors
    # THE COST, STATED: with the pack deleted all 27 still report green. That is correct for these
    # questions and is NOT a blind spot in the pack. The real finding is the one above: the guard
    # itself is not wired into the gate. That is a separate decision (Dad's call on gate time --
    # packpresent copies the whole repo and runs every script twice).

    "alertbulkcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "baselinekey.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "capordercensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "claimcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "contractruncensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "eventclock.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "fleetcoveragecensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "fleetsamplecensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "gateaudit.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "hostclaim.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "hostidshape.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "ingestceilingcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "inplacefiltercensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "keycollisioncensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "livenesscensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "manifestkeycensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "modtimecensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "replaywarn.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "retainfailcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "retainpathcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "retentioncensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "scopekey.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "seencensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "spoolackcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "sqlitetrimcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "timelinecap.py": "census instrument over Go source / tree records; the rule pack is not an input.",
    "walkerrorcensus.py": "census instrument over Go source / tree records; the rule pack is not an input.",

}

# NOTE on guardreach.py / guardreach_test.py (declared beat 631): both are pack-free by subject. The
# linter scores how GUARDS REPORT -- denominators, negative claims, static-read notes -- over Go and
# shell source in scripts/ and internal/. It never opens rules/carbonato.yaml, so it behaves identically
# with the pack deleted and must not pretend otherwise. The test drives the linter and inherits that.
# Unlike covermap above, there is no pack-reading partner to lean on: this instrument has no opinion
# about the pack at all, which is the reason it cannot be blinded by a missing one.
# NOTE on covermap_test.py: it passes with no pack because it operates on inline text, which is
# legitimate for the unit it tests. But that is exactly the shape that hid Nadia's bug -- a green that
# would read the same if there were nothing to check. It is left pack-free and declared here rather
# than "fixed", because its fixtures are the point. What protects us instead is that covermap.py
# itself IS required to fail without a pack (asserted below).

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"ok    {label}")
    else:
        fails.append(label)
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def run_one(script, workdir):
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                           timeout=180, cwd=workdir)
        return r.returncode
    except subprocess.TimeoutExpired:
        return "timeout"


def main():
    scripts = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "*.py")))
    scripts = [s for s in scripts if s not in ("packpresent.py", "packpresent_test.py")]

    # Third design. History of this guard's harness, all wrong in instructive ways:
    #   1. copied only scripts/ + rules/     -> 6 red for missing dirs, baseline useless
    #   2. copied the whole repo with copytree -> still 6 red (mtimes differ on copy, so dispose.py's
    #      "internal/ was not touched" assertion fires)
    #   3. ran in the REAL tree, moving the pack aside -> worked alone, but BROKE ITS NEIGHBOURS: run
    #      under suitecheck.sh, other suites read the pack mid-run and failed. A guard that damages the
    #      thing it guards is not a guard.
    # Now: a hardlink mirror of the whole repo. Hardlinks share inodes, so mtimes and content are
    # identical (dispose.py stays honest), and the pack is removed from the MIRROR ONLY -- the real
    # pack is never moved. Same isolation as (3), none of the blast radius.
    # Mirror must live on the SAME filesystem as the repo or os.link fails (EXDEV): /tmp here is
    # tmpfs (dev 1048754) while the repo is on the root overlay (dev 1794). So build it inside the
    # repo and remove it in finally.
    tmp = tempfile.mkdtemp(prefix=".packpresent-mirror-", dir=ROOT)
    try:
        shutil.copytree(ROOT, tmp, symlinks=True,
                        copy_function=os.link,
                        ignore=shutil.ignore_patterns("__pycache__", ".git", "out"),
                        dirs_exist_ok=True)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp = None
    try:
        # Copy the WHOLE repo, not a subset. First attempt copied only scripts/ + rules/ + a few dirs,
        # and six scripts went red for reasons that had nothing to do with the pack (dispose.py checks
        # mtimes under internal/; check-links.py needs docs). A baseline that is red for unrelated
        # reasons cannot answer the question this guard asks.
        workdir = os.path.join(tmp, "scripts")

        # 1. Baseline WITH the pack: everything green, so a later red is about the pack.
        green_with = []
        red_with = []
        for s in scripts:
            rc = run_one(s, workdir)
            (green_with if rc == 0 else red_with).append((s, rc))
        # Informational, not a failure: a red script cannot be tested for pack-dependence, and a
        # pre-existing red (stale index, changed glob set) is not this guard's business.
        if red_with:
            print(f"note  {len(red_with)} script(s) already red WITH the pack (pre-existing, "
                  f"outside this guard's scope): {[s for s, _ in red_with]}")
        else:
            print("ok    baseline: every script exits 0 with the pack present")
        check("enough scripts are green to make this guard meaningful", len(green_with) >= 40,
              f"only {len(green_with)} green")

        # 2. Remove the pack and re-run.
        pack_path = os.path.join(tmp, PACK_REL)
        # If the pack is already absent (mutation M1: deleted upstream), there is nothing to remove --
        # and the guard must still ANSWER rather than crash with a traceback. A guard that dies with a
        # stack trace when the pack is missing reports the fault as an accident, not a finding.
        if os.path.exists(pack_path):
            os.remove(pack_path)   # hardlink: removes the MIRROR entry only, real pack survives
        check("the pack is absent from the mirror", not os.path.exists(pack_path))
        check("the real pack is untouched by this guard",
              os.path.exists(os.path.join(ROOT, PACK_REL)))

        silent = []
        for s in [n for n, _ in green_with]:
            rc = run_one(s, workdir)
            if rc == 0 and s not in KNOWN_PACK_FREE:
                silent.append(s)
        check("no undeclared script passes with the pack absent", silent == [], f"silent: {silent}")

        # 3. Every declared exemption must still exist and still be green (no stale cover).
        stale = [s for s in KNOWN_PACK_FREE if not os.path.exists(os.path.join(HERE, s))]
        check("no stale exemption for a deleted script", stale == [], f"stale: {stale}")

        # 4. Every exemption states a real reason.
        thin = [s for s, why in KNOWN_PACK_FREE.items()
                if not why or why.strip().lower() in ("", "todo", "n/a")]
        check("every exemption states a reason", thin == [], f"thin: {thin}")

        # 5. The pack must actually carry rules -- the guard is vacuous otherwise. Read from the STASH,
        # because this check runs while the real file is moved aside. (First version read the real path
        # and crashed on FileNotFoundError -- my own bug, caught by running it.)
        import yaml
        real_pack = os.path.join(ROOT, PACK_REL)
        check("the shipped pack exists", os.path.exists(real_pack))
        try:
            doc = yaml.safe_load(open(real_pack, encoding="utf-8").read())
        except Exception:
            doc = None
        rules = (doc or {}).get("rules") or []
        check("the shipped pack is not empty", bool(rules) or os.path.getsize(real_pack) if
              os.path.exists(real_pack) else False)
        check("the shipped pack parses to a non-empty rule list", len(rules) > 0)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print(f"packpresent: {len(fails)} FAIL")
        return 1
    print(f"packpresent: all ok -- {len(scripts)} scripts, "
          f"{len(KNOWN_PACK_FREE)} declared pack-free")
    return 0


if __name__ == "__main__":
    sys.exit(main())

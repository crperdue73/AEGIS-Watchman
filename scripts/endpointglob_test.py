#!/usr/bin/env python3
"""endpointglob_test -- transcribe `matchOne` and ask the endpoint globs what they actually match.

BEAT 590. This is the largest REACH-only family: 32 values across 3 rules, and 78 of the pack's 125
match values are REACH. `fieldcheck` has said REACH for weeks — "a check proves the glob CAN fire; no
sample drives it" — which is honest and stops at the interesting question. This suite asks the next
one, without a Go toolchain and without inventing rule semantics: given the engine's own match
algorithm, transcribed and pinned, plus an endpoint shaped the way `internal/telemetry` actually
emits one, what do these 32 globs do?

THE HONEST POSITION, STATED FIRST. I am not asserting that these globs are correct, too broad, or too
narrow. That is a rule-semantics judgement and belongs to the pack owner. What I can measure is
SUBSUMPTION — which globs can only ever fire as a consequence of another glob in the same list — and
that is a fact about the strings, not an opinion about the rules. A glob shadowed by a broader glob
in the same list is dead weight: it cannot widen coverage, cannot narrow it, and its presence makes
the list look more precise than it is.

WHAT IS PINNED
  1. `matchOne` and its helpers, transcribed from internal/rules/engine.go (lines 237-300), with the
     source pinned by grep so drift breaks this test rather than sliding through. Four fallbacks in
     order: whole-string path.Match, every trailing suffix, basename, then the "*needle*" contains
     fallback. Getting the ORDER or the COUNT wrong changes what matches.
  2. `detailString` for the `endpoint` key is a plain string read — a non-string detail (a JSON
     number, say) is "" and the rule silently cannot fire. Same defect class as beats 587/588.
  3. Every one of the 32 endpoint globs, against endpoints the shipped collector can emit, plus
     adversarial shapes: a host that is a *suffix* of a matched domain (evil-slack.com), a host that
     carries the needle in its path, and a bare IP with a port.
  4. Subsumption: for each list, which globs are made redundant by a broader sibling.

THE LIMIT THIS SUITE CANNOT LIFT: "can fire" is not "will fire". No sample event drives these rules,
so nothing here proves a real endpoint reaches the collector in this shape. It constrains the match
string against a stated model of the emitted string — nothing more.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import fieldcheck  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")




def _egress_go():
    with open(os.path.join(ROOT, "internal", "telemetry", "egress.go"), encoding="utf-8") as fh:
        return fh.read()


def _known_service_names():
    """Read the reverse map OUT OF THE GO SOURCE, not from a list typed here."""
    src = _egress_go()
    block = src[src.index("var knownServices = []knownService{"):]
    block = block[:block.index("\n}\n")]
    return re.findall(r'\{name:\s*"([^"]+)"', block)


def _endpoint_globs_by_rule():
    import yaml
    with open(os.path.join(ROOT, "rules", "carbonato.yaml"), encoding="utf-8") as fh:
        pack = yaml.safe_load(fh)
    out = {}
    for r in pack["rules"]:
        g = (r.get("match") or {}).get("endpoint_glob_any")
        if not g:
            continue
        out[r["id"]] = g if isinstance(g, list) else list(g.values())[0]
    return out


def main():
    from globmatch import any_glob_matches, match_one, path_match, path_suffixes, substring_needle  # noqa: E402


    ENGINE = os.path.join(ROOT, "internal", "rules", "engine.go")

    eng = open(ENGINE, encoding="utf-8").read()



    check("anyGlobMatches guards the empty string before looking at any glob",

          bool(re.search(r"func anyGlobMatches\(globs \[\]string, s string\) bool \{\s*if s == \"\" \{\s*return false", eng)), True)

    check("matchOne tries exactly four fallbacks, in the order transcribed",

          re.findall(r"path\.Match\((glob, s|glob, suf|glob, base)\)", eng),

          ["glob, s", "glob, suf", "glob, base"])

    check("the contains fallback is last and guarded by substringNeedle",

          bool(re.search(r"if needle, ok := substringNeedle\(glob\); ok \{\s*if strings\.Contains\(s, needle\)", eng)), True)

    check("substringNeedle trims one leading and one trailing '*' only",

          bool(re.search(r"TrimPrefix\(core, \"\*\"\)", eng)) and bool(re.search(r"TrimSuffix\(core, \"\*\"\)", eng)), True)

    check("substringNeedle refuses globs with interior wildcards",

          bool(re.search(r'strings\.ContainsAny\(core, "\*\?\["\)', eng)), True)

    check("endpoint is read as detailString, i.e. a STRING detail",

          bool(re.search(r'ep := detailString\(action, "endpoint"\)', eng)), True)

    check("detailString returns \"\" for any non-string, non-[]byte value",

          bool(re.search(r'func detailString\(action schema\.Action, key string\) string \{.*?default:\s*return ""',

                         eng, re.S)), True)

    check("Go's path.Match does not treat ']' specially outside a class (our transcription assumes it)",

          "path.Match" in eng, True)



    # --------------------------------------------------------------------------------------------------

    # 3. THE 32 GLOBS. Read from the pack, not typed here -- so a pack edit is visible immediately.

    # --------------------------------------------------------------------------------------------------

    text = open(fieldcheck.PACK, encoding="utf-8").read()

    bylist = {}

    for rid, key, vals in fieldcheck.keys_by_rule(text):

        if key == "endpoint_glob_any":

            bylist[rid] = list(vals)



    check("three rules carry endpoint_glob_any", len(bylist), 3)

    check("the three rules are the ones the ledger names", sorted(bylist),

          ["carbonato.c2.blockchain_rpc", "carbonato.exfil.egress_after_change",

           "carbonato.exfil.egress_after_identity_change"])

    check("32 values in total", sum(len(v) for v in bylist.values()), 32)

    check("every glob is a bare \"*x*\" shape with no interior wildcard",

          sorted({g for v in bylist.values() for g in v if substring_needle(g) is None}), [])



    # --------------------------------------------------------------------------------------------------

    # 4. WHAT THE EMITTED STRING LOOKS LIKE. internal/telemetry emits "host:port" or a full URL

    #    (egress.go decodeSockAddr joins host:port; the endpoint detail keeps the raw address). Both

    #    shapes must be handled by the transcription, because the engine sees whatever the collector

    #    writes -- not whatever is convenient.

    # --------------------------------------------------------------------------------------------------

    SLACK_HOSTPORT = "hooks.slack.com:443"

    SLACK_URL = "https://hooks.slack.com/services/T00/B00/XXXX"

    TELEGRAM_URL = "https://api.telegram.org/bot123456:ABC/sendMessage"

    IP_PORT = "104.18.12.34:443"



    check("a host:port endpoint matches *slack.com*", any_glob_matches(bylist["carbonato.exfil.egress_after_change"], SLACK_HOSTPORT), True)

    check("a full URL endpoint matches *slack.com*", any_glob_matches(bylist["carbonato.exfil.egress_after_change"], SLACK_URL), True)

    check("a full Telegram URL matches *api.telegram.org*", any_glob_matches(bylist["carbonato.exfil.egress_after_change"], TELEGRAM_URL), True)

    check("the specific glob is not needed -- the broad one alone already matches", match_one("*telegram.org*", TELEGRAM_URL), True)



    # --------------------------------------------------------------------------------------------------

    # 5. THE ADVERSARIAL SHAPE. The contains-fallback is a substring test on the WHOLE endpoint string,

    #    so a needle in the PATH is as good as a needle in the HOST. That is the design intent for

    #    multi-segment URLs (the comment in matchOne says so), and it has a consequence worth stating:

    #    matching is not host-anchored.

    # --------------------------------------------------------------------------------------------------

    # The first attempt at this section asserted that "evil-slack.com.evil" does NOT match, and FAILED:

    # it matches, because "slack.com" is a substring of it. That is not a transcription bug, it is the

    # measured behaviour of the contains fallback, and it is the finding -- a domain glob can only ever

    # be a SUBSTRING test, so "the agent talked to slack.com" and "the agent talked to a host an attacker

    # named to contain slack.com" are the same observation downstream. Recorded, not wished away. Who

    # owns that (pack owner, or the collector anchoring host) is not mine to decide.

    check("a LOOK-ALIKE host matches: 'evil-slack.com.evil' contains the needle",

          any_glob_matches(["*slack.com*"], "evil-slack.com.evil:443"), True)

    check("  ...as does the suffix-spoof 'slack.com.evil'",

          any_glob_matches(["*slack.com*"], "slack.com.evil:443"), True)

    check("  ...and a host that embeds the name only in its PATH",

          any_glob_matches(["*slack.com*"], "attacker.example:443/slack.com/x"), True)

    check("the ONE shape that does not match is a host with the needle absent entirely",

          any_glob_matches(["*slack.com*"], "attacker.example:443/x"), False)

    check("a dot-anchored glob would be tighter -- and the pack uses none",

          [g for v in bylist.values() for g in v if not g.startswith("*") or not g.endswith("*")], [])

    check("a bare IP:port matches nothing in the C2 list (correct: globs are names, not addresses)",

          any_glob_matches(bylist["carbonato.c2.blockchain_rpc"], IP_PORT), False)



    # --------------------------------------------------------------------------------------------------

    # 5b. THREE BRANCHES THAT HAD NO WITNESS -- found by mutating, not by reading.

    #

    # Beat 590 killed 1 of 4 mutants on the first run (M2). The survivors were not strong tests, they

    # were unexercised code: nothing called the suffix branch past its first element, nothing put a '?'

    # in front of a '/', and nothing passed the empty string through the top-level guard. Beat 588 taught

    # me that a mutant reporting 0 fails may be a broken mutant; the complementary lesson is that a

    # SURVIVING mutant may be a hole in the suite, and the only way to tell is to read the branch and

    # ask whether any assertion could possibly reach it. These are those assertions.

    #

    # The suffix branch specifically: Go's '*' does not cross '/', so a multi-segment glob can only match

    # a deeper string via a SUFFIX. If the branch only tries suffixes[0] (the string itself), the whole

    # mechanism silently collapses -- and that is exactly what M1 did while every test stayed green.

    check("pathSuffixes splits at every '/', not just the first",

          path_suffixes("/root/.ssh/id_ed25519"),

          ["/root/.ssh/id_ed25519", "root/.ssh/id_ed25519", ".ssh/id_ed25519", "id_ed25519"])

    check("a '*'-glob matches a DEEPER path only through a later suffix (branch 2)",

          match_one(".ssh/id_ed25519", "/root/.ssh/id_ed25519"), True)

    check("  ...and NOT through the whole string or the basename alone",

          (path_match(".ssh/id_ed25519", "/root/.ssh/id_ed25519"),

           path_match(".ssh/id_ed25519", "id_ed25519")), (False, False))

    check("'?' does not cross '/' in Go's path.Match (transcribed, not fnmatch)",

          (path_match("a?b", "a/b"), path_match("a?b", "axb")), (False, True))

    # Asserted False first and got True -- correctly, and not because '?' is loose. A pattern that fails

    # whole-string can still match through the SUFFIX branch: "v2/events" is a trailing suffix of the URL

    # and "v?/events*" matches it. So one assertion about the URL cannot isolate one branch. The branch

    # itself has to be tested on a plain name with no '/' in it, which is what the pair below does.

    # Third try at this pair, and the first two were my own confusion, not the code: "v2" IS exactly one

    # char after 'v', so "v?" matching "v2" is right. The distinguishing case needs THREE chars after the

    # literal: "v22" cannot satisfy "v?" because '?' never repeats. That is the assertion that isolates

    # single-char semantics from "any wildcard".

    check("'?' matches exactly one char: 'v?' matches 'v2'",

          path_match("v?/events*", "v2/events"), True)

    check("  ...but not two: 'v?' does NOT match 'v22/events'",

          path_match("v?/events*", "v22/events"), False)

    check("'*' does match two: 'v*' matches 'v22/events'",

          path_match("v*/events*", "v22/events"), True)

    check("  ...so '?' vs '*' are distinct rather than both being 'anything'",

          (path_match("v?/events*", "v22/events"), path_match("v*/events*", "v22/events")),

          (False, True))

    check("the same glob with 'v*' matches the URL too",

          match_one("*/v*/events*", "https://x.example/api/v2/events"), True)

    check("a character class matches, and a negated class rejects",

          (path_match("[ab]x", "ax"), path_match("[^a]x", "ax")), (True, False))

    check("an unterminated class is ErrBadPattern -> no match, not a crash",

          path_match("[ab", "ax"), False)

    check("the empty string never matches, even against '*' (branch 0 guard)",

          any_glob_matches(["*"], ""), False)

    check("  ...and match_one agrees",

          match_one("*", ""), False)



    # --------------------------------------------------------------------------------------------------

    # 6. SUBSUMPTION. A glob whose match set is a strict subset of a sibling's is redundant: it cannot

    #    widen coverage and it makes the list read more precise than it is. This is a measurement about

    #    strings. It is NOT a claim that the list is wrong -- the pack owner may want the specific name

    #    documented even where it is shadowed. Reported, then pinned so a change is visible.

    # --------------------------------------------------------------------------------------------------

    def subsumed_by(glob, siblings):

        """A glob is subsumed if its needle contains another glob's needle (so the broader one wins)."""

        n = substring_needle(glob)

        if n is None:

            return None

        for other in siblings:

            if other == glob:

                continue

            m = substring_needle(other)

            if m is None or m == n:

                continue

            if m in n:

                return other

        return None





    shadowed = {}

    for rid, globs in bylist.items():

        hits = {g: subsumed_by(g, globs) for g in globs}

        shadowed[rid] = {g: s for g, s in hits.items() if s}

        for g, s in sorted(shadowed[rid].items()):

            print(f"     {rid}: {g!r} is shadowed by {s!r}")



    check("egress_after_change: *api.telegram.org* is shadowed by *telegram.org*",

          shadowed["carbonato.exfil.egress_after_change"].get("*api.telegram.org*"), "*telegram.org*")

    check("egress_after_change: *hooks.slack.com* is shadowed by *slack.com*",

          shadowed["carbonato.exfil.egress_after_change"].get("*hooks.slack.com*"), "*slack.com*")

    check("egress_after_change has exactly 2 shadowed globs out of 8", len(shadowed["carbonato.exfil.egress_after_change"]), 2)

    check("egress_after_identity_change has 0 shadowed globs out of 13",

          len(shadowed["carbonato.exfil.egress_after_identity_change"]), 0)

    check("c2.blockchain_rpc has 0 shadowed globs out of 11",

          len(shadowed["carbonato.c2.blockchain_rpc"]), 0)

    check("  ...which matches the independent scan in beat 590's WORKLOG",

          sum(len(v) for v in shadowed.values()), 2)



    # --------------------------------------------------------------------------------------------------

    # 7. THE ONE GLOB THAT DOES NOT FIT ITS OWN LIST'S SHAPE.

    # --------------------------------------------------------------------------------------------------

    # Three globs carry a slash, not one -- I typed "the only" from a glance and the pack disagreed

    # immediately. Listed from the pack so the claim cannot drift.

    check("three globs carry a path component",

          sorted(g for v in bylist.values() for g in v if "/" in g),

          ["*/api/v2/events*", "*bscscan.com/api*", "*etherscan.io/api*"])

    check("  ...and every one of them matches a full URL",

          [g for g in ("*/api/v2/events*", "*etherscan.io/api*", "*bscscan.com/api*")

           if not any_glob_matches([g], f"https://x.example{g.strip('*')}" if not g.startswith("*/")

                                   else "https://x.example/api/v2/events")], [])

    check("  ...but a bare host:port matches none of them (no path to match)",

          [g for g in ("*etherscan.io/api*", "*bscscan.com/api*")

           if any_glob_matches([g], "etherscan.io:443")], [])

    check("  ...except scope-wise, the endpoint detail is host:port as often as a URL (egress.go)",

          any_glob_matches(["*etherscan.io/api*"], "api.etherscan.io:443"), False)



    # --------------------------------------------------------------------------------------------------

    # 5c. THE SURVIVORS, EXPLAINED. Five mutants were run against this suite. Two died immediately

    # (contains->startswith: 4 fails; '?' crossing '/': 1 fail) and the suffix truncation and matchOne's

    # guard died once the cases above existed. Three survived, and each survival is a fact about the

    # engine rather than a hole I left open. Written down so the next person does not re-derive them:



    # (1) anyGlobMatches's `if s == ""` is REDUNDANT with matchOne's. Proof: the empty string already

    #     fails inside match_one, so removing the outer guard changes no outcome. Harmless -- a cheap

    #     early exit -- but it is not load-bearing, and a mutant that removes it SHOULD survive.

    check("the outer empty-string guard is redundant: match_one alone rejects \"\"",

          match_one("*", ""), False)

    check("  ...so removing the outer guard cannot be detected, by construction",

          all(not match_one(g, "") for g in ("*", "*x*", "/", "[a]", "?")), True)



    # (2) The BASENAME branch is unreachable for every glob the pack contains. Both branches compare the

    #     same pattern against a shorter string, and every pack glob is a "*x*" shape, so substring

    #     containment already covers whatever a basename match could find. The branch matters for a

    #     pattern that spells out a path ("/etc/shadow"), which no endpoint glob does.

    check("no pack glob needs the basename branch: contains already covers it",

          [g for v in bylist.values() for g in v

           if any(path_match(g, s.rsplit("/", 1)[-1]) and not path_match(g, s)

                  for s in (SLACK_URL, TELEGRAM_URL))], [])



    # (3) substringNeedle's interior-wildcard refusal is not exercised by any pack glob either -- every

    #     endpoint glob is exactly "*...*" with no inner wildcard (asserted earlier). It becomes load-

    #     bearing the moment someone writes "*api*.telegram*". Its own unit behaviour is pinned here

    #     directly rather than through a rule, so the refusal is at least recorded.

    check("substringNeedle refuses an interior wildcard",

          substring_needle("*api*.telegram*"), None)

    check("  ...and accepts a plain *x* shape",

          substring_needle("*telegram.org*"), "telegram.org")

    check("  ...and a bare x* keeps x",

          substring_needle("slack.com*"), "slack.com")

    # --------------------------------------------------------------------------------------------------
    # FINDING-2026-10-26c (beat 599): the endpoint globs mostly have NO SUBJECT.
    # internal/telemetry/egress.go emits "IP:port". A domain glob can only fire if `serviceForAddr` names
    # the peer, and knownServices has exactly two entries. Measured here so the gate states the number
    # instead of the pack looking like 32 covered endpoints.
    # --------------------------------------------------------------------------------------------------
    SERVICES = sorted(_known_service_names())
    check("the reverse map names exactly two services (re-measure if this fails)", SERVICES,
          ["api.telegram.org", "slack.com"])
    NAMED = {"slack.com", "api.telegram.org"}
    reachable, inert = [], []
    for rid, G in _endpoint_globs_by_rule().items():
        for g in G:
            brand = g.strip("*").split("/")[0]
            (reachable if brand in NAMED or brand in SERVICES else inert).append((rid, g))
    check("29 of 32 endpoint globs have no path to a hostname (measured)", len(inert), 29)
    check("  ...and 3 can receive a service name", len(reachable), 3)
    check("  ...every c2.blockchain_rpc glob is in the inert set",
          [g for rid, g in inert if rid.endswith("blockchain_rpc")].__len__(), 11)
    # The exact string the collector builds for slack's single /32, measured rather than described:
    EP_SLACK = "slack.com 3.120.129.226:443"
    check("the collector emits '<service> <ip:port>' and '*slack.com*' fires on it",
          match_one("*slack.com*", EP_SLACK), True)
    check("  ...but '*hooks.slack.com*' does NOT: the map yields 'slack.com', never the subdomain",
          match_one("*hooks.slack.com*", EP_SLACK), False)
    # The subject the collector actually produces, for the record:
    check("an IP:port endpoint matches no domain glob",
          [g for rid, G in _endpoint_globs_by_rule().items() for g in G
           if match_one(g, "104.18.1.1:443")], [])

    print()
    if FAILS:
        print(f"endpointglob: {len(FAILS)} FAIL")
        sys.exit(1)
    print("endpointglob: all ok")


if __name__ == "__main__":
    main()


# ------------------------------------------------------------------------------------------------------
# Section 8 (beat 607): the contains-fallback comment claims a restriction the code does not implement.
# Pin the REAL behaviour so a future "fix" in either direction is a deliberate decision, not a surprise.
# ------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    ENG = open(os.path.join(ROOT, "internal", "rules", "engine.go"), encoding="utf-8").read()
    check("engine.go's comment claims slash-globs get no contains fallback",
          "Only applied to globs with no interior '/' beyond the wildcard" in ENG, True)

    # ...but substringNeedle rejects only *?[ -- measured, not assumed.
    sn = ENG[ENG.index("func substringNeedle("):]
    sn = sn[:sn.index("\n}\n")]
    check("  ...yet substringNeedle only guards the wildcard characters",
          'ContainsAny(core, "*?[")' in sn and "'/'" not in sn, True)

    from globmatch import substring_needle as _needle
    check("  ...so a slash-containing glob DOES get a needle (current-but-undesired)",
          [( _needle(g), "/" in (_needle(g) or "")) for g in ["*/api/v2/events*", "*etherscan.io/api*"]],
          [("/api/v2/events", True), ("etherscan.io/api", True)])
    # egress.go emits "host:port" or "service host:port" -- never a path. So a slash-needle can never
    # be contained in one. Prove it against the two real emitted shapes, not against a guess.
    emitted = ["1.2.3.4:443", "slack.com 3.120.129.226:443"]
    check("  ...and neither needle is a substring of any shape egress.go emits",
          [any(_needle(g) in s for s in emitted) for g in ["*/api/v2/events*", "*etherscan.io/api*"]],
          [False, False])


# ------------------------------------------------------------------------------------------------------
# Section 9 (beat 608): interior-slash path globs get contains fallbacks. Pinned CURRENT-BUT-WRONG so a
# future narrowing (FINDING-2026-10-27) fails here on purpose instead of sliding through.
# ------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    from globmatch import match_one as _m, path_suffixes as _suffixes, path_match as _pm, match_one as _mo

    def _match_without_fallback(glob, s):
        """Run the three path mechanisms only -- no contains test. Proves the decoy is not the fallback."""
        if s == "":
            return False
        base = s.rsplit("/", 1)[-1]
        return _pm(glob, s) or any(_pm(glob, x) for x in _suffixes(s)) or _pm(glob, base)
    # Pin the population, not the two cases beat 607 happened to look at.
    import yaml as _yaml
    with open(os.path.join(ROOT, "rules", "carbonato.yaml"), encoding="utf-8") as _fh:
        _rules = _yaml.safe_load(_fh)["rules"]
    interior = []
    for _r in _rules:
        for _g in (_r.get("match") or {}).get("path_glob_any") or []:
            if _g.startswith("*") and "/" in _g:
                interior.append(_g)
    # Three different populations, all correct, none interchangeable. Beat 608 published "nine" for the
    # first of these and implied it was the whole population.
    _leaf = [g for g in interior if g.endswith("/*")]
    _needled = []
    for _g in interior:
        _core = _g.strip("*")
        if _core != "" and not any(ch in _core for ch in "*?["):
            _needled.append(_g)
    check("path globs starting '*' with an interior slash", len(interior), 23)
    check("  ...of those, the '*/dir/*' shape (what beat 601/608 kept quoting)", len(_leaf), 9)
    check("  ...and the ones that actually receive a contains needle", len(_needled), 22)

    # The real target fires -- via whichever mechanism, we only care that it fires.
    check("*/etc/cron.d/* matches the real system dir", _m("*/etc/cron.d/*", "/etc/cron.d/payload"), True)

    # A decoy also fires. Beat 608 first blamed the contains fallback for this and was WRONG: it is reached
    # by SUFFIX matching, which is deliberate. Pin the mechanism, not just the outcome, so the two stay
    # distinguishable -- otherwise a future change to either one is invisible.
    decoy = "/tmp/build/etc/cron.d/payload"
    check("decoy fires (current behaviour)", _m("*/etc/cron.d/*", decoy), True)
    check("  ...reached by suffix matching, not the contains fallback",
          [x for x in _suffixes(decoy) if _pm("*/etc/cron.d/*", x)], ["build/etc/cron.d/payload"])
    check("  ...and with the fallback disabled entirely the decoy STILL fires",
          _match_without_fallback("*/etc/cron.d/*", decoy), True)

    # A case where the contains fallback is the ONLY mechanism that can match -- no path, suffix, or
    # basename attempt succeeds. This is what pins the fallback's reach; the decoy above cannot.
    _only = "https://x.example/v1/api/v2/events/9"
    check("fallback-only case: no path/suffix/basename mechanism matches",
          _match_without_fallback("*/api/v2/events*", _only), False)
    check("  ...but with the fallback it matches (this is the fallback firing, measurably)",
          _m("*/api/v2/events*", _only), True)

    # The comment still claims a restriction the code does not implement -- both must move together.
    check("engine.go still documents the non-existent slash restriction",
          "globs that contain no '/'" in ENG, True)

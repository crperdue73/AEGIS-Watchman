#!/usr/bin/env python3
"""bindglob2_test -- the 3 detail_glob_any values, against the shape the collector actually emits.

Beat 593. `carbonato.container.host_root_mount` globs `binds:
["*:/host*", "/:/", "*/var/run/docker.sock*"]`. The matcher is the familiar `anyGlobMatches`, but the
SUBJECT is not: `internal/telemetry/docker.go:151` emits

    detail["binds"] = strings.Join(cfg.Binds, ",")

one comma-joined string of every bind, not a list. The comment above it says the join exists so "rules
glob on '*:/' to catch the host-root mount specifically" -- and that is exactly what to test: whether
these three globs stay correct when there are several binds, because a substring test over a joined
string does not know where one bind ends and the next begins.

Three questions, same as every other glob family: (1) does any value shadow another, (2) is anything
anchored wrong, (3) does a glob need an input shape the collector never produces. Limits: reachability
is not detection. The ledger stays 47 LIVE / 78 REACH / 0 unread.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from globmatch import any_glob_matches, match_one, path_match, path_suffixes, substring_needle  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")



def main():
    # READ FROM THE PACK, not typed here. My first version hardcoded this list -- and then mutant M4
    # (replacing "/:/" with "*/:/host*" in carbonato.yaml) SURVIVED, along with M5 (adding a shadowing
    # glob), because the suite was interrogating my own constant instead of the rule. A suite that
    # transcribes its subject is testing the transcription. Same defect as the beat-591 greps, different
    # costume: the words were right and the work was missing.
    import yaml

    _doc = yaml.safe_load(open(os.path.join(os.path.dirname(HERE), "rules", "carbonato.yaml"),
                               encoding="utf-8"))
    _host_bind = [r for r in _doc["rules"] if r["id"] == "carbonato.container.host_root_mount"]
    check("the host_root_mount rule exists", len(_host_bind), 1)
    GLOBS = _host_bind[0]["match"]["detail_glob_any"]["binds"]
    check("the rule globs exactly one detail key, 'binds'",
          sorted(_host_bind[0]["match"]["detail_glob_any"]), ["binds"])
    check("the pack supplies 2 globs", len(GLOBS), 2)
    check("the pack's globs are the two this suite reasons about",
          sorted(GLOBS), sorted(["/:/", "*/var/run/docker.sock*"]))

    # --------------------------------------------------------------------------------------------------
    # 0. The matcher is the same one the path and endpoint families use -- confirm, don't assume.
    # --------------------------------------------------------------------------------------------------
    check("both globs are bare '*x*' forms with no interior wildcard",
          [g for g in GLOBS if substring_needle(g) is None], [])
    check("needles are as expected",
          [substring_needle(g) for g in GLOBS], ["/:/", "/var/run/docker.sock"])

    # --------------------------------------------------------------------------------------------------
    # 1. The emitted shape: binds is ONE comma-joined string.
    # --------------------------------------------------------------------------------------------------
    # CORRECTED bench 610: previously asserted True, which only held via the bogus needle '/:/'.
    # Without a needle, '/:/host' must be reached by path/suffix/basename or not at all -- and it is not.
    check("a single host-root bind matches '/:/'", match_one("/:/", "/:/host"), True)
    # DELETED beat 614: the pack no longer ships "*:/host*". It matched "/data:/host" -- which is not a
    # host-root mount at all -- and that is precisely why it is gone. The false-positive family is
    # recorded in section 0b below as a deletion record rather than as live assertions.
    check("  ...and the deleted glob is really gone from the pack",
          [g for g in GLOBS if g == "*:/host*"], [])
    check("the docker socket bind matches its glob",
          match_one("*/var/run/docker.sock*", "/var/run/docker.sock:/var/run/docker.sock"), True)

    # --------------------------------------------------------------------------------------------------
    # 2. The join is where this can go wrong. Several binds arrive as one string, and every glob is a
    #    substring test on that whole string.
    # ------------------------------------------------------------------------------------------------==
    multi = "/data:/data,/:/host,/var/run/docker.sock:/var/run/docker.sock"
    check("with several binds, the host-root bind is still caught", any_glob_matches(GLOBS, multi), True)

    # The dangerous direction: a bind pair SPLIT across the comma. 'a,b' contains ':/host' only if some bind
    # ends the host side there -- but a needle can also be satisfied ACROSS the boundary.
    # I got this wrong TWICE. First I predicted [] and then wrote a replacement asserting ['*:/host*'] on the
    # witness "/host,/host" -- which has no colon at all, so it matches nothing, because the needle is
    # ':/host'. The claim is real but the witness was malformed. The actual shape: one bind's host side ends
    # exactly where the next bind begins, so the needle ':/host' is satisfied by ':<first>/host' with the
    # comma invisible to a substring test. Two binds, each NOT a host-root mount on its own terms, together
    # read as one.
    # DELETED beat 614 -- the comma-split witness '/a:/host,/host' was carried by '*:/host*' only.
    # Its two binds are '/a:/host' and '/host': NEITHER source is '/', so the shape it "proved" was a
    # false positive. A glob can earn a unique witness by being WRONG; that is not earning a place.
    # Recorded here so the reasoning survives the code:
    #   OLD '*:/host*' on '/a:/host,/host' -> True   (a false positive, and its only unique witness)
    #   surviving '/:/'  on that shape      -> False  (correct)
    check("the deleted glob's only unique witness was itself a false positive",
          (match_one("/:/", "/a:/host,/host"), match_one("*/var/run/docker.sock*", "/a:/host,/host")),
          (False, False))

    # 0b. DELETION RECORD -- what "*:/host*" did, kept as measured facts about a glob that no longer ships.
    # These assert the matcher's behaviour, NOT the pack's contents, so they stay true and keep the
    # reason for the deletion auditable. If neargo ever changes, these change and say so.
    check("  DELETED glob would have fired on '/etc:/host' (mount INTO container, opposite of an escape)",
          match_one("*:/host*", "/etc:/host"), True)
    check("  ...and on '/data:/host/app'", match_one("*:/host*", "/data:/host/app"), True)
    check("  ...but NOT on '/srv:/home/host' -- the over-match was UNEVEN, which is worse than broad",
          match_one("*:/host*", "/srv:/home/host"), False)
    check("  proposed tightening '*/:/host*' had ZERO unique witnesses vs '/:/'",
          [b for b in ["/:/host", "/:/host/etc", "/:/host/home/u"]
           if match_one("*/:/host*", b) and not match_one("/:/", b)], [])

    # A bind whose HOST side merely looks like the socket path is a false positive, by design of '*x*'.
    check("a CONTAINER-side path of /var/run/docker.sock fires the socket glob (unanchored)",
          match_one("*/var/run/docker.sock*", "/var/run/docker.sock:/app/x"), True)
    check("a path that merely CONTAINS the socket path fires it too",
          match_one("*/var/run/docker.sock*", "/tmp/not/var/run/docker.sock.bak:/x"), True)

    # --------------------------------------------------------------------------------------------------
    # 3. Shadowing: does one glob subsume another? Behavioural witness set, not string containment.
    # --------------------------------------------------------------------------------------------------
    WITNESSES = [
        "/:/host", "/:/", "/data:/host", "/data:/host/x", "/var/run/docker.sock:/srv",
        "/srv:/var/run/docker.sock", "/tmp/x:/y", "", "/host:/host", "/a:/host,/b:/c",
    ]


    def sig(globs):
        return tuple(any_glob_matches(globs, w) for w in WITNESSES)


    shadowed = [g for g in GLOBS
                if sig([g]) == sig([h for h in GLOBS if h != g])]
    check("no glob is behaviourally redundant with the others", shadowed, [])

    # MEASURED, not predicted. I had guessed {2, 3, 3} and the real distribution is {4, 1, 2}: '/:/' has
    # only ONE witness of its own, because almost everything it matches ("/:/host", "/:/") is also caught by
    # '*:/host*'. So '/:/' is the weakest of the three -- it earns its place on exactly one shape.
    counts = {g: sum(1 for w in WITNESSES if match_one(g, w) and not any(
        match_one(h, w) for h in GLOBS if h != g)) for g in GLOBS}
    # RE-MEASURED beat 614 after deleting "*:/host*": the distribution changed, as it had to.
    # Last beat '/:/' had ONE unique witness because '*:/host*' was eating most of its shapes. With the
    # over-broad glob gone, '/:/' now owns two -- '/:/host' and '/:/' -- and that is the point: the
    # shapes '/:/' owns are all TRUE host-root mounts, whereas the four that '*:/host*' used to own
    # were a mix, and it could not tell them apart.
    check("each glob has at least one witness only it matches (re-measured)",
          counts, {"/:/": 2, "*/var/run/docker.sock*": 2})
    check("  ...'/:/' now imports the host-root shapes the deleted glob used to shadow",
          sorted(w for w in WITNESSES if match_one("/:/", w) and not any(
              match_one(h, w) for h in GLOBS if h != "/:/")), ["/:/", "/:/host"])

    # --------------------------------------------------------------------------------------------------
    # 4. An incompletely-expanded var: docker reports binds from HostConfig, which is post-resolution, so
    #    this is a note rather than a claim. What IS claimable: an empty binds detail never matches.
    # --------------------------------------------------------------------------------------------------
    check("an empty binds string matches nothing", any_glob_matches(GLOBS, ""), False)
    check("a binds value of just a comma matches nothing", any_glob_matches(GLOBS, ","), False)

    # --------------------------------------------------------------------------------------------------
    # 5. Beat 598: "*:/host*" is a SUBSTRING test wearing a mount test's clothes. It matches any bind
    #    whose TARGET merely starts with the letters "host", because path.Match's trailing '*' matches
    #    empty. Full finding: docs/FINDING-2026-10-26b-host-root-substring.md
    #    These are asserted as CURRENT behaviour, not desired behaviour. If the pack is tightened, they
    #    fail -- which is the point: somebody must then change them on purpose.
    # --------------------------------------------------------------------------------------------------
    HOSTG = [g for g in GLOBS if g == "*:/host*"]
    check("the over-matching '*:/host*' glob is GONE from the pack", len(HOSTG), 0)
    # Kept as a measured record of the defect that justified the deletion (asserts the MATCHER, not the pack).
    for fp in ("/etc:/host", "/data:/hostdata", "/mnt:/hostile", "/x:/host-backup", "/:/hostname"):
        check(f"  the deleted glob would have fired on {fp!r}",
              match_one("*:/host*", fp), True)
    check("  ...and could not tell '/:/host' (real) from '/:/hostname' (not)",
          (match_one("*:/host*", "/:/host"), match_one("*:/host*", "/:/hostname")), (True, True))
    # CORRECTED beat 614: I first claimed '/:/' was silent on ALL of them. It is not -- '/:/hostname'
    # fires, because '/:/' is a plain substring test and '/:/hostname' literally begins '/:/'. That is a
    # residual over-match in the surviving glob, and I am recording it as one instead of quietly
    # choosing witnesses that pass. It is much narrower than before (a container path starting with
    # "/:/", i.e. a destination that itself looks like an absolute path) but it is real.
    check("  ...while the surviving '/:/' is silent on the 'host' false positives",
          [b for b in ("/etc:/host", "/data:/hostdata", "/mnt:/hostile")
           if match_one("/:/", b)], [])
    check("  RESIDUAL over-match: '/:/hostname' still fires (substring, not segment, test)",
          match_one("/:/", "/:/hostname"), True)
    check("  ...and still fires on real host-root mounts",
          [b for b in ("/:/host", "/:/host/data") if match_one("/:/", b)],
          ["/:/host", "/:/host/data"])

    # --------------------------------------------------------------------------------------------------
    # 6. Beat 593b recorded that the comma-joined binds string is ORDER-DEPENDENT. Re-measured here and
    #    it is NOT. Both orderings fire. Pinned so the false note stops being repeated.
    # --------------------------------------------------------------------------------------------------
    ORDER_CASES = {
        "/:/host,/data:/data": True,
        "/data:/data,/:/host": True,
        "/data:/data,/:/host,/x:/x": True,
        "/var/run/docker.sock:/var/run/docker.sock,/data:/data": True,
        "/data:/data,/var/run/docker.sock:/var/run/docker.sock": True,
    }
    check("the comma-joined binds string is order-INDEPENDENT for these globs (beat 593b was wrong)",
          {s: any_glob_matches(GLOBS, s) for s in ORDER_CASES}, ORDER_CASES)

    # --------------------------------------------------------------------------------------------------
    # 7. Beat 609: the adapter JOINS binds into one comma string, so every bind glob is really tested
    #    against a BLOB. Sections 5-6 pinned single binds only. A genuine critical mount must survive the
    #    join; a benign list must not fire because of it.
    # --------------------------------------------------------------------------------------------------
    DOCKER = open(os.path.join(HERE, "..", "internal", "telemetry", "docker.go"), encoding="utf-8").read()
    check("docker.go joins binds with ',' into one detail value",
          'detail["binds"] = strings.Join(cfg.Binds, ",")' in DOCKER, True)

    # CORRECTED beat 614: "/host:/host" was listed as a genuine host-root mount here. It is not -- the
    # source is "/host", not "/". The deleted "*:/host*" made it fire, which was a false positive that
    # this suite had blessed. Real host-root mounts are "/:/..." forms.
    for real in ("/:/", "/:/host", "/:/host/etc", "/var/run/docker.sock:/var/run/docker.sock"):
        check(f"genuine mount {real} fires alone AND in company",
              (any_glob_matches(GLOBS, real), any_glob_matches(GLOBS, "/data:/data," + real)),
              (True, True))

    check("a benign joined list does not fire from the join itself",
          any_glob_matches(GLOBS, "/data:/data,/logs:/logs"), False)

    # DEFECT 4 PIN -- RETIRED beat 629, deliberately, with the reason recorded here.
    #
    # This case used to assert (True, False): "docker.go's comment names a '*:/' glob AND the pack does
    # not ship it". That is a pin on a KNOWN DEFECT, written at beat 614 so the comment could not be
    # forgotten. Beat 629 fixed the comment, so the pair became (False, False) and this went red.
    #
    # A guard pinned to a defect must be retired the moment the defect is fixed, or it starts demanding
    # the bug back. Retired in place rather than deleted, so the history is legible.
    #
    # The claim is now owned by two things that check the RIGHT property:
    #   scripts/commentglobs.py      -- every glob a rule-referring comment quotes must be a LIVE parsed
    #                                   pack value (general; not specific to this comment)
    #   scripts/commentglobs_test.py -- plants a stale claim and four look-alikes, so the guard cannot be
    #                                   retuned into silence
    # What remains worth asserting here, cheaply: the stale phrasing is gone and has not come back.
    check("docker.go's stale '*:/' comment claim is gone (retired defect pin, beat 629)",
          ('"*:/" to catch the host-root mount' not in DOCKER,
           "*:/host*" not in GLOBS and "*/:/host*" not in GLOBS), (True, True))

    print()
    if FAILS:
        print(f"bindglob2: {len(FAILS)} FAIL")
        sys.exit(1)
    print("bindglob2: all ok")


if __name__ == "__main__":
    main()

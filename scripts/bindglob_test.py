#!/usr/bin/env python3
"""bindglob_test.py — one test per glob in carbonato.container.host_root_mount.

WHY
  Beat 575 found the rule may be half-dead. Beat 576 then found that rule is "certified" by a test
  (carbonato_pack_test.go:156) that asserts the rule ID fired, in a map checked AFTER the engine ran —
  structurally unable to say WHICH glob fired. So a rule with one silent glob and one lying glob passes
  its own test forever.

  This is the per-glob test that rule-level test cannot be. It does not run Go (no toolchain on this
  host); it pins the behaviour of `neargo`, the line-for-line transcription of engine.go:237-304 that
  the project already trusts for exactly this. Every assertion below compares against a LITERAL
  measured from the transcription, never against another assertion.

  If engine.go or the pack's three globs change, these go red — which is the point. A measurement that
  cannot be invalidated is not a measurement.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import neargo                                                # noqa: E402

# Beat 614: "*:/host*" deleted from the pack -- redundant with "/:/" and its only distinct
# effect was a critical-rule false positive on -v /etc:/host. See bindglob2_test section 0.
GLOBS = ["/:/", "*/var/run/docker.sock*"]
ok_n = 0
bad_n = 0


def ok(label, cond):
    global ok_n, bad_n
    if cond:
        ok_n += 1
        print(f"ok    {label}")
    else:
        bad_n += 1
        print(f"FAIL  {label}")


def fires(glob, binds):
    return neargo.match_one(glob, binds)



def main():
    # --- 1. the three globs are the ones in the pack ------------------------------------------------
    import re                                                    # noqa: E402

    pack = os.path.join(HERE, "..", "rules", "carbonato.yaml")
    with open(pack, encoding="utf-8") as fh:
        text = fh.read()
    block = re.search(r"- id: carbonato\.container\.host_root_mount(.*?)(?=\n  - id: )", text, re.S).group(1)
    listed = re.findall(r'^\s+- "([^"]+)"', block[block.index("detail_glob_any"):], re.M)
    ok(f"the pack still lists exactly these binds globs (got {listed})", sorted(listed) == sorted(GLOBS))

    # --- 2. /:/ does NOT catch a real host-root mount ----------------------------------------------
    # CORRECTED 2026-10-27 (beat 610). All three were asserted False as "the defect". They were False
    # only under neargo's invented '/' refusal, which stopped the contains fallback. engine.go has no
    # such refusal: needle '/:/' is a substring of '/:/host', '/:/etc' and '/:/data', so the glob FIRES.
    # That is the intended breadth of a '*x*' glob, and it closes the "host root missed" gap this file
    # was written to document. Pinned True, with the reason recorded so it is not reverted as a regression.
    ok("/:/ fires on '/:/host' via the contains fallback", fires("/:/", "/:/host") is True)
    ok("/:/ fires on '/:/etc' (same fallback)", fires("/:/", "/:/etc") is True)
    ok("/:/ fires on '/:/data' (same fallback)", fires("/:/", "/:/data") is True)
    # control: it does fire on the one shape it can match, so the test isn't just asserting False
    ok("/:/ DOES fire on the literal '/:/' (control — proves the probe can say True)",
       fires("/:/", "/:/") is True)

    # --- 3-5 REWRITTEN beat 614, 2026-10-27 ------------------------------------------------------
    # These sections used to document "*:/host*": how it fired via a suffix artifact, the family of
    # false positives it produced (/etc:/hosts, /data:/hostdata, /srv:/host), and the comma-split
    # shape. That glob is DELETED from the pack. Its distinguishing behaviour was the false
    # positives -- which are now the reason it is gone, so they belong in bindglob2's deletion
    # record, not as live assertions about a glob that no longer ships.
    # What matters now: the surviving globs still cover host-root mounts and stay silent on benign ones.
    ok("surviving '/:/' fires on a host-root mount", fires("/:/", "/:/host") is True)
    ok("surviving '/:/' fires on the deeper host-root mount", fires("/:/", "/:/host/etc") is True)
    ok("surviving '/:/' is SILENT on the old false positive '/etc:/host'", fires("/:/", "/etc:/host") is False)
    ok("surviving socket glob fires on the docker socket",
       fires("*/var/run/docker.sock*", "/var/run/docker.sock:/var/run/docker.sock") is True)
    ok("no surviving glob fires on a benign absolute bind",
       [g for g in GLOBS if fires(g, "/etc/localtime:/etc/localtime:ro")] == [])

    # --- 5b. The deleted glob's lessons, kept as matcher facts (beat 614) --------------------------
    # These six replace the assertions I removed with the glob. Not padding: each one is a measured
    # property that justified the deletion, and each asserts the MATCHER (stable), not the pack.
    ok("the deleted '*:/host*' carried needle ':/host'",
       neargo.substring_needle("*:/host*") == ":/host")
    ok("the proposed tightening '*/:/host*' carried needle '/:/host'",
       neargo.substring_needle("*/:/host*") == "/:/host")
    ok("'*/:/host*' had no shape '/:/' did not already catch",
       [b for b in ("/:/host", "/:/host/etc", "/:/host/home/u")
        if fires("*/:/host*", b) and not fires("/:/", b)] == [])
    ok("the deleted glob's only unique witness '/a:/host,/host' was a false positive",
       fires("*:/host*", "/a:/host,/host") and not fires("/:/", "/a:/host,/host"))
    ok("the deleted glob over-matched UNEVENLY: '/etc:/host' fires",
       fires("*:/host*", "/etc:/host"))
    ok("  ...but '/srv:/home/host' does not -- uneven over-match trains people to ignore the alert",
       not fires("*:/host*", "/srv:/home/host"))

    # --- 6. docker.go joins binds with ',' — that is the premise of all of the above ---------------
    with open(os.path.join(HERE, "..", "internal", "telemetry", "docker.go"), encoding="utf-8") as fh:
        docker = fh.read()
    ok("docker.go still stores binds as ONE comma-joined value",
       'detail["binds"] = strings.Join(cfg.Binds, ",")' in docker)

    # --- 7. the disease isn't binds (beat 578): a bind spec satisfies a PERSISTENCE rule -------------
    # docker run -v x:/etc/crontab:/dest mounts a file ONTO /etc/crontab inside a container. It is not a
    # host crontab write. The rule fires anyway, off the '/etc/crontab' suffix of a src:dst spec.
    ok("a bind spec 'x:/etc/crontab' matches the crontab persistence glob",
       neargo.match_one("*/etc/crontab", "x:/etc/crontab") is True)
    ok("  ...and '*/etc/cron*' too", neargo.match_one("*/etc/cron*", "x:/etc/crontab") is True)
    # control: the probe can say False, so 'True' above is a fact and not the only thing it can say
    ok("  control: '*.ssh/id_*' does NOT match 'x:/etc/crontab'",
       neargo.match_one("*/.ssh/id_*", "x:/etc/crontab") is False)

    # --- 8. the artefact is DATA-DEPENDENT, which is why it looks random -----------------------------
    # CORRECTED beat 610: this used to read "does NOT fire: the only usable suffix starts 'etc/' without
    # a leading '/'". True of the SUFFIX route only. The contains route finds needle '/etc/cron' in
    # '/data:/etc/cron.d/evil' and fires. So the "artefact is data-dependent" story was the mirror's,
    # not the engine's -- the bind-spec confusion is uniform, which is worse for precision and simpler
    # to reason about: every src:dst spec carrying a sensitive path trips the persistence rules.
    ok("'/data:/etc/cron.d/evil' DOES match '*/etc/cron*' (contains fallback)",
       neargo.match_one("*/etc/cron*", "/data:/etc/cron.d/evil") is True)
    ok("'x:/etc/crontab' DOES match '*/etc/cron*' (suffix starts at a '/')",
       neargo.match_one("*/etc/cron*", "x:/etc/crontab") is True)

    # --- 9. argv does not rescue it, and the reason is pinned -------------------------------------
    # CORRECTED beat 610: was asserted False ("the joined line breaks the suffix"). The suffix route
    # does fail, but the contains route succeeds -- needle 'etc/crontab' is in both the joined line and
    # the argv element. So argv DOES fire here, and the bind-spec->persistence confusion is reachable
    # through the command line as well as through the detail blob. Wider than recorded.
    ok("argv_any DOES fire on the bind spec as a docker arg (contains route)",
       neargo.any_argv(["*/etc/crontab"], ["docker", "run", "-v", "x:/etc/crontab:/dest"]) is True)

    # --- 10. zero anchored globs: nothing protects the argv rules ---------------------------------
    anchored = [g for g in re.findall(r'^\s+- "([^"]*\*[^"]*)"\s*$', text, re.M) if not g.startswith("*")]
    ok(f"no glob in the pack is anchored (all begin with '*'); got {len(anchored)} anchored",
       anchored == [])

    print(f"\nbindglob_test: {ok_n} ok, {bad_n} fail")
    sys.exit(1 if bad_n else 0)


if __name__ == "__main__":
    main()

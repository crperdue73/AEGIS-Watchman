#!/usr/bin/env python3
"""pathglob_test -- the 30 path globs, against the two shapes a file.access path actually takes.

BEAT 591. Second-largest REACH-only family (30 values / 4 rules) after endpoints, and it goes through
the SAME matcher (`engine.go:129` -> `anyGlobMatches(PathGlobAny, detailString(action,"path"))`), so
the transcription work from beat 590 already applies and is imported here rather than re-typed -- the
one thing I will not do is keep two copies of `matchOne` that can drift apart.

WHAT THIS ASKS THAT FIELDCHECK CANNOT. `fieldcheck` says REACH for all 30: no sample drives them, so
"a check proves the glob can fire." Fine. The questions I can answer without a rule-semantics opinion:

  A. SHADOWING. Same test as endpoints: a glob subsumed by a broader sibling in its own list is dead
     weight. Crucially, "subsumed" is not the same test for a path glob as for an endpoint glob --
     '*' does not cross '/', so containment of the *needle* is NOT sufficient. `*/.ssh/*` does not
     subsume `*/id_rsa*`; it subsumes only globs whose needles live under a .ssh directory.
  B. TWO PATH SHAPES. The collector can emit an absolute path (/root/.ssh/id_rsa) or a relative one
     (.ssh/id_rsa). Every glob here starts with '*' or '*', which the suffix loop rescues either way --
     but two globs have a trailing '*' and one has none, and that asymmetry changes what they catch.
  C. THE DIRECTORY ITSELF. `*/etc/cron.d/*` requires something AFTER the directory. A write to the
     directory, or a read of it, does not match. Whether the collector reports directories is a
     collector question I cannot answer from here -- so it is recorded as the stated limit, not
     asserted as a miss.
  D. THE SHAPES THAT LOOK LIKE A MATCH AND ARE NOT. `*credentials*` on a path containing "credentials"
     in a directory name; `*.env` which is a bare star-dot suffix and so matches ANY filename ending
     .env including .env.local; `*/authorized_keys` with no trailing star.

THE LIMIT, STATED UP FRONT: reachability is not detection. Nothing here drives a rule. The ledger stays
47 LIVE / 78 REACH / 0 unread after this beat, and any future note claiming otherwise is wrong.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import fieldcheck  # noqa: E402
# ONE transcription, not two -- and it is `globmatch`, not `endpointglob_test`. Importing a *_test.py
# module EXECUTES that suite (measured: 51 `ok` lines fired on import), which would fold another suite's
# checks into this one's count and make ordering decide the total.
from globmatch import (  # noqa: E402
    match_one, path_match, path_suffixes, substring_needle, any_glob_matches,
)

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def main():
    text = open(fieldcheck.PACK, encoding="utf-8").read()
    bylist = {}
    for rid, key, vals in fieldcheck.keys_by_rule(text):
        if key == "path_glob_any":
            bylist[rid] = list(vals)

    # --------------------------------------------------------------------------------------------------
    # 1. THE FAMILY, READ FROM THE PACK
    # --------------------------------------------------------------------------------------------------
    check("four rules carry path_glob_any", len(bylist), 4)
    check("they are the four the ledger names", sorted(bylist),
          ["carbonato.persist.file_write", "carbonato.secret.read_after_shell",
           "carbonato.secret.read_model_key", "carbonato.secret.read_order_follows_constraint"])
    check("30 values in total", sum(len(v) for v in bylist.values()), 30)
    check("per-rule counts are 8 / 5 / 7 / 10",
          {k: len(v) for k, v in sorted(bylist.items())},
          {"carbonato.secret.read_after_shell": 5, "carbonato.secret.read_model_key": 7,
           "carbonato.secret.read_order_follows_constraint": 8, "carbonato.persist.file_write": 10})

    # --------------------------------------------------------------------------------------------------
    # 2. THE TWO SHAPES. Every path glob begins with '*', so the suffix loop should make absolute and
    #    relative paths equivalent. Asserted per glob rather than assumed -- if one didn't, it would fire
    #    on relative paths only and nobody would notice.
    # --------------------------------------------------------------------------------------------------
    ABSOLUTE = ["/root/.ssh/id_rsa", "/root/.aws/credentials", "/home/dev/.kube/config",
                "/etc/cron.d/evil", "/root/.bashrc", "/home/dev/.netrc"]
    RELATIVE = [p.lstrip("/") for p in ABSOLUTE]

    # The twin test FAILED first run on exactly one witness: '/etc/cron.d/evil' vs 'etc/cron.d/evil'. Not a
    # port bug -- an absolute path yields the suffix 'etc/cron.d/evil' which matches '*/etc/cron.d/*', while
    # a RELATIVE path's own suffixes start at 'cron.d/evil' and never re-acquire '/etc'. So a glob whose
    # needle begins with a slash catches absolute paths only. Harmless here (the collector emits absolute
    # paths for file.access) but it is a fact worth pinning instead of asserting away.
    check("absolute and relative twins agree for every glob EXCEPT slash-leading needles",
          {a: [g for v in bylist.values() for g in v if match_one(g, a) != match_one(g, a.lstrip("/"))]
           for a in ABSOLUTE if [g for v in bylist.values() for g in v
                                 if match_one(g, a) != match_one(g, a.lstrip("/"))]},
          {"/etc/cron.d/evil": ["*/etc/cron.d/*"]})
    check("  ...a slash-leading needle needs an absolute path",
          (match_one("*/etc/cron.d/*", "/etc/cron.d/evil"), match_one("*/etc/cron.d/*", "etc/cron.d/evil")),
          (True, False))

    check("/root/.ssh/id_rsa fires the ssh glob",
          any_glob_matches(bylist["carbonato.secret.read_after_shell"], "/root/.ssh/id_rsa"), True)
    check("  ...and does so through the SUFFIX branch, not the whole string",
          (path_match("*/.ssh/*", "/root/.ssh/id_rsa"), match_one("*/.ssh/*", "/root/.ssh/id_rsa")),
          (False, True))
    check("/root/.aws/credentials fires BOTH the aws glob and the bare *credentials* glob",
          [g for g in bylist["carbonato.secret.read_order_follows_constraint"]
           if match_one(g, "/root/.aws/credentials")],
          ["*/.aws/*", "*credentials*"])

    # --------------------------------------------------------------------------------------------------
    # 3. SHADOWING, with the path-specific test. For endpoints, needle-containment decided it. Here '*' does
    #    not cross '/', so a glob only subsumes another if every string the other can match is a string it
    #    can match -- which for these lists means the needles must nest AND the trailing-star shapes agree.
    #    Tested by BEHAVIOUR on a witness set rather than by needle arithmetic, because the arithmetic got
    #    it wrong in beat 590 when a slash was involved.
    # --------------------------------------------------------------------------------------------------
    WITNESSES = [
        "/root/.ssh/id_rsa", "/root/.ssh/known_hosts", "/root/.ssh/config", "/root/.ssh/",
        "/home/u/.ssh/id_ed25519", "/root/.aws/credentials", "/root/.aws/config",
        "/home/u/.config/gcloud/application_default_credentials.json", "/home/u/.config/gcloud/legacy.json",
        "/home/u/.kube/config", "/root/.netrc", "/home/u/.git-credentials", "/home/u/.docker/config.json",
        "/srv/app/.env", "/srv/app/.env.local", "/srv/app/prod.env", "/srv/app/credentials.json",
        "/srv/app/credentials", "/srv/app/my_credentials.bak", "/etc/cron.d/evil", "/etc/cron.d/",
        "/etc/cron.daily/logrotate", "/etc/crontab", "/etc/cron.weekly/x", "/etc/systemd/system/x.service",
        "/root/.bashrc", "/root/.bash_profile", "/root/.profile", "/root/.zshrc",
        "/root/.ssh/authorized_keys", "/home/u/app/authorized_keys", "/home/u/openai_api_key.txt",
        "/home/u/.config/openai/config.json", "/home/u/anthropic.env",
    ]

    def signature(globs):
        """Bitmask of which witnesses these globs can catch. Equal masks = behaviourally identical."""
        return tuple(any(match_one(g, w) for g in globs) for w in WITNESSES)


    shadowed = {}
    for rid, globs in bylist.items():
        for g in globs:
            rest = [x for x in globs if x != g]
            if signature([g]) == signature(rest):
                shadowed.setdefault(rid, []).append(g)

    for rid, hits in sorted(shadowed.items()):
        for g in hits:
            print(f"     {rid}: {g!r} adds nothing to the other {len(bylist[rid]) - 1}")

    # These are the pairs a needle-only test would have called shadowed and a path test does not.
    check("NOTE: */.ssh/* does NOT subsume *id_rsa* -- the id_rsa glob catches /root/id_rsa outside .ssh",
          (match_one("*/.ssh/*", "/root/id_rsa"), match_one("*id_rsa*", "/root/id_rsa")), (False, True))
    # My first version tested "/root/id_rsa" as the discriminator and got False, because *id_rsa* matches
    # that too -- so the pair is NOT distinguished by paths outside .ssh. The real difference is the other
    # direction: */.ssh/* catches known_hosts/config, which no id_rsa glob can.
    check("  ...and the pair is distinguished by .ssh files that are not keys",
          (match_one("*id_rsa*", "/root/.ssh/known_hosts"), match_one("*/.ssh/*", "/root/.ssh/known_hosts")),
          (False, True))
    check("  ...so neither glob subsumes the other",
          signature(["*/.ssh/*"]) != signature(["*id_rsa*", "*id_ed25519*"]), True)
    check("no path glob is behaviourally identical to its siblings on this witness set", shadowed, {})

    # --------------------------------------------------------------------------------------------------
    # 4. THE ASYMMETRIES THAT ARE REAL (each measured, none asserted as a defect)
    # --------------------------------------------------------------------------------------------------
    check("'*/.ssh/*' does NOT match the directory itself -- something must follow the slash",
          match_one("*/.ssh/*", "/root/.ssh"), False)
    # Asserted False, measured True, and True is correct: Go's '*' matches the empty string, so a trailing
    # '*' after a slash accepts the slash itself. Two independent readings agree (my regex port, and Go's
    # documented matchChunk trying i == len(s)) -- and '/a/x/' vs '*/x/*' correctly does NOT match, so the
    # port is not simply being permissive. This is the real shape: a directory path with a trailing slash
    # matches these globs; a directory path WITHOUT one does not.
    check("a trailing '*' matches EMPTY, so '/root/.ssh/' DOES match",
          match_one("*/.ssh/*", "/root/.ssh/"), True)
    check("  ...but '/root/.ssh' (no trailing slash) does not",
          match_one("*/.ssh/*", "/root/.ssh"), False)
    # This line started as the opposite assertion -- I wrote that '*/x/*' should REJECT '/a/x/' as a control
    # against a permissive port, and it does not. It shouldn't: '/a/x/' splits to ['/a/x/', 'a/x/', 'x/', ''],
    # and the empty tail is a legal suffix, so '*','x','' matches with '*' and the final '*' both empty.
    # The correct control is a path where the middle component genuinely differs.
    check("  ...nor permissive: '*/x/*' rejects '/a/y/'", match_one("*/x/*", "/a/y/"), False)
    check("  ...and rejects '/a/xy/' -- the 'x' must be its own component",
          match_one("*/x/*", "/a/xy/"), False)
    check("'*/authorized_keys' (no trailing star) still matches a nested path via the suffix loop",
          match_one("*/authorized_keys", "/root/.ssh/authorized_keys"), True)

    # I asserted '*.env' would NOT match '.env.local'. It does -- through the suffix loop: '/srv/app/.env'
    # is not a suffix of '.env.local', but the CONTAINS fallback needs '.env' with no interior wildcard, and
    # '.env' occurs in '.env.local'. So '*.env' is a substring test, not a suffix test, and it catches
    # '.env.production', 'prod.env.bak', and any path with '.env' anywhere in it.
    check("'*.env' matches '.env.local' (contains fallback, not suffix)",
          match_one("*.env", "/srv/app/.env.local"), True)
    check("  ...and a backup 'prod.env.bak'",
          match_one("*.env", "/srv/app/prod.env.bak"), True)
    check("  ...but not a path with no '.env' substring at all",
          match_one("*.env", "/srv/app/environment"), False)
    check("'*credentials*' matches a path whose DIRECTORY holds the word",
          match_one("*credentials*", "/root/credentials/id_rsa"), True)
    check("  ...and a file merely named like it",
          match_one("*credentials*", "/srv/app/my_credentials.bak"), True)
    check("'/etc/cron.weekly/x' matches NOTHING in file_write (pack lists d/daily/hourly/crontab only)",
          [g for g in bylist["carbonato.persist.file_write"] if match_one(g, "/etc/cron.weekly/x")], [])
    check("'/etc/cron.d/' (the directory, WITH trailing slash) IS matched by '*/etc/cron.d/*'",
          [g for g in bylist["carbonato.persist.file_write"] if match_one(g, "/etc/cron.d/")],
          ["*/etc/cron.d/*"])
    check("  ...while '/etc/cron.d' (no trailing slash) is not",
          [g for g in bylist["carbonato.persist.file_write"] if match_one(g, "/etc/cron.d")], [])
    print()
    if FAILS:
        print(f"pathglob: {len(FAILS)} FAIL")
        sys.exit(1)
    print("pathglob: all ok")


if __name__ == "__main__":
    main()


# ------------------------------------------------------------------------------------------------------
# FINDING-2026-10-26e (beat 601): persist.file_write watches three DIRECTORIES but every glob ends in
# "/*", so the directory itself — which is exactly what `rm -rf /etc/cron.d` and `mkdir /etc/cron.d`
# report — matches nothing. The file cases work. The directory cases are the anti-forensics move.
# ------------------------------------------------------------------------------------------------------
def _persist_globs():
    import yaml
    with open(os.path.join(ROOT, "rules", "carbonato.yaml"), encoding="utf-8") as fh:
        pack = yaml.safe_load(fh)
    for r in pack["rules"]:
        if r["id"] == "carbonato.persist.file_write":
            return (r.get("match") or {}).get("path_glob_any") or []
    return []


def persist_directory_gap():
    """Is the bare directory of each watched dir covered by ANY glob? Returns the uncovered ones."""
    G = _persist_globs()
    watched = ["/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly", "/etc/systemd/system"]
    return [d for d in watched if not any(match_one(g, d) for g in G)]


if __name__ == "__main__":
    PERSIST = _persist_globs()
    check("persist.file_write globs load from the pack (not hardcoded)", len(PERSIST) >= 10, True)

    # The cases that DO work, so a fix cannot break them.
    for path, expect in [
        ("/etc/cron.d/evil", True),
        ("/etc/cron.daily/backdoor", True),
        ("/etc/systemd/system/x.service", True),
        ("/root/.bashrc", True),
        ("/home/u/.ssh/authorized_keys", True),
        ("/etc/crontab", True),
        ("/etc/passwd", False),
    ]:
        check(f"  persist fires on {path!r} == {expect}",
              any(match_one(g, path) for g in PERSIST), expect)

    # The gap, pinned as CURRENT-BUT-WRONG so tightening the pack fails this suite on purpose.
    uncovered = persist_directory_gap()
    check("CURRENT DEFECT: all four watched directories are unmatched by every glob", uncovered,
          ["/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly", "/etc/systemd/system"])
    check("  ...so `rm -rf /etc/cron.d` (dir DELETE) fires no persist alert",
          any(match_one(g, "/etc/cron.d") for g in PERSIST), False)
    check("  ...and `mkdir /etc/cron.d` (dir CREATE) fires none either",
          any(match_one(g, "/etc/cron.d") for g in PERSIST), False)
    check("  ...while a FILE delete in the same dir IS caught (the asymmetry, stated)",
          any(match_one(g, "/etc/cron.d/x") for g in PERSIST), True)


# ------------------------------------------------------------------------------------------------------
# Section 8: the finding must live in the project's real Go suite too, not only in this Python
# transcription. No Go toolchain here, so we can't compile -- we can insist the test exists, names the
# right rule, and still asserts the DEFECT (if someone "fixes" it by deleting the assertion, we notice).
# ------------------------------------------------------------------------------------------------------
def _go_pack_test():
    with open(os.path.join(ROOT, "internal", "rules", "carbonato_pack_test.go"), encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    GO = _go_pack_test()
    check("Go suite carries TestPersistDirectoryItselfIsMissed", "func TestPersistDirectoryItselfIsMissed" in GO, True)
    body = GO[GO.index("func TestPersistDirectoryItselfIsMissed"):]
    body = body[:body.index("\n}\n")] if "\n}\n" in body else body
    check("  ...it exercises the persist rule", "carbonato.persist.file_write" in body, True)
    check("  ...it asserts the four watched directories stay silent (the defect)",
          sum(f'"{d}"' in body for d in ["/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly", "/etc/systemd/system"]), 4)
    check("  ...it covers both DELETE and CREATE on a directory",
          ('"DELETE"' in body) and ('"CREATE"' in body), True)
    check("  ...and it still proves a FILE delete in the same dir IS caught (the asymmetry)",
          '"/etc/cron.d/payload"' in body, True)
    check("  ...its comment points a future fixer at the finding doc",
          "FINDING-2026-10-26e" in body, True)


# ------------------------------------------------------------------------------------------------------
# Section 9 (beat 604): the persist-directory chain, traced end to end, plus the SECOND gap found while
# tracing it -- rmdir/mkdir/mkdirat appear nowhere in the codebase. Today the CREATE/DELETE nametype check
# rescues them; there is no test for a record whose PATH carries no nametype.
# ------------------------------------------------------------------------------------------------------
def _filewatch_go():
    with open(os.path.join(ROOT, "internal", "telemetry", "filewatch.go"), encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    FW = _filewatch_go()
    # The order of the two decisions is what makes a directory DELETE reach the engine at all.
    nt_pos = FW.index('p.Nametype == "CREATE" || p.Nametype == "DELETE"')
    sm_pos = FW.index("strictWriteSyscalls[r.Syscall]")
    check("filewatch: the CREATE/DELETE nametype check precedes the syscall map (dir ops survive)",
          nt_pos < sm_pos, True)
    check("filewatch: PARENT records are skipped so the dir path wins over its container",
          'p.Nametype == "PARENT"' in FW, True)

    check("GAP: rmdir is absent from the syscall maps", '"rmdir"' in FW, False)
    check("GAP: mkdir is absent from the syscall maps", '"mkdir"' in FW, False)
    check("GAP: mkdirat is absent from the syscall maps", '"mkdirat"' in FW, False)
    # Prove the failure mode rather than gesture at it: take the real map, drop the nametype rescue,
    # and check that a rmdir record with no tag falls through every gate.
    smap = set(re.findall(r'"([a-z0-9_]+)":\s*true', FW[FW.index("var strictWriteSyscalls"):FW.index("var openSyscalls")]))
    omap = set(re.findall(r'"([a-z0-9_]+)":\s*true', FW[FW.index("var openSyscalls"):FW.index("func (r *AuditRecord) WriteAction")]))
    check("  ...the two syscall maps are parsed from source (not typed)", (len(smap) > 20, len(omap)), (True, 3))
    for sc in ["rmdir", "mkdir", "mkdirat"]:
        check(f"  ...{sc} is in NEITHER map, so without a nametype it reaches writeTarget's dead end",
              (sc in smap, sc in omap), (False, False))
    check("  ...while unlinkat IS in strictWriteSyscalls (the contrast that hides the gap)",
          "unlinkat" in smap, True)


# ------------------------------------------------------------------------------------------------------
# Section 10 (beat 605): the missing fixture is now written. Pin it, and pin the fact that the watch-key
# rescue does NOT save rmdir/mkdir -- an operator's -k on those syscalls is silently ineffective.
# ------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    with open(os.path.join(ROOT, "internal", "telemetry", "filewatch_test.go"), encoding="utf-8") as fh:
        FT = fh.read()
    check("Go suite carries TestDirectoryOpsWithAndWithoutNametype",
          "func TestDirectoryOpsWithAndWithoutNametype" in FT, True)
    fbody = FT[FT.index("func TestDirectoryOpsWithAndWithoutNametype"):]

    # The watch-key rescue is gated on openSyscalls, so it cannot cover rmdir/mkdir.
    wt = FW[FW.index("func (r *AuditRecord) writeTarget()"):]
    wt = wt[:wt.index("\n}\n")]
    check("writeTarget: the watch-key rescue is ANDed with openSyscalls (not OR)",
          'if r.WatchKey != "" && openSyscalls[r.Syscall]' in wt, True)
    check("  ...so a -k watch on a rmdir path is silently ineffective (the test proves it)",
          "WITH a watch key was kept" in fbody, True)
    check("  ...and the parser drops non-actionable records, so zero records is a valid expectation",
          "if len(recs) == 0 {" in fbody, True)
    check("  ...with unlinkat as the contrast case that DOES survive untagged",
          "untagged unlinkat" in fbody, True)

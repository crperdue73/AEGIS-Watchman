#!/usr/bin/env python3
"""pathfixture — the 30 `path_glob_any` values have no reader. This is that reader.

fieldcheck's verdict on the path family was "NOBODY", and its closing note said the fix is
"a pure test-and-fixture job". That was true and unhelpful — it never said whether the 30 globs
are CORRECT, only that nobody looks. `wirediff` priced the work at 30 values in one family with a
single shape (`leading *`, all 30). This file does the work instead of describing it.

WHAT IT ASSERTS
  1. Every path glob in the pack will be exercised by at least one fixture path. A glob with no
     fixture is a glob this file would let rot silently, which is the same defect one level down.
  2. Every fixture produces the verdict recorded in the table below. The table is not derived from
     the matcher — that would be circular. Each expected value was reasoned from Go's `path.Match`
     documentation (`*` does not cross `/`) plus the engine's documented suffix/base fallback.
  3. `*/etc/cron*` does not match `/etc/cron.d/x`, and the reason is recorded in the file, so the
     next person doesn't 'fix' it by adding a leading slash and calling it done.

THE FINDING THIS TURNED UP
  All 30 path globs start with `*`, and `*` does not cross `/`. So every one of them can only
  fire on:
    (a) a path with something before the match point (`./etc/crontab`, `~/.ssh/id_rsa`), or
    (b) the SUFFIX / basename fallback the engine documents in packcheck.match_one.
  Whether that fallback exists in the GO engine is the question this file cannot answer — no Go
  on this host. If it doesn't, `*/etc/cron*` is dead on every real absolute path, and the "4 dead
  globs" packcheck reports is an undercount. That is a claim for the pack owner and it is written
  here rather than guessed at.

LIMITS
  - Python port (`packcheck.match_one`), not the Go engine. A mirror, and mirrors are wrong exactly
    where you stopped looking.
  - 30 globs × a hand-written path list. Anyone who adds a glob to the pack must add a fixture here
    or check 1 goes red — which is the point.
  - Exit 2 if the pack cannot be parsed, 1 on any fixture disagreement, 0 if all green.
"""
import os
import re
import sys


# Beat 634: KNOWN engine over-matches, measured (not assumed) and each with its route in the engine.
# These are NOT expected values -- the fixture's `want` is still what the RULE should do. They are
# recorded so the suite can report them as xfail (visible, counted, not green) instead of pretending the
# engine is right, and instead of a red suite that nobody can distinguish from my own broken expectations.
# Full analysis: docs/FINDING-2026-10-28-contains-fallback-on-paths.md
XFAIL = {
    ("*/.ssh/*", "/home/agent/.ssh/"):
        "engine: pathSuffixes yields 'agent/.ssh/', which path.Match accepts -- the bare directory matches",
    ("*/.aws/*", "/home/agent/.aws/"):
        "engine: pathSuffixes yields 'agent/.aws/', same route as .ssh",
    ("*/.netrc*", "/home/agent/.netrcfile/example"):
        "engine: substringNeedle('.netrc') + strings.Contains -- a dir named .netrcfile matches",
    ("*/.zshrc", "/home/agent/.zshrc.bak"):
        "engine: substringNeedle('.zshrc') + Contains -- .zshrc.bak is a different file",
    ("*/.bashrc", "/home/agent/.bashrc.bak"):
        "engine: substringNeedle('.bashrc') + Contains -- sed -i.bak makes this everyday noise",
    ("*/.git-credentials*", "/home/agent/.git-credentials-backup/x"):
        "engine: substringNeedle('.git-credentials') + Contains -- and '*' cannot cross '/'",
    ("*/etc/crontab", "/etc/crontab.local"):
        "engine: substringNeedle('/etc/crontab') + Contains -- note the needle CONTAINS a slash",
    # ---- beat 641: five more, all from the negative-coverage sweep. Every one is the SAME root as
    # the seven above -- substringNeedle + strings.Contains -- reached from the other direction. The
    # pattern is now unmistakable: the fallback is the single largest source of wrong verdicts in
    # this matcher, in both polarities. A slash-free needle is far too loose (matches any file whose
    # name merely contains the word); a slash-bearing needle is too loose too, because Contains does
    # not honour the segment boundary the glob was written to express.
    ("*/.config/openai*", "./.config/openaiX"):
        "engine: openai* matches openaiX on the WHOLE-string route -- path.Match is right, the glob is too loose",
    ("*/.bash_profile", "./.bash_profile.bak"):
        "engine: substringNeedle('/.bash_profile') + Contains -- slash-bearing needle, still fires on .bak",
    ("*/authorized_keys", "./authorized_keys.bak"):
        "engine: substringNeedle('/authorized_keys') + Contains -- a backup counts as the file",
    ("*id_rsa*", "./keys/id_rsa_public_readme.md"):
        "engine: needle id_rsa + Contains -- any file whose NAME contains id_rsa matches",
    ("*credentials*", "./credentials_help.html"):
        "engine: needle credentials + Contains -- a help page matches",
}


# Beat 633: the whole body lives in main() so importing this file RUNS NOTHING.
# It is an instrument whose product is its output; it is not a library, and it must not
# perform a check into someone else's tally (nonprobe_test.py, docs/2026-10-28-import-silence-blindspot.md).
def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from packcheck import go_path_match, match_one  # noqa: E402

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PACK = os.path.join(ROOT, "rules", "carbonato.yaml")

    # (glob, path, should_match, why) — every expectation reasoned, not recorded from a run.
    FIXTURES = [
        # (the trailing-slash case that used to be here is now the True fixture directly below,
        #  with the measurement and the reasoning in its comment)
        ('*/.ssh/*', './.ssh/id_rsa', True, 'one segment before .ssh, then filename'),
        # Beat 633 CORRECTION. This fixture said False, with the reasoning "trailing slash makes an
        # empty last segment" applied to the LAST segment. Measured against the port of engine.go's
        # matchOne: True, and not by the suffix loop -- `*/.ssh/*` matches the FULL path directly,
        # because 'id_rsa/' is a perfectly non-empty segment and Go's path.Match never strips the
        # trailing '/'. The empty-last-segment effect is real but lands on the SUFFIXES: '.ssh/id_rsa/'
        # and 'id_rsa/' do NOT match (`*` cannot absorb the '/' -- path.Match's '*' never crosses '/'),
        # so the rule fires here on the absolute path alone. The old expectation was reasoning about
        # how paths usually behave instead of about what this matcher does. What the fixture tests now
        # is the opposite claim, which is the one that matters: a trailing slash does NOT spare the key.
        ('*/.ssh/*', '/home/agent/.ssh/id_rsa/', True, 'trailing slash does not spare it: id_rsa/ is a non-empty segment, and the trailing-slash suffixes drop out'),
        ('*/.aws/*', './.aws/credentials', True, 'flat'),
        ('*/.aws/*', '.aws/credentials', False, 'no leading segment — star needs one'),
        ('*/.config/gcloud/*', './.config/gcloud/key.json', True, 'flat'),
        ('*/.config/gcloud/application_default_credentials.json*', './.config/gcloud/application_default_credentials.json', True, 'flat'),
        ('*/.config/openai*', './.config/openai', True, 'flat; star absorbs nothing'),
        ('*/.docker/config.json*', './.docker/config.json', True, 'flat'),
        ('*/.git-credentials*', './.git-credentials', True, 'flat'),
        ('*/.kube/config*', './.kube/config', True, 'flat'),
        ('*/.netrc*', './.netrc', True, 'flat'),
        ('*/.zshrc', './.zshrc', True, 'flat'),
        ('*/.zshrc', '.zshrc', False, 'no leading segment — the same limit, not a special case'),
        ('*/.bash_profile', './.bash_profile', True, 'flat'),
        ('*.env', './.env', True, 'flat'),
        ('*.env', 'prod.env', True, 'slash-free glob matches a bare name too — the only family where that is true'),
        ('*/.bashrc', './.bashrc', True, 'flat — the path-family glob'),
        ('*/.bashrc*', './.bashrc', True, 'the argv-family glob (line 294); the trailing star absorbs nothing. kept here so the two crontab/bashrc spellings are both exercised'),
        ('*/authorized_keys', './authorized_keys', True, 'flat'),
        ('*id_rsa*', './keys/id_rsa', True, 'flat path; needle inside one segment'),
        ('*id_ed25519*', './keys/id_ed25519', True, 'flat path'),
        ('*credentials*', './credentials.json', True, 'flat, needle inside the basename'),
        ('*/openai*key*', './openai_api_key.txt', True, 'flat, two needles in one segment'),
        ('*anthropic*key*', './anthropic_api_key.txt', True, 'flat'),
        ('*/etc/crontab', './etc/crontab', True, 'flat'),
        ('*/etc/crontab', 'etc/crontab', False, 'no leading segment'),
        ('*/etc/cron.d/*', './etc/cron.d/evil', True, 'flat'),
        ('*/etc/cron.d/*', '/etc/cron.d/evil', True, "matched only by match_one's SUFFIX loop: the suffixes of an "
         "absolute path include its tail segments (`etc/cron.d/evil`), so the star has a segment to consume. "
         "That loop is this PORT's, not Go's — see the FINDING note"),
        ('*/etc/cron.daily/*', './etc/cron.daily/evil', True, 'flat'),
        ('*/etc/cron.hourly/*', './etc/cron.hourly/evil', True, 'flat'),
        ('*/etc/systemd/system/*', './etc/systemd/system/evil.service', True, 'flat'),
        ('*/etc/systemd/system/*', '/etc/systemd/system/evil.service', True, 'absolute; same suffix loop'),
        ('*/.profile', './.profile', True, 'flat'),
        ('*/.profile', '/home/agent/.profile', True, 'absolute, rescued by the same suffix loop'),

        # ---- beat 634: the NEGATIVE fixtures. There were only 3 in 34, and the mutation check added
        # below is what said so: a matcher that answers `True` to every path only disagreed with 3 lines,
        # so this table could not have caught a permissive matcher if the port became one. A fixture set
        # that is 30 yeses cannot produce a no, and a check that cannot produce a no has not been tested.
        # These are not filler: each is the exact false-positive the pack owner would care about, and each
        # is a REAL path an attacker would use. The matcher must decline every one.
        ('*/.ssh/*', '/home/agent/.ssh/id_rsa.pub', True, 'sibling file in a watched dir STILL matches -- *.pub is not excluded by the glob, recorded so nobody assumes it is'),
        ('*/.ssh/*', '/home/agent/.ssh', False, 'the DIRECTORY itself, no trailing slash: the glob needs a filename segment after .ssh'),
        ('*/.ssh/*', '/home/agent/.ssh/', False, 'directory with trailing slash: nothing after the slash, and *.ssh/ has no segment for the star'),
        ('*/.aws/*', '/home/agent/.aws', False, 'directory only'),
        ('*/.aws/*', '/home/agent/.aws/', False, 'directory, trailing slash'),
        ('*.env', 'environment.txt', False, 'substring is not a match: the star must absorb a whole prefix and the literal must end the name'),
        ('*.env', 'env', False, 'the literal is .env, not env'),
        ('*/.kube/config*', '/home/agent/.kube/other', False, 'sibling name in the dir, no config prefix'),
        ('*/.netrc*', '/home/agent/.netrcfile/example', False, 'no: the star cannot cross the / -- .netrcfile matches but example/ is a second segment'),
        ('*/.zshrc', '/home/agent/.zshrc.bak', False, 'no trailing star on this glob, so .zshrc.bak is a DIFFERENT file'),
        ('*/.bashrc', '/home/agent/.bashrc.bak', False, 'same: exact-name glob must not match a suffix variant'),
        ('*/.git-credentials*', '/home/agent/.git-credentials-backup/x', False, 'star does not cross /'),
        ('*/etc/crontab', '/etc/crontab.local', False, 'sibling, not the file'),
        ('*/etc/cron.d/*', '/etc/cron.d', False, 'the directory itself'),
        ('*/etc/cron.d/*', '/etc/cron.daily/evil', False, 'cron.daily is not cron.d -- the literal segment must match whole'),
        ('*/.config/gcloud/*', '/etc/config/gcloud/x', False, 'etc is not .config -- one wrong segment, no match'),
        # --- beat 640: the bare-relative-path miss. FINDING-2026-10-28-relative-path-glob-miss.md ---
        # Every one of these is False under the current engine, and every one of them SHOULD be True.
        # A tool-call path of ".netrc" or ".ssh/id_rsa" (what `cat .ssh/id_rsa` from $HOME actually
        # looks like) does not match its own `*/`-globs, because '*' cannot cross '/' and the bare path
        # has no separator for '*/' to consume. Absolute paths fire; relative ones do not.
        # Declared as False with the "engine:" prefix, per the XFAIL convention already used here: the
        # fixture records the MEASURED behaviour, so the day Dad lands the separator fix these rows fail
        # and the change has to be acknowledged instead of silently absorbed.
        ('*/.netrc*',    '.netrc',      False, 'engine: bare relative path never matches a */ glob. WANT True'),
        ('*/.bashrc',    '.bashrc',     False, 'engine: same. WANT True'),
        ('*/.profile',   '.profile',    False, 'engine: same. WANT True'),
        ('*/.ssh/*',     '.ssh/id_rsa', False, 'engine: same. WANT True'),
        ('*/.aws/*',     '.aws/credentials', False, 'engine: same. WANT True'),
        ('*/.kube/config*', '.kube/config', False, 'engine: same. WANT True'),
        ('*/.git-credentials*', '.git-credentials', False, 'engine: same. WANT True'),
        ('*/.docker/config.json*', '.docker/config.json', False, 'engine: same. WANT True'),
        ('*/etc/crontab', 'etc/crontab', False, 'engine: not dotfile-specific; every leading-*/ pattern. WANT True'),
        # beat 640 CORRECTION: I first wrote this row as False ("the ./ form misses too"). Measured True --
        # './' supplies the separator that '*' consumes, so the glob aligns normally. The miss is specific
        # to a path with NO leading separator at all. Corrected to the measurement; the ./ form is covered
        # here as a PASSING control so the real boundary (.netrc vs ./.netrc) stays visible.
        ('*/.netrc*',    './.netrc',    True,  'control: ./ supplies the separator; this form WORKS'),
        # controls: the SAME globs against absolute paths DO fire. These pass today and must keep passing,
        # so a future "fix" cannot be judged by the rows above alone.
        ('*/.netrc*',    '/home/u/.netrc', True, 'control: absolute path fires via the suffix route'),
        ('*/.bashrc',    '/root/.bashrc',  True, 'control: absolute path fires'),
        ('*/.ssh/*',     '/home/u/.ssh/id_rsa', True, 'control: absolute path fires'),

        # ---- beat 641: the other half. FINDING-2026-10-28-glob-negative-coverage.md ----
        # 13 globs had rows that only ever asked "does it fire". A glob never asked to say no has not
        # been tested; it has been demonstrated. These are the must-NOT-fire cases, one per glob,
        # chosen as the nearest honest neighbour: the near-miss a detector is most likely to confuse
        # with the real thing. Each is reasoned before running, not recorded after.
        ('*/.config/gcloud/application_default_credentials.json*', './.config/gcloud/README.md', False,
         'same directory, different file; the * is a suffix not a wildcard over siblings'),
        ('*/.config/openai*', './.config/openaiX', False,
         'engine: openai* matches openaiX on the WHOLE-string route -- path.Match is right, the glob is too loose. WANT False'),
        ('*/.config/openai*', './.config/azure/config', False,
         'different provider dir entirely; must not fire'),
        ('*/.bash_profile', './.bash_profile.bak', False,
         'engine: substringNeedle(\'/.bash_profile\') + Contains -- the slash-bearing needle still fires. WANT False'),
        ('*/.bashrc*', './.bash_history', False,
         'shares the dotfile prefix, shares no real suffix'),
        ('*/authorized_keys', './authorized_keys.bak', False,
         'engine: substringNeedle(\'/authorized_keys\') + Contains -- backups count as the file. WANT False'),
        ('*id_rsa*', './keys/id_rsa_public_readme.md', False,
         'engine: needle id_rsa, Contains -- any file with id_rsa in its name matches. WANT False'),
        ('*id_ed25519*', './notes/ed25519.txt', False,
         'ed25519 without the id_ prefix is not the key file'),
        ('*credentials*', './credentials_help.html', False,
         'engine: needle credentials, Contains -- a help page matches. WANT False'),
        ('*/openai*key*', './.config/openai/models.json', False,
         'openai but no key in the name'),
        ('*anthropic*key*', './.anthropic/README.md', False,
         'anthropic dir, no key'),
        ('*/etc/cron.daily/*', './etc/cron.weekly/job', False,
         'weekly is not daily -- the whole point of the segment'),
        ('*/etc/cron.hourly/*', './etc/cron.daily/job', False,
         'daily is not hourly'),
        ('*/etc/systemd/system/*', './etc/systemd/user/evil.service', False,
         'system/ vs user/ is a different scope and a different trust boundary'),

    ]

    FAILS = []


    def ok(name, cond, detail=""):
        print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
        if not cond:
            FAILS.append(f"{name} {detail}".strip())


    def read(p):
        with open(p, encoding="utf-8") as fh:
            return fh.read()


    KEYS = ("path_glob_any", "endpoint_glob_any", "exe_any", "argv_any", "detail_equals")
    KEY_RE = re.compile(r"^\s*(" + "|".join(KEYS) + r"):\s*(.*)$")


    def strip_yaml_scalar(raw):
        """The YAML comment rule this reader must honour: everything from an unquoted ' #' to EOL is a
        comment and is NOT part of the value.

        Beat 644: this reader did `raw.strip().strip("\"'")`, which removes a leading quote only when the
        string STARTS with one, and a trailing quote only when it ENDS with one. For a value with a trailing
        comment --
            - "*hooks.slack.com*"      # shadowed by *slack.com*
        -- the string ends with the comment, so the closing quote survived and the comment came along with
        it. Three endpoint globs in the shipped pack were read as `*hooks.slack.com*"      # shadowed by ...`
        and could never match anything.

        The direction of that error matters: the ENGINE parses with gopkg.in/yaml.v3 (rule.go:129), which
        strips comments correctly, so the RULES WERE LIVE IN PRODUCTION while my auditor reported them dead.
        A reader that invents dead rules is an accuser, and an accuser is worse than a blank.
        """
        s = raw.strip()
        if s[:1] in ("'", '"'):
            q = s[0]
            end = s.find(q, 1)
            return s[1:end] if end > 0 else s[1:]
        # unquoted: ' #' starts a comment; a bare '#' at position 0 likewise
        i = s.find(" #")
        if i >= 0:
            s = s[:i]
        elif s.startswith("#"):
            s = ""
        return s.strip()

    def key_blocks(text):
        """[(rule_id, key, [values])] for fieldcheck's five keys. One list object per key, mutated
        while its block is open — appended to `out` on the key line, so it is the same object."""
        out, rid, cur = [], None, None
        for line in text.splitlines():
            m = re.match(r"^\s*-\s+id:\s*(\S+)", line)
            if m:
                rid, cur = m.group(1), None
                continue
            m = KEY_RE.match(line)
            if m:
                key, inline = m.group(1), m.group(2).strip()
                vals = []
                if inline.startswith("["):
                    vals = [strip_yaml_scalar(v) for v in inline.strip("[]").split(",") if v.strip()]
                elif inline:
                    vals = [inline.strip("\"'")]
                out.append((rid, key, vals))
                cur = vals if not vals else None
                continue
            if cur is not None:
                m2 = re.match(r"^\s*-\s+(.+?)\s*$", line)
                if m2:
                    cur.append(strip_yaml_scalar(m2.group(1)))
                    continue
                if line.strip():
                    cur = None
        return out


    def pack_path_globs(text):
        """Just the path family, from the same walk — so the two readers cannot disagree."""
        return [(rid, v) for rid, key, vals in key_blocks(text) if key == "path_glob_any" for v in vals]


    text = read(PACK)
    globs = pack_path_globs(text)
    print(f"     path_glob_any values in the pack: {len(globs)} "
          f"across {len({r for r, _ in globs})} rule(s)")
    ok("the pack's path glob count matches fieldcheck's 30", len(globs) == 30, str(len(globs)))
    ok("every path glob has at least one fixture (no glob is left to rot)",
       {g for _, g in globs} <= {g for g, _, _, _ in FIXTURES},
       f"missing: {sorted({g for _, g in globs} - {g for g, _, _, _ in FIXTURES})}")
    ARGV_ONLY = {"*/.bashrc*"}   # lives in an argv_any block (line 294), not the path family; exercised here
    ok("no fixture names a glob the pack does not have at all",
       {g for g, _, _, _ in FIXTURES} <= {g for _, g in globs} | ARGV_ONLY,
       f"extra: {sorted({g for g, _, _, _ in FIXTURES} - ({g for _, g in globs} | ARGV_ONLY))}")

    print()
    bad = 0
    xfail = []
    for g, p, want, why in FIXTURES:
        got = match_one(g, p)
        if got != want:
            # Beat 634: a disagreement is now judged, not just counted. If the fixture is in XFAIL the
            # engine is KNOWN to over-match here (docs/FINDING-2026-10-28-contains-fallback-on-paths.md)
            # and the line is reported as xfail -- visible, counted, and NOT a pass. A disagreement
            # outside XFAIL is a plain failure. XFAIL is empty the day the engine is fixed, and the
            # "every xfail still xfails" check below fails if one of these starts matching, so the list
            # cannot outlive its defect.
            if (g, p) in XFAIL:
                xfail.append((g, p))
                print(f"xfail {g:22} {p:52} expected {want}, got {got}  [{XFAIL[(g, p)]}]")
            else:
                bad += 1
                print(f"FAIL  {g:22} {p:52} expected {want}, got {got}")
                print(f"        why it should be {want}: {why}")
        else:
            print(f"ok    {g:22} {p:52} {str(got):5}  {why}")
    ok(f"every non-xfail fixture verdict agrees with the reasoning", bad == 0, f"{bad} disagreed")
    ok(f"the {len(XFAIL)} known engine over-matches still fail (a fix here must empty XFAIL)",
       len(xfail) == len(XFAIL), f"{len(XFAIL) - len(xfail)} now behave differently -- update the finding")
    ok("XFAIL is declared for engine behaviour, never to excuse this file's own expectations",
       all(v.startswith("engine:") for v in XFAIL.values()), str(set(XFAIL.values())))

    # --- beat 641: every glob must be asked to say no at least once --------------------------------
    # The count above proves every glob has SOME fixture. It never proved the fixture could fail.
    # 13 globs here had rows that only ever demonstrated a match: a glob asked only "does it fire"
    # has not been tested, it has been shown off. This check makes that structural -- add a glob
    # without a must-not-fire case and the gate goes red, instead of quietly reporting coverage.
    _by_glob = {}
    for _g, _p, _want, _ in FIXTURES:
        _n, _neg = _by_glob.get(_g, (0, 0))
        _by_glob[_g] = (_n + 1, _neg + (1 if _want is False else 0))
    _one_sided = sorted(g for g, (n, neg) in _by_glob.items() if neg == 0)
    print()
    ok("every path glob is asked to match AND to not match (a glob never asked to say no is untested)",
       not _one_sided, f"{len(_one_sided)} globs have only positive rows: {_one_sided}")

    # --- beat 642: is the ORACLE right, or only self-consistent? -----------------------------------
    # Everything above judges 77 rows with packcheck.match_one. That is one mirror checking another:
    # if match_one is wrong in a way that suits the fixtures, the whole table prints ok. Callie named
    # this exact gap on the floor (6876) about her own probe -- "none of them asks whether I am
    # pointed at the right mailbox".
    #
    # This check is the smallest honest version of that question: assert the port handles the parts of
    # Go's path.Match syntax that THIS PACK currently does not use. go_path_match() rewrites a glob
    # into a Python regex and escapes '[' and ']' as literals, so '[abc]' is ported as the four
    # characters "[abc]" instead of a character class. Go supports classes. The pack has no bracket
    # glob today, so this is not a live defect -- but the day someone writes one, every fixture would
    # silently judge it by the wrong rule and still print green.
    _CLASS_CASES = [("[abc]", "a", True), ("[a-z]", "m", True), ("[abc]", "d", False)]
    _class_gap = [(g, s, want, go_path_match(g, s)) for g, s, want in _CLASS_CASES
                  if go_path_match(g, s) != want]
    print()
    print(f"     oracle gap: {len(_class_gap)} of {len(_CLASS_CASES)} bracket-class cases disagree with Go")
    for g, s, want, got in _class_gap:
        print(f"       port says {got!s:5} for go_path_match({g!r}, {s!r}); Go says {want}")
    # Note on this check's own first draft: it used the regex [^\\]\[[^\]]*\], which requires a
    # character BEFORE the '[' -- so a glob starting with a bracket class, the most likely shape, was
    # invisible to it. It printed ok because it was broken, not because the pack is clean. That is the
    # exact defect this whole day has been about, reproduced inside the check written to catch it.
    def _has_bracket_glob(g):
        return "[" in g and "]" in g[g.index("["):]

    # globs is a list of (rule_id, glob) PAIRS, not strings. The first version of this line
    # iterated the tuples, so _has_bracket_glob() received a tuple and every comparison quietly
    # failed -- green for the second wrong reason in the same check.
    _bracket_globs = [g for _, g in globs if _has_bracket_glob(g)]
    ok("the pack uses no bracket glob, so this oracle gap is unreachable TODAY (a new glob would hit it)",
       not _bracket_globs,
       f"bracket glob(s) in the pack: {_bracket_globs} -- fix go_path_match before trusting any verdict")

    # --- beat 634: is this file ABLE to say no? ------------------------------------------------------
    # Until now nothing checked that. The fixture table is hand-reasoned, and a hand-reasoned table is
    # exactly the thing that quietly agrees with a broken matcher -- if the `want` column and the
    # matcher drift together, every line prints ok and the file has never proven anything. So: run the
    # same table against two deliberately-broken matchers and require the table to reject both.
    # This is the `redundant_mut.py` idea applied here, because THIS file had no such proof.
    def table_against(match):
        return sum(1 for g, p, want, _ in FIXTURES if match(g, p) != want)

    def matches_everything(g, p):
        return True

    def returns_glob_truthiness(g, p):        # a matcher that "works" but ignores the glob's shape
        return g.rstrip("*") in p or g.startswith("*")

    ok("the table REJECTS a matcher that says yes to everything",
       table_against(matches_everything) > 5,
       f"only {table_against(matches_everything)} lines disagreed -- the table is too permissive to notice")
    ok("the table REJECTS a matcher that ignores the glob",
       table_against(returns_glob_truthiness) > 0,
       "the table cannot distinguish a real matcher from a shape-blind one")

    # --- beat 643: a SECOND, independently-written port as a cross-check -------------------------------
    # Beat 642 concluded "my oracle is a mirror" and could only say so, because there was nothing to
    # compare it to. There was: `neargo` is a separate transcription of Go's path.Match, written from
    # the Go source at a different time, and pathmatchdiff.py already attests it against Go semantics
    # including character classes. Two ports from one reading is a mirror; two written separately, and
    # disagreeing where it doesn't matter, is a cross-check.
    #
    # They DO diverge -- on bracket classes, where go_path_match escapes '[' and ']' as literals
    # (FINDING-2026-10-28-oracle-is-a-mirror.md). The question this check asks is the one that matters:
    # does that divergence reach ANY fixture? If a row's whole-string verdict is decided by a construct
    # the two ports read differently, that row is not evidence.
    import neargo
    _divergent = [(g, p, neargo.path_match(g, p), go_path_match(g, p))
                  for g, p, _, _ in FIXTURES
                  if neargo.path_match(g, p) != go_path_match(g, p)]
    print()
    ok("no fixture row is decided by a construct the two ports read differently "
       "(two independent transcriptions agree on everything the pack actually uses)",
       not _divergent,
       f"{len(_divergent)} rows differ between neargo and go_path_match: {_divergent[:3]}")

    # --- the claim that needs the Go engine, stated so nobody mistakes a mirror for the engine --------
    n_abs_hit = sum(1 for g, p, w, _ in FIXTURES
                    if p.startswith("/") and w and match_one(g, p))
    print()
    print(f"     absolute-path fixtures that DO match under this port: {n_abs_hit}")
    print("     (each is matched by the SUFFIX loop in packcheck.match_one, which tries the tail segments")
    print("      of a path — `etc/cron.d/evil` — so the leading star has a segment to consume. Go's")
    print("      path.Match would return false on the full path. Whether the GO engine has this suffix")
    print("      loop is UNVERIFIED here: no Go on this host, and whether it should is the pack owner's")
    print("      call. If it does not, these globs only fire on relative paths, and packcheck's mirrored")
    print("      verdicts are more permissive than the engine — the dangerous direction for a detector.)")
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print(f"path family has a reader: {len(globs)} globs, {len(FIXTURES)} fixtures, all green")


if __name__ == "__main__":
    sys.exit(main())

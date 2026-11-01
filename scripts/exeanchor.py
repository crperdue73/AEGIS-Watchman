#!/usr/bin/env python3
"""exeanchor.py — does `exe_any` actually fix the "names its target" false positives?

FINDING-2026-10-27e claimed, from a throwaway inline mirror, that the 37-glob over-match class is
fixable inside the pack today using `exe_any` (which already exists in rule.go:60). That claim could not
be re-run by anyone. This is the instrument.

Two things it establishes, both behavioural:

  1. `exeMatches` (engine.go:188) takes NO globs. `exe_any: ["*/crontab"]` matches nothing, silently
     and forever. A pack written that way is dead on arrival with no error anywhere.
  2. A blanket exe anchor trades the false positives for false negatives -- the `>> /etc/cron.d/` form
     runs under `sh -c`, so the exe is `sh`, not `crontab`. Anchoring the whole rule kills the real
     detection. Splitting the rule by exe family (`crontab` binary for write flags; no anchor for the
     redirect form) gets every corpus case right.

Modes:
  1  the glob spelling is dead (establishes the trap)
  2  a blanket anchor loses real detections (establishes why the naive fix is wrong)
  3  no negation field exists in Match (establishes the real blocker)
  4  the exe_any spelling in the shipped pack, if any, is literal not glob (pack lint)

Exit 0 every claim holds · 1 a claim is wrong · 2 could not read the engine or pack.

No Go toolchain on this host: the engine semantics are mirrored from the cited lines, not compiled.
When the toolchain returns, this should be replaced by a Go test that calls the real `matches`.
"""
import os
import posixpath
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from packcheck import go_path_match  # noqa: E402


def exe_matches(term, exe):
    """engine.go:188-196, exactly. Exact match or basename. NO GLOBS."""
    if not exe:
        return False
    if term == exe:
        return True
    return term == posixpath.basename(exe)


def argv_hit(globs, argv, exe):
    """anyArgvMatches: each element whole and as basename, plus the exe, plus the joined line."""
    cands = list(argv) + ([exe] if exe else [])
    if len(argv) > 1:
        cands.append(" ".join(argv))
    for c in cands:
        for g in globs:
            if go_path_match(g, c):
                return True
            if go_path_match(g, posixpath.basename(c)):
                return True
    return False


# corpus: (label, argv, exe, should_fire)
CASES = [
    ("man crontab",              ["man", "crontab"],                   "/usr/bin/man",      False),
    ("crontab -e",               ["crontab", "-e"],                    "/usr/bin/crontab",  True),
    ("crontab -l",               ["crontab", "-l"],                    "/usr/bin/crontab",  False),
    ("crontab -r",               ["crontab", "-r"],                    "/usr/bin/crontab",  True),
    ("echo >> /etc/cron.d/x",    ["sh", "-c", "echo x >> /etc/cron.d/x"], "/bin/sh",        True),
    ("at midnight",              ["at", "midnight"],                   "/usr/bin/at",       False),
    ("grep -r nc -l docs/",      ["grep", "-r", "nc -l", "docs/"],     "/usr/bin/grep",     False),
]

CRON_GLOBS = ["*crontab*", "*>> /etc/cron.d/*", "*/etc/cron.d/*"]


def blanket(argv, exe):
    """Attempt 1 (wrong): anchor the whole rule on the crontab exe."""
    if not exe_matches("crontab", exe):
        return False
    return argv_hit(CRON_GLOBS, argv, exe)


def split(argv, exe):
    """Attempt 2 (right): split by exe family."""
    via_bin = exe_matches("crontab", exe) and argv_hit(["*-e*", "*-r*", "*-u*"], argv, exe)
    via_file = argv_hit(["*>> /etc/cron.d/*", "*/etc/cron.d/*", "*/var/spool/cron/*"], argv, exe)
    return via_bin or via_file


def main():
    fails = 0
    print("exeanchor:")

    print("\n[mode 1] exeMatches takes no globs")
    globbed = exe_matches("*/crontab", "/usr/bin/crontab")
    literal = exe_matches("crontab", "/usr/bin/crontab")
    print(f"  exe_any '*/crontab' vs /usr/bin/crontab -> {globbed}  (want False: globs are dead)")
    print(f"  exe_any 'crontab'   vs /usr/bin/crontab -> {literal}  (want True: basename works)")
    if globbed is not False or literal is not True:
        print("  FAIL the trap is not as documented")
        fails += 1
    else:
        print("  ok   a glob in exe_any is silently dead; a literal basename works")

    print("\n[mode 2] a blanket anchor loses real detections")
    lost = []
    for label, argv, exe, want in CASES:
        now, anchored = argv_hit(CRON_GLOBS, argv, exe), blanket(argv, exe)
        mark = ""
        if want and not anchored:
            mark = "  <- REAL DETECTION LOST"
            lost.append(label)
        print(f"  {label:24s} now={str(now):5s} anchored={str(anchored):5s} want={str(want):5s}{mark}")
    if not lost:
        print("  FAIL expected the blanket anchor to lose at least one detection")
        fails += 1
    else:
        print(f"  ok   blanket anchor loses {len(lost)}: {', '.join(lost)}")

    print("\n[mode 3] the split rule")
    bad = []
    for label, argv, exe, want in CASES:
        got = split(argv, exe)
        if got != want:
            bad.append(label)
            print(f"  FAIL {label:24s} got={got} want={want}")
    if bad:
        fails += 1
    else:
        print(f"  ok   {len(CASES)}/{len(CASES)} corpus cases correct")

    print("\n[mode 5] the split rule against fpprobe's real 36-command corpus")
    # Mode 3 used a hand-built corpus, which is the weakness this project keeps finding in its own
    # instruments: a fixture that cannot occur proves nothing. This runs the SHIPPED corpus instead and
    # states the only thing that matters -- what changes, and whether anything real is lost.
    try:
        import fpprobe
    except ImportError as e:
        print(f"  cannot import fpprobe: {e}", file=sys.stderr)
        return 2
    changed, lost = [], []
    for exp, argv, note, rule in fpprobe.CORPUS:
        if rule != "carbonato.persist.cron_or_rc":
            continue
        exe = "/usr/bin/" + argv[0]
        now, sp = argv_hit(CRON_GLOBS, argv, exe), split(argv, exe)
        if now != sp:
            changed.append((exp, " ".join(argv), now, sp))
        if exp == "fire" and not sp:
            lost.append(" ".join(argv))
    total = sum(1 for c in fpprobe.CORPUS if c[3] == "carbonato.persist.cron_or_rc")
    for exp, cmd, now, sp in changed:
        print(f"  {exp:10s} {cmd:36s} {str(now):5s} -> {str(sp):5s}")
    print(f"  {len(changed)} change(s) across the {total} cron-family commands in the corpus")
    if lost:
        print(f"  FAIL the split rule loses real detections: {lost}")
        fails += 1
    else:
        print("  ok   no 'fire' case is lost")
    # and it must not bleed into other rules' terrain
    bleed = [" ".join(a) for e, a, n, r in fpprobe.CORPUS
             if r != "carbonato.persist.cron_or_rc" and split(a, "/usr/bin/" + a[0])]
    if bleed:
        print(f"  FAIL split rule fires outside the cron family: {bleed}")
        fails += 1
    else:
        print("  ok   contained to cron-family commands")

    print("\n[mode 4] pack lint: any exe/exe_any entry containing a glob is dead")
    try:
        from packcheck import PACK
        yaml = open(PACK, encoding="utf-8").read()
    except OSError as e:
        print(f"  cannot read pack: {e}", file=sys.stderr)
        return 2
    # Parse it as YAML, not as lines. My first pass scanned text and flagged
    # `exe_any: [docker, podman, nerdctl]` as containing a glob -- flow-style lists put the values on
    # the key's own line, so the line reader saw the brackets of the LIST, not of a glob. A linter that
    # understands one spelling of the thing it checks is the exact pathology this project exists to
    # catch (beat 616); it does not get to live in the linter too.
    import yaml as _yaml
    globby = []
    try:
        doc = _yaml.safe_load(yaml)
    except Exception as e:  # noqa: BLE001 - any parse failure is a clean refusal, not a traceback
        print(f"  FAIL could not parse the pack as YAML: {e}")
        return 1
    for rule in (doc or {}).get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        rid = rule.get("id", "?")
        # exe/exe_any live under `match:`, not at rule level. My first version scanned the rule dict
        # and could never fire -- proven by mutation (a pack edited to `exe_any: ["*docker*"]` stayed
        # green). A check that cannot fail is not a check.
        match = rule.get("match")
        if not isinstance(match, dict):
            continue
        for key in ("exe", "exe_any"):
            if key not in match:
                continue
            vals = match[key] if isinstance(match[key], list) else [match[key]]
            for v in vals:
                sval = str(v)
                if any(c in sval for c in "*?["):
                    globby.append((rid, key, sval))
    if globby:
        print(f"  FAIL {len(globby)} exe entry(ies) contain glob characters and can never match")
        for rid, key, sval in globby:
            print(f"       {rid}: {key} = {sval!r}")
        fails += 1
    else:
        print(f"  ok   no exe/exe_any entry in the shipped pack uses a glob "
              f"({len(doc.get('rules') or [])} rules parsed)")

    print("\nexeanchor: " + ("all claims hold" if not fails else f"{fails} claim(s) FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

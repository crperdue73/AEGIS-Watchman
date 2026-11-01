#!/usr/bin/env python3
"""packcover — does each rule fire for the RIGHT REASON?

WHY THIS EXISTS, AND WHY packcheck WASN'T ENOUGH
  packcheck (written a few hours earlier) asks: "is every glob fired by some sample?"
  That question is answerable and it found 4 dead globs. But it has a hole I walked
  into the same night, and it is the hole that matters:

    A rule fires if ANY of its globs matches. So a rule can be "covered" by one
    generic glob while every glob naming the thing it CLAIMS to catch is dead.

  Concretely: `persist.cron_or_rc` claims to cover /etc/cron.d and /etc/systemd/system.
  Both of those globs are dead. The rule still fires on `/etc/cron.d/evil` anyway —
  because `*crontab*`... no. Because the joined candidate string happens to match
  some other glob. The rule reports "detected", the analyst sees a hit, and the two
  mechanisms the rule is named for are unguarded. packcheck's per-glob view showed
  the dead globs; it could NOT show that the rule still "passes" its own samples.

  So this check asks the question that has teeth:
    For every path the rule's `description`/`tags` claim to cover, WHICH glob
    caught it? If the answer is "a glob for a different mechanism", that's a defect
    even though the rule fired.

WHAT IT DOES
  Each rule gets a COVERAGE list: (claimed path, the glob that SHOULD catch it).
  We fire the whole rule at the path and record which glob actually matched. PASS
  only if the intended glob is among the matchers. Anything else is a MISATTRIBUTION.

WHAT IT DOES NOT DO
  - Not the Go matcher; a port. Wrong port => wrong answer, both directions.
  - It does not know what the rule "should" cover beyond the list I wrote from its
    own description. If my list is wrong, this is wrong — but the list is short,
    visible, and arguable, which is better than a silent assumption.
  - It cannot tell you a path is unreachable in practice.

Exit: 0 every claim attributed to its named glob · 1 at least one misattribution
      · 2 could not parse (never a pass)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import go_path_match, match_one, parse_pack, PACK  # noqa: E402


# For each rule: (argv describing the attack, path-or-token that SHOULD match,
#                 substring identifying the glob that OUGHT to be doing the work)
CLAIMS = {
    "carbonato.persist.cron_or_rc": [
        ([ "/etc/cron.d/evil" ],              "/etc/cron",        "the cron.d drop"),
        ([ "/etc/cron.daily/backdoor" ],      "/etc/cron",        "the cron.daily drop"),
        ([ "/etc/systemd/system/x.service" ], "/etc/systemd",     "the systemd unit drop"),
        ([ "/var/spool/cron/root" ],          "/var/spool/cron",  "the spool crontab"),
        ([ "crontab", "-e" ],                 "crontab",          "the crontab command"),
        ([ "/home/u/.bashrc" ],            ".bashrc",          "the bash rc"),
    ],
    "carbonato.infra.privileged_container": [
        ([ "docker", "run", "-v", "/:/host", "img" ], "/:",         "the host-root bind mount"),
        ([ "docker", "run", "--privileged", "img" ],  "--privileged", "the privileged flag"),
    ],
    "carbonato.tool.package_install": [
        ([ "pip", "install", "evil" ],          "pip install",   "pip install"),
        ([ "npm", "install", "-g", "evil" ],    "npm install -g", "the global npm install"),
        ([ "curl", "-O", "/home/u/x" ],      "/home/",        "the download to home"),
    ],
    "carbonato.exec.ssh_key_heredoc": [
        (["cat", "/root/.ssh/id_rsa"],           ".ssh/id_",     "the private key read"),
        (["cat", "/home/u/.ssh/id_ed25519"],  ".ssh/id_",     "the ed25519 key read"),
        (["base64", "/root/.ssh/id_rsa"],        "id_rsa",       "the key through base64"),
        (["cat", "/root/.ssh/known_hosts"],      "known_hosts",  "known_hosts"),
    ],
    "carbonato.exec.obfuscation": [
        (["base64", "-d", "x"],                  "base64",       "base64"),
        (["xxd", "-r", "x"],                     "xxd",          "xxd"),
        (["openssl", "enc", "-aes256", "-in", "f"], "openssl enc", "openssl enc"),
        (["tar", "-czf", "x.tgz", "."],          "tar -cz",      "modern tar bundling"),
        (["tar", "czf", "x.tgz", "."],           "tar cz",       "old-style tar bundling"),
    ],
    "carbonato.net.new_listener": [
        (["nc", "-l", "-p", "4444"],             "nc -l",        "netcat listener"),
        (["ncat", "-l", "4444"],                 "ncat -l",      "ncat listener"),
        (["socat", "TCP-L:4444", "-"],           "socat",        "socat listener"),
        (["python3", "-m", "http.server", "8080"], "http.server", "the python http listener"),
        (["nc", "--listen", "4444"],             "--listen",     "the --listen long form"),
    ],
    "carbonato.persist.survives_session": [
        ([ "nohup", "./x" ],      "nohup",      "nohup"),
        ([ "setsid", "./x" ],     "setsid",     "setsid"),
    ],
}


def globs_for(rid, pack_text):
    for r, gl in parse_pack(pack_text):
        if r == rid:
            return gl
    return None


def candidates(argv, exe=""):
    c = list(argv)
    if exe:
        c.append(exe)
    if len(argv) > 1:
        c.append(" ".join(argv))
    return c


def main():
    try:
        text = open(PACK, encoding="utf-8").read()
    except OSError as e:
        print(f"packcover: cannot read pack: {e}", file=sys.stderr)
        return 2

    if not parse_pack(text):
        print("packcover: parsed 0 rules — pack unreadable", file=sys.stderr)
        return 2

    fails = 0
    checked = 0
    all_rules = [r for r, _ in parse_pack(text)]

    # A rule with no CLAIMS entry is UNCHECKED, not covered. Before this guard,
    # packcover checked 4 of 7 rules and printed a confident "13 claims, 6
    # misattributed" — the same defect it exists to catch, one level up: the
    # checker's own coverage was the thing nobody was measuring.
    unchecked = [r for r in all_rules if r not in CLAIMS]
    if unchecked:
        print("UNCHECKED RULES (no claims recorded — claims must be added, not assumed absent):")
        for r in unchecked:
            print(f"  ?  {r}")
        print("  an unchecked rule is not a passing rule\n")
        # An unchecked rule must move the exit code, or the warning is decoration.
        fails += len(unchecked)

    for rid, claims in CLAIMS.items():
        globs = globs_for(rid, text)
        if globs is None:
            # Rule absent from this pack (synthetic tests, partial packs). Neutral:
            # it must not turn a correct pack red, and must not read as covered.
            print(f"\nSKIP {rid}: not in pack (claims unverified, not counted)")
            continue
        print(f"\n{rid}")
        for argv, marker, label in claims:
            checked += 1
            matchers = [g for g in globs
                        if any(match_one(g, c) for c in candidates(argv))]
            intended = [g for g in matchers if marker.lstrip("*") in g]
            if intended:
                print(f"  ok    {label:26} caught by {intended[0]!r}")
            elif matchers:
                print(f"  WRONG {label:26} fires, but via {matchers} — NOT {marker!r}")
                print(f"        the glob meant to cover this ({marker!r}) is dead; "
                      f"the rule looks armed for {label} and is not")
                fails += 1
            else:
                print(f"  MISS  {label:26} nothing catches it at all")
                fails += 1

    print()
    print(f"packcover: {checked} claims checked across {len(CLAIMS) - len([r for r in CLAIMS if r not in all_rules])} rules, "
          f"{fails} misattributed or missed, {len(unchecked)} rule(s) unchecked")
    if fails:
        print("  a rule that fires for the wrong reason is a rule that will be trusted for it")
        return 1
    print("  every claim attributed to the glob that names it")
    return 0


if __name__ == "__main__":
    sys.exit(main())

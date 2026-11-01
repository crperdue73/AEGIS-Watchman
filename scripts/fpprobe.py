#!/usr/bin/env python3
"""fpprobe — measure the FALSE POSITIVE side of the rule pack.

WHY
  packcheck asks "does every glob fire?"  (dead-glob misses)
  packcover asks "does the RIGHT glob fire?"  (misattribution)
  Neither asks the question that decides whether an operator keeps reading:
  DOES IT FIRE ON ORDINARY COMMANDS?

  Measured 2026-10-23: `man crontab` trips persist.cron_or_rc at severity HIGH,
  and `echo "nc -l is a real tool"` trips net.new_listener. Both at the whole
  tier. The rule is doing what it says about the wrong thing.

WHAT
  A corpus of commands an agent legitimately runs, each labelled:
    quiet  — a correct pack must NOT fire
    fire   — a correct pack MUST fire (real attacks; keeps this honest the same
             way packcheck's PASS cases keep it honest — a probe of only
             "shouldn't fire" is satisfied by a pack that never fires at all)
  Reports precision per the corpus, and every unexpected result with the tier
  that produced it.

HONEST LIMITS
  - Python port of the matcher, not the Go code.
  - The corpus is 40 lines I wrote. It is a probe, not a benchmark. A rate from
    it is a property of this list.
  - "Benign" is my judgement. `pip install requests` IS an install; whether it
    should alert is a product decision, not a fact. Those cases are labelled
    `judgement` and reported separately rather than scored as errors.

Exit: 0 no unexpected results · 1 at least one · 2 could not parse the pack.
"""
import os
import posixpath
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import go_path_match, parse_pack, suffixes, PACK  # noqa: E402


def tier(glob, s):
    """Which of the four tiers fired. Mirrors matchOne, in order."""
    if go_path_match(glob, s):
        return "whole"
    for suf in suffixes(s):
        if go_path_match(glob, suf):
            return "suffix"
    if go_path_match(glob, posixpath.basename(s)):
        return "basename"
    core = glob.strip("*")
    if core and not any(c in core for c in "*?[") and core in s:
        return "contains"
    return None


# (label, argv, expectation, note)
#   quiet      correct pack fires NOTHING
#   fire       correct pack fires SOMETHING  (the alarm must work)
#   judgement  firing is arguable — reported, never scored
CORPUS = [
    ("quiet", ["cat", "/etc/hosts"],                       "reading hosts", None),
    ("quiet", ["ls", "/var/log"],                          "listing logs", None),
    ("quiet", ["man", "crontab"],                          "READING the manual", "carbonato.persist.cron_or_rc"),
    ("quiet", ["man", "nc"],                               "READING the manual", "carbonato.persist.cron_or_rc"),
    ("quiet", ["grep", "-r", "nc -l", "docs/"],            "searching for a phrase", "carbonato.net.new_listener"),
    ("quiet", ["vim", "notes.md"],                         "editing notes", None),
    ("quiet", ["git", "status"],                           "vcs", None),
    ("quiet", ["make", "test"],                            "build", None),
    ("quiet", ["npm", "run", "build"],                     "build", None),
    ("quiet", ["docker", "ps"],                            "listing containers", None),
    ("quiet", ["docker", "run", "-v", "/data:/data", "i"], "ordinary mount", "carbonato.infra.privileged_container"),
    ("quiet", ["scp", "backup.tar.gz", "host:/srv/"],      "offsite copy", None),
    ("quiet", ["systemctl", "status", "nginx"],            "reading status", None),
    ("quiet", ["journalctl", "-u", "nginx"],               "reading logs", None),
    ("quiet", ["printf", "%s", "base64 is a codec"],       "naming a codec", "carbonato.exec.obfuscation"),
    ("quiet", ["curl", "-s", "http://localhost:8080/api"], "calling own api", None),
    ("quiet", ["openssl", "version"],                      "version check", None),
    ("quiet", ["find", "/", "-name", "id_rsa"],            "FINDING a key", "carbonato.exec.ssh_key_heredoc"),
    ("quiet", ["ps", "aux"],                               "process list", None),
    ("quiet", ["kill", "-9", "1234"],                      "killing own job", None),
    ("quiet", ["echo", "hello"],                           "trivial", None),

    ("judgement", ["pip", "install", "requests"],          "legit install — arguably fine", "carbonato.tool.package_install"),
    ("judgement", ["pip", "list"],                         "listing packages", "carbonato.tool.package_install"),
    ("judgement", ["npm", "install", "-g", "@types/node"], "legit global install", "carbonato.tool.package_install"),
    ("judgement", ["crontab", "-l"],                       "LISTING crontab, not writing", "carbonato.persist.cron_or_rc"),
    ("judgement", ["tar", "-czf", "backup.tar.gz", "docs/"], "ordinary backup", "carbonato.exec.obfuscation"),
    ("judgement", ["ssh", "-i", "/home/u/.ssh/id_ed25519", "host"], "own key, own host", "carbonato.exec.ssh_key_heredoc"),

    ("fire", ["cat", "/root/.ssh/id_rsa"],                 "reading root private key", "carbonato.exec.ssh_key_heredoc"),
    ("fire", ["nc", "-l", "-p", "4444"],                   "opening a listener", "carbonato.net.new_listener"),
    ("fire", ["ncat", "-l", "4444"],                       "opening a listener", "carbonato.net.new_listener"),
    ("fire", ["base64", "-d", "payload.b64"],              "decoding a payload", "carbonato.exec.obfuscation"),
    ("fire", ["docker", "run", "--privileged", "img"],     "privileged container", "carbonato.infra.privileged_container"),
    ("fire", ["nohup", "./implant"],                       "surviving the session", "carbonato.persist.survives_session"),
    ("fire", ["echo", "x", ">>", "/etc/cron.d/evil"],      "the cron drop", "carbonato.persist.cron_or_rc"),
    ("fire", ["openssl", "enc", "-aes256", "-in", "f"],    "encrypting a payload", "carbonato.exec.obfuscation"),
    ("fire", ["socat", "TCP-L:4444", "-"],                 "opening a listener", "carbonato.net.new_listener"),
]


def main():
    try:
        pack = open(PACK, encoding="utf-8").read()
    except OSError as e:
        print(f"fpprobe: cannot read pack: {e}", file=sys.stderr)
        return 2
    rules = parse_pack(pack)
    if not rules:
        print("fpprobe: parsed 0 rules — pack unreadable", file=sys.stderr)
        return 2

    # A pack holding a SUBSET of the rules cannot be scored on attacks it never
    # claimed to cover. Score a MUST-FIRE line only when its owning rule is
    # present; otherwise it is unverified, not missed. Without this the tool is a
    # permanent veto on any partial pack — found by its own first test run.
    present = {rid for rid, _ in rules}

    bad = []
    skipped = []
    counts = {"quiet": [0, 0], "fire": [0, 0], "judgement": [0, 0]}
    for label, argv, note, owner in CORPUS:
        cands = list(argv) + [" ".join(argv)]
        found = []
        for rid, globs in rules:
            for c in cands:
                for g in globs:
                    t = tier(g, c)
                    if t:
                        found.append((rid, g, t, c))
        if label == "fire" and owner and owner not in present:
            skipped.append((argv, note))
            continue
        ok = (label == "quiet" and not found) or (label == "fire" and found)
        if label != "judgement":
            counts[label][0] += 1
            if ok:
                counts[label][1] += 1
        if label == "quiet" and found:
            bad.append((label, argv, note, found))
            for rid, g, t, c in found[:2]:
                print(f"  NOISE  {' '.join(argv):42} {note:26} -> {rid} {g!r} tier={t}")
        elif label == "fire" and not found:
            bad.append((label, argv, note, found))
            print(f"  MISS   {' '.join(argv):42} {note:26} -> nothing fired")
        elif label == "judgement" and found:
            rid, g, t, c = found[0]
            print(f"  note   {' '.join(argv):42} {note:26} -> {rid} (arguable)")

    print()
    for label in ("quiet", "fire"):
        n, ok = counts[label]
        print(f"  {label:6} {ok}/{n} as expected")
    if skipped:
        print(f"  fire   {len(skipped)} case(s) unverified — owning rule absent from this pack")
    # The four NOISE lines below are ONE documented defect, not four: a glob fires on a command that
    # merely NAMES its target (docs/FINDING-2026-10-27b -- 37 globs / 7 rules). They are NOT pinned to
    # green here. Pinning them needed a pack-size heuristic and a rule-identity table to stop the
    # SYNTHETIC pack in fpprobe_test.sh from going un-failable, and a guard that needs two heuristics to
    # stay honest is a guard that will lie. The suite stays red on purpose and the count is carried by
    # scripts/nameprobe.py, which pins the class at the PACK level where the defect actually lives.
    print(f"\nfpprobe: {len(bad)} unexpected result(s) across {len(CORPUS)} commands")
    if bad:
        print("  all noise here should be the 'names its target' class: see FINDING-2026-10-27b")
        print("  the class is pinned at the pack level by scripts/nameprobe.py (37 globs / 7 rules)")
        return 1



if __name__ == "__main__":
    sys.exit(main())

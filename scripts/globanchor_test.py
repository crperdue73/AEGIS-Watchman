#!/usr/bin/env python3
"""globanchor_test.py — pins the anchoring property of every glob in the shipped pack.

WHY
  Beat 580 measured that in this matcher a glob with a leading `*/` is anchored to a path segment
  boundary, and one without it matches the literal anywhere. `*id_rsa*` fires on
  `/home/x/notid_rsabackup.txt`; `*credentials*` fires on `/app/config/credentials.py`. Both are on
  CRITICAL secret rules.

  The pack is not mine to change. What IS mine is making sure the inventory can't drift: this test
  classifies every path-shaped glob as anchored or unanchored, asserts the exact set of unanchored
  ones, and proves each named false positive is real. If someone adds an unanchored infix to a path
  rule, this goes red and they have to say so on purpose.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import neargo                                                # noqa: E402

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


def main():
    with open(os.path.join(PROJ, "rules", "carbonato.yaml"), encoding="utf-8") as fh:
        pack = fh.read()

    # --- 1. the anchoring property itself, on literals, with controls -------------------------------
    ok("anchored glob requires the segment: '*/.ssh/*' misses a bare 'id_rsa'",
       neargo.match_one("*/.ssh/*", "id_rsa") is False)
    ok("anchored glob matches a real path: '*/.ssh/*' hits '/home/x/.ssh/id_rsa'",
       neargo.match_one("*/.ssh/*", "/home/x/.ssh/id_rsa") is True)
    ok("unanchored infix hits a longer name: '*id_rsa*' hits '/home/x/notid_rsabackup.txt'",
       neargo.match_one("*id_rsa*", "/home/x/notid_rsabackup.txt") is True)
    ok("  control: the anchored form does NOT: '*/.ssh/*' misses that path",
       neargo.match_one("*/.ssh/*", "/home/x/notid_rsabackup.txt") is False)

    # --- 2. the measured false positives, pinned ---------------------------------------------------
    FALSE_POSITIVES = {
        "*id_rsa*": ["/srv/backup/id_rsa.tar.gz", "/home/x/notid_rsabackup.txt"],
        "*credentials*": ["/app/config/credentials.py", "/tmp/test_credentials.json",
                          "/var/log/credentials_rotation.log"],
        "*/.netrc*": ["/home/x/.netrc.example"],
        "*/.git-credentials*": ["/opt/.git-credentials.example"],
    }
    for glob, paths in FALSE_POSITIVES.items():
        for path in paths:
            ok(f"false positive pinned: {glob} fires on {path}", neargo.match_one(glob, path) is True)

    # --- 3. the exact set of unanchored path-infixed globs in the pack -------------------------------
    # Unanchored = starts with '*' and the char after is NOT '/'. Endpoint globs (hostnames) and argv
    # globs are exempt by design: a hostname/token has no segments, so substring IS the right semantics.
    ENDPOINT_RULES = {"carbonato.exfil.egress_after_change",
                      "carbonato.exfil.egress_after_identity_change",
                      "carbonato.c2.blockchain_rpc"}
    ARGV_RULES = {"carbonato.tool.package_install", "carbonato.exec.obfuscation",
                  "carbonato.persist.cron_or_rc", "carbonato.infra.privileged_container",
                  "carbonato.net.new_listener", "carbonato.exec.ssh_key_heredoc",
                  "carbonato.persist.survives_session"}

    blocks = re.split(r"\n  - id: ", pack)
    unanchored_path = {}
    for blk in blocks[1:]:
        rid = blk.split("\n")[0].strip()
        if rid in ENDPOINT_RULES or rid in ARGV_RULES:
            continue
        parts = blk.split("\n    match:")
        match = ("match:" + parts[1]) if len(parts) > 1 else ""
        if "path_glob_any" not in match:
            continue
        for g in re.findall(r'^\s+- "([^"]*)"\s*$', match, re.M):
            # Unanchored means the glob's FIRST wildcard is still hunting for a '/'. A glob like '*.env'
            # that starts with a literal '.' is a suffix match anchored at end-of-string — correct for a
            # path. I classified it as unanchored on the first run; the assertion refused my classification.
            core = g.lstrip("*")
            if core and not core.startswith("/") and not core.startswith("."):
                unanchored_path.setdefault(rid, []).append(g)

    expected = {
        "carbonato.secret.read_order_follows_constraint": ["*id_rsa*", "*id_ed25519*", "*credentials*"],
        "carbonato.secret.read_after_shell": ["*id_rsa*", "*id_ed25519*"],
        # '*/openai*key*' is ANCHORED (char after '*' is '/'). Only '*anthropic*key*' is not.
        "carbonato.secret.read_model_key": ["*anthropic*key*"],
    }
    got = {k: sorted(v) for k, v in unanchored_path.items()}
    ok(f"the unanchored path-glob inventory is exactly as measured (got {got})",
       got == {k: sorted(v) for k, v in expected.items()})

    # --- 3b. '*.env' is FINE — pinned so nobody "fixes" it --------------------------------------
    ok("'*.env' correctly misses '/app/environment.md'", neargo.match_one("*.env", "/app/environment.md") is False)
    ok("'*.env' correctly misses '/etc/environment'", neargo.match_one("*.env", "/etc/environment") is False)
    ok("'*.env' correctly hits '/home/x/.env'", neargo.match_one("*.env", "/home/x/.env") is True)

    # --- 4. the trailing-star drift between the two persistence rules ------------------------------
    def globs_for(rid):
        for blk in blocks[1:]:
            if blk.split("\n")[0].strip() == rid:
                parts = blk.split("\n    match:")
                return re.findall(r'^\s+- "([^"]*)"\s*$', ("match:" + parts[1]) if len(parts) > 1 else "", re.M)
        return []


    cron = globs_for("carbonato.persist.cron_or_rc")
    fw = globs_for("carbonato.persist.file_write")
    SHARED = [".bashrc", ".bash_profile", ".zshrc", ".profile"]


    def form(globs, name):
        for g in globs:
            if g.rstrip("*").endswith(name):
                return g
        return None


    drift = [(n, form(cron, n), form(fw, n)) for n in SHARED if form(cron, n) != form(fw, n)]
    ok(f"the two persistence rules still disagree on these shared files (drift recorded: {drift})",
       len(drift) == 3)
    ok("  ...and '.profile' is written WITHOUT a trailing star in BOTH (the odd one out)",
       form(cron, ".profile") == "*/.profile" and form(fw, ".profile") == "*/.profile")

    print(f"\nglobanchor_test: {ok_n} ok, {bad_n} fail")
    sys.exit(1 if bad_n else 0)


if __name__ == "__main__":
    main()

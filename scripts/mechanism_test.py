#!/usr/bin/env python3
"""mechanism_test.py — pins the four match candidates and the type gate that bounds them.

WHY
  Beats 577-580 kept discovering that the matcher's behaviour depended on which candidate form fired:
  whole string, trailing suffix, basename, or substring. I had catalogued three and there are four.
  Each new one produced a new false positive that I explained differently.

  The basename branch is DEAD CODE (subsumed by the suffix list), so there are three live candidates,
not four. The suffix candidate is the most alarming: a bind spec `/data:/x/.ssh/id_rsa`
  yields basename 'id_rsa', so a critical secret glob matches a string that is not a path at all.

  It is currently harmless ONLY because the secret rules are `type: file.access` and binds only appear
  on `container.start`. That invariant is not written down anywhere and nothing enforces it. This test
  writes it down and enforces it.

  If someone adds a rule with a path-shaped detail_glob and no type gate, this goes red.
"""
import os
import posixpath
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
    # --- 1. each candidate independently, on literals, with a control -------------------------------
    # candidate 1: whole string
    ok("candidate 1 (whole string): '*/etc/crontab' matches 'x:/etc/crontab'",
       neargo.match_one("*/etc/crontab", "x:/etc/crontab") is True)
    # candidate 2: suffix split
    ok("candidate 2 (suffix): '*:/host*' reaches '/:/host' only via a '/'-suffix",
       neargo.match_one("*:/host*", "/:/host") is True)
    # candidate 3: the basename branch is DEAD CODE, not a fourth mechanism.
    # pathSuffixes already yields the basename as its last element whenever s contains '/', and when s has
    # no '/' the whole-string candidate covers it. Proven by mutation: replacing base with the whole string
    # changes nothing. Pinning the SUBSUMPTION so nobody adds a fixture "for the basename case".
    s_bind = "/data:/home/x/.ssh/id_rsa"
    ok("pathSuffixes already yields the basename as its final element",
       neargo.path_suffixes(s_bind)[-1] == posixpath.basename(s_bind) == "id_rsa")
    ok("  ...so '*id_rsa*' matching the bind spec is candidate 2, not a basename branch",
       neargo.match_one("*id_rsa*", s_bind) is True)
    ok("  and for a slashless string the whole string IS the basename (candidate 1)",
       neargo.path_suffixes("id_rsa") == ["id_rsa"])
    # candidate 4: substring for globs with no interior '/'
    ok("candidate 4 (substring): '*slack.com*' matches a full URL",
       neargo.match_one("*slack.com*", "https://hooks.slack.com/services/T/B") is True)
    ok("  control: a path-shaped glob does NOT get the substring fallback",
       neargo.match_one("*/.ssh/*", "xx.ssh/yy") is False)

    # --- 2. the three secret rules are file.access, and binds only ride container.start ------------
    with open(os.path.join(PROJ, "rules", "carbonato.yaml"), encoding="utf-8") as fh:
        pack = fh.read()

    blocks = re.split(r"\n  - id: ", pack)


    def rule_type(rid):
        for blk in blocks[1:]:
            if blk.split("\n")[0].strip() == rid:
                parts = blk.split("\n    match:")
                m = ("match:" + parts[1]) if len(parts) > 1 else ""
                t = re.search(r"^\s{6}type:\s*(\S+)", m, re.M)
                return t.group(1) if t else None
        return None


    SECRET_RULES = [
        "carbonato.secret.read_order_follows_constraint",
        "carbonato.secret.read_after_shell",
        "carbonato.secret.read_model_key",
        "carbonato.persist.file_write",
    ]
    for rid in SECRET_RULES:
        t = rule_type(rid)
        ok(f"{rid.split('.')[-1]} is type file.access or file.write (got {t})",
           t in ("file.access", "file.write"))
    ok("the binds rule is type container.start (got %r)" % rule_type("carbonato.container.host_root_mount"),
       rule_type("carbonato.container.host_root_mount") == "container.start")

    # --- 3. nothing in the pack uses detail_glob_any without a type gate ---------------------------
    ungated = []
    for blk in blocks[1:]:
        rid = blk.split("\n")[0].strip()
        parts = blk.split("\n    match:")
        m = ("match:" + parts[1]) if len(parts) > 1 else ""
        if "detail_glob_any" in m and not re.search(r"^\s{6}type:", m, re.M):
            ungated.append(rid)
    ok(f"every detail_glob_any rule is type-gated (ungated: {ungated})", ungated == [])

    # --- 4. the value side: exactly one writes a separator-bearing string --------------------------
    with open(os.path.join(PROJ, "internal", "telemetry", "docker.go"), encoding="utf-8") as fh:
        docker_go = fh.read()
    with open(os.path.join(PROJ, "internal", "telemetry", "filewatch.go"), encoding="utf-8") as fh:
        fw_go = fh.read()
    ok("docker.go tags its action ContainerStart, not file.access",
       "Type: ContainerStart" in docker_go and "FileWrite" not in docker_go.split("Type: ContainerStart")[0][-200:])
    ok("filewatch.go tags its action FileWrite", "Type: FileWrite" in fw_go)
    ok("docker.go is the only telemetry writing joins of binds",
       docker_go.count("strings.Join(") == 1 and fw_go.count("strings.Join(") == 0)

    print(f"\nmechanism_test: {ok_n} ok, {bad_n} fail")
    sys.exit(1 if bad_n else 0)


if __name__ == "__main__":
    main()

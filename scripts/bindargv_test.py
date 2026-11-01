#!/usr/bin/env python3
"""bindargv_test.py — the privileged-container rule's bind leg works, and stays narrow.

HISTORY: rules/carbonato.yaml shipped `*-v /*:/` in carbonato.infra.privileged_container. It has an
INTERIOR star, so substringNeedle refuses it (it trims one leading and one trailing star, then rejects
a core still containing `*?[`). With no needle, the only route left is path.Match, where `*` never
crosses `/` -- and a joined command line is full of slashes. It had never fired. Nobody noticed,
because a glob that never fires emits no failing check; that is exactly what rule.go calls worse than
a startup error. Found beat 611, fixed beat 613.

The fix is `*-v /:*`, whose needle is `-v /:`. That targets SOURCE exactly "/" -- the host root --
which is the case the rule is actually about. It is *narrower* than the intent-free original, not just
wider: the first replacement I tried, `*-v /*`, fired on EVERY absolute-source bind including
/etc/localtime and /app:/app, which in a privileged-container rule is a false-positive machine.
Measured before shipping; that is why this file tests SILENCE as hard as it tests firing.

This suite replaced deadglob_test.py when the pack was fixed. The tombstone did its job: it pinned a
defect, then failed loudly when the defect was gone.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import neargo  # noqa: E402

PACK = os.path.join(HERE, os.pardir, "rules", "carbonato.yaml")
RULE = "carbonato.infra.privileged_container"
BIND_GLOB = "*-v /:*"

FAILS = []


def check(name, got, want):
    if got == want:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}: got {got!r} want {want!r}")
        FAILS.append(name)


def rule_argv_globs():
    import yaml
    pack = yaml.safe_load(open(PACK))
    r = [x for x in pack["rules"] if x["id"] == RULE][0]
    return r["match"]["argv_any"]


def main():
    globs = rule_argv_globs()

    print("--- 1. the rule ships a bind leg that CAN fire")
    check(f"'{BIND_GLOB}' is in {RULE}", BIND_GLOB in globs, True)
    check("the old dead glob is gone", "*-v /*:/" in globs, False)
    check(f"'{BIND_GLOB}' carries a needle", neargo.substring_needle(BIND_GLOB), "-v /:")

    print()
    print("--- 2. it FIRES on host-root mounts (the escape it exists to catch)")
    for a in (["docker", "run", "-v", "/:/host", "evil"],
              ["docker", "run", "-v", "/:/data", "x"],
              ["podman", "run", "-v", "/:/mnt", "x"],
              ["nerdctl", "run", "--privileged", "-v", "/:/host", "x"]):
        check(f"fires: {' '.join(a)[:44]}", neargo.any_argv(globs, a), True)

    print()
    print("--- 3. it stays SILENT on ordinary binds (no false-positive machine)")
    for a in (["docker", "run", "-v", "/etc/localtime:/etc/localtime:ro", "nginx"],
              ["docker", "run", "-v", "/var/lib/pg:/var/lib/pg", "postgres"],
              ["docker", "run", "-v", "/app:/app", "app"],
              ["docker", "run", "-v", "/etc/nginx/nginx.conf:/etc/nginx/nginx.conf:ro", "nginx"],
              ["docker", "run", "-v", "data:/data", "app"]):
        check(f"silent: {' '.join(a)[:42]}", neargo.any_argv(globs, a), False)

    print()
    print("--- 4. the bind leg is load-bearing: remove it and host-root mounts are missed")
    without = [g for g in globs if g != BIND_GLOB]
    check("without the bind leg, '-v /:/host' is MISSED",
          neargo.any_argv(without, ["docker", "run", "-v", "/:/host", "evil"]), False)
    check("the other legs still cover --privileged",
          neargo.any_argv(without, ["docker", "run", "--privileged", "x"]), True)

    print()
    if FAILS:
        print(f"bindargv_test: {len(FAILS)} failure(s).")
        return 1
    print("bindargv_test: all ok -- bind leg fires on host-root mounts and stays silent on benign ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

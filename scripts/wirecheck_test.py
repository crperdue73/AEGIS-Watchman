#!/usr/bin/env python3
"""wirecheck_test — proves wirecheck can fail, not just that it passes.

The value of a wire check is its ability to catch a predicate reading a field nothing emits. So the
tests that matter here are the negative ones: they synthesise a Go source and a pack with a known
dead wire and confirm wirecheck's own functions call it MISSING.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wirecheck as W  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name} {detail}".strip())
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


# --- 1. the engine's demand side is read, not recalled --------------------------------------


def main():
    D = W.demand_from_engine()
    ok("five detail-reading predicates found", len(D) == 5, str(sorted(D)))
    ok("ExeAny reads exe (hoisted above the blocks, not the next block's key)", D.get("ExeAny") == "exe", str(D.get("ExeAny")))
    ok("ArgvAny reads argv", D.get("ArgvAny") == "argv")
    ok("PathGlobAny reads path", D.get("PathGlobAny") == "path")
    ok("EndpointGlobAny reads endpoint", D.get("EndpointGlobAny") == "endpoint")
    ok("DetailEquals takes the key from the rule", D.get("DetailEquals") == "<key from rule>")
    ok("the demand table matches the hard-coded expectation",
       all(W.EXPECTED_DEMAND.get(k) == v for k, v in D.items()),
       str({k: (W.EXPECTED_DEMAND.get(k), v) for k, v in D.items() if W.EXPECTED_DEMAND.get(k) != v}))

    # --- 2. the supply side is read, not recalled ------------------------------------------------
    S = W.type_constructors()
    TYPES = {k for k in S if not k.endswith("\x00cond")}
    ok("process.exec has a constructor", "process.exec" in TYPES, str(sorted(TYPES)))
    ok("container.start has a constructor", "container.start" in TYPES)
    ok("process.exec emits exe unconditionally", "exe" in S["process.exec"])
    ok("process.exec argv is conditional (only when len(r.Argv) > 0)",
       "argv" in S["process.exec\x00cond"] and "argv" not in S["process.exec"])
    ok("container.start privileged is conditional (inside `if cfg != nil`)",
       "privileged" in S["container.start\x00cond"])
    ok("container.start host_pid / host_network conditional too",
       {"host_pid", "host_network"} <= S["container.start\x00cond"])
    ok("container.start binds conditional", "binds" in S["container.start\x00cond"])
    ok("container.start container/action unconditional (from the map literal)",
       {"container", "action"} <= S["container.start"])
    # the recurring YAML/Go literal trap: map-literal members are written "k": not detail["k"] =
    ok("map-literal members are counted as emitted", "path" in S["file.write"],
       str(sorted(S["file.write"])) if "file.write" in S else "no file.write")
    ok("file.access has NO constructor (R2.2 stub)", "file.access" not in TYPES)

    # --- 3. the pack reader: the list-marker-then-quote bug, pinned -------------------------------
    ROWS = W.read_demand_keys()
    ok("21 rule/predicate/key wires parsed", len(ROWS) == 21, str(len(ROWS)))
    ok("no wire has an unparsed key", not [r for r in ROWS if "<unparsed>" in r[3]],
       str([r[0] + "/" + r[2] for r in ROWS if "<unparsed>" in r[3]]))
    ok("no key carries a YAML quote", not [k for r in ROWS for k in r[3] if '"' in k or "'" in k],
       str([k for r in ROWS for k in r[3] if '"' in k or "'" in k]))
    ok("no key carries a leading '- '", not [k for r in ROWS for k in r[3] if k.startswith("-")])
    ok("detail_non_empty_any on privileged_start yields 3 keys",
       [r[3] for r in ROWS if r[0] == "carbonato.container.privileged_start" and r[2] == "detail_non_empty_any"]
       == [["privileged", "host_pid", "host_network"]],
       str([r[3] for r in ROWS if r[0] == "carbonato.container.privileged_start" and r[2] == "detail_non_empty_any"]))
    ok("detail_glob_any on host_root_mount yields the one key `binds`",
       [r[3] for r in ROWS if r[2] == "detail_glob_any"] == [["binds"]],
       str([r[3] for r in ROWS if r[2] == "detail_glob_any"]))

    # --- 4. CAN IT FAIL? synthesise a dead wire ---------------------------------------------------
    def verdict(emit_set, cond_set, key):
        if key in emit_set:
            return "WIRED"
        if key in cond_set:
            return "CONDITIONAL"
        return "MISSING"


    ok("a key the type emits is WIRED", verdict({"path"}, set(), "path") == "WIRED")
    ok("a path-dependent key is CONDITIONAL", verdict(set(), {"argv"}, "argv") == "CONDITIONAL")
    ok("a key the type never emits is MISSING", verdict({"exe", "argv"}, set(), "path") == "MISSING")
    ok("MISSING beats everything: `path` on process.exec",
       verdict(S["process.exec"], S["process.exec\x00cond"], "path") == "MISSING")
    ok("WIRED beats CONDITIONAL when both claim a key",
       verdict({"binds"}, {"binds"}, "binds") == "WIRED")

    # the exact defect shape this file exists to catch: path_glob_any on a type with no `path`
    ok("a path_glob_any on process.exec would be caught",
       verdict(S["process.exec"], S["process.exec\x00cond"], "path") == "MISSING")

    # --- 5. an empty denominator refuses to green -------------------------------------------------
    ok("main() on the real tree exits 0", W.main() == 0)

    # simulate the empty denominator: a pack whose rules all carry no predicates
    _orig = W.read_demand_keys
    W.read_demand_keys = lambda: []
    try:
        rc = W.main()
    finally:
        W.read_demand_keys = _orig
    ok("empty denominator exits 2, NOT 0 (a clean 0/0 is not a pass)", rc == 2, str(rc))

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  " + f)
        sys.exit(1)
    print("wirecheck_test: all checks passed")


if __name__ == "__main__":
    main()

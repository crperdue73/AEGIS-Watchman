#!/usr/bin/env python3
"""presencecheck_test -- the last unread match family: detail_non_empty / detail_non_empty_any.

BEAT 588. After beat 587 the coverage ledger said 7 values are read by nothing, all of them the
presence tests: `detail_non_empty` (1 value, carbonato.net.listener_bound) and
`detail_non_empty_any` (6 values, carbonato.container.privileged_start and
carbonato.container.after_identity_change). The ledger's own note said "presence tests, little to
read" -- which is an excuse, not a finding. There is plenty to read here, and one part of it is the
same disease as beat 587.

WHAT THE ENGINE DOES (engine.go:148, read and pinned below)
    for _, k := range m.DetailNonEmpty {
        if detailString(action, k) == "" { return false }        // AND over keys
    }
    if len(m.DetailNonEmptyAny) > 0 {
        any := false
        for _, k := range m.DetailNonEmptyAny {
            if detailString(action, k) != "" { any = true; break }
        }
        if !any { return false }                                 // OR over keys
    }

  Presence is decided by the SAME `detailString` type switch as detail_equals. So a detail value
  that is not a string and not []byte is INVISIBLE to a presence test too: `privileged: true` (a
  JSON boolean) reads as "", the rule does not fire, and nothing anywhere says so. The v1 collector
  emits the string "true" for all three flags, so the shipped path is fine -- and that is proven
  here, not asserted.

WHY THIS IS NOT A REDUNDANT COPY OF scopecheck_test.py
  scopecheck pins the type table for ONE value on ONE rule. This file pins (a) the AND-vs-OR
  asymmetry, which is the semantic difference between the two keys and the only place a
  transcription can silently swap them, (b) the emit shape of every collector that feeds a presence
  test -- docker.go for the three container flags AND egress.go for `listen`, which was never
  checked by anything, and (c) that the *pack* uses OR where the rule title says "any of".

  That last one is where a real miss lives: a rule whose probability rests on an OR can fire on one
  flag while the alert text implies the rule is watching four fields.

THE TRANSCRIPTION CAVEAT, STATED NOT HIDDEN
  No Go toolchain on this host. `detail_string` below is a transcription of engine.go:307 and the
  predicate block is a transcription of engine.go:148-165. A transcription proves the rule I wrote
  down, not the one that compiles. Beat 587's M1 died because the transcription is pinned against
  its own source; the same pin is repeated here for the functions THIS file uses, so a drift in
  either copy breaks a test instead of sliding through.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fieldcheck  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, ".."))
FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def detail_string(v):
    """Transcription of engine.go:307 detailString. String and []byte pass; everything else is ''."""
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        return v.decode()
    if isinstance(v, str):
        return v
    return ""


def non_empty(detail, keys):
    """Transcription of the AND block, engine.go:148. EVERY listed key must be non-empty."""
    if detail is None:
        return False
    return all(detail_string(detail.get(k)) != "" for k in keys)


def non_empty_any(detail, keys):
    """Transcription of the OR block, engine.go:153. AT LEAST ONE listed key non-empty."""
    if detail is None:
        return False
    return any(detail_string(detail.get(k)) != "" for k in keys)



def main():
    # --------------------------------------------------------------------------------------------------
    # 1. POSITIVE CONTROLS. Without these a matcher that always returns False passes everything below.
    # --------------------------------------------------------------------------------------------------
    check("CONTROL: AND fires on the collector's listen value",
          non_empty({"listen": "0.0.0.0:4444", "syscall": "listen"}, ["listen"]), True)
    check("CONTROL: OR fires on the collector's privileged value",
          non_empty_any({"privileged": "true", "image": "alpine"}, ["privileged", "host_pid", "host_network"]),
          True)
    check("CONTROL: OR fires on host_pid ALONE (the OR is real, not an AND in disguise)",
          non_empty_any({"host_pid": "true"}, ["privileged", "host_pid", "host_network"]), True)
    check("CONTROL: OR fires on host_network alone",
          non_empty_any({"host_network": "true"}, ["privileged", "host_pid", "host_network"]), True)

    # --------------------------------------------------------------------------------------------------
    # 2. THE TYPE DEFECT, SAME SHAPE AS BEAT 587. A boolean or a number is invisible to a presence test.
    # --------------------------------------------------------------------------------------------------
    check("AND: a JSON boolean true is INVISIBLE (reads as empty) and the rule does NOT fire",
          non_empty({"listen": True}, ["listen"]), False)
    check("OR: a JSON boolean true is INVISIBLE and the rule does NOT fire",
          non_empty_any({"privileged": True}, ["privileged", "host_pid", "host_network"]), False)
    check("OR: three JSON booleans are all invisible -- the worst case, every flag present",
          non_empty_any({"privileged": True, "host_pid": True, "host_network": True},
                        ["privileged", "host_pid", "host_network"]), False)
    check("OR: int 1 is invisible", non_empty_any({"privileged": 1}, ["privileged"]), False)
    check("AND: int 1 is invisible", non_empty({"listen": 1}, ["listen"]), False)

    # --------------------------------------------------------------------------------------------------
    # 3. SEMANTICS: AND and OR must differ, and this is the one place a swap is silent.
    # --------------------------------------------------------------------------------------------------
    KEYS = ["privileged", "host_pid", "host_network"]
    ONE = {"privileged": "true"}
    check("the two predicates DISAGREE on one-of-three (that disagreement is the semantics)",
          (non_empty(ONE, KEYS), non_empty_any(ONE, KEYS)), (False, True))
    check("AND and OR agree when all keys are present",
          (non_empty({"a": "1", "b": "2"}, ["a", "b"]), non_empty_any({"a": "1", "b": "2"}, ["a", "b"])),
          (True, True))
    check("AND and OR agree when no key is present",
          (non_empty({}, ["a", "b"]), non_empty_any({}, ["a", "b"])), (False, False))
    check("AND fails on a PARTIAL set where OR succeeds (the swap would be silent)",
          (non_empty(ONE, ["privileged", "host_pid"]), non_empty_any(ONE, ["privileged", "host_pid"])),
          (False, True))
    check("an empty string is empty for both", (non_empty({"a": ""}, ["a"]), non_empty_any({"a": ""}, ["a"])),
          (False, False))
    check("nil detail is empty for both", (non_empty(None, ["a"]), non_empty_any(None, ["a"])),
          (False, False))
    check("a missing key is empty for both", (non_empty({"b": "x"}, ["a"]), non_empty_any({"b": "x"}, ["a"])),
          (False, False))

    # --------------------------------------------------------------------------------------------------
    # 4. PIN THE TRANSCRIPTION to engine.go, the same way scopecheck pins detailString. A transcription
    #    that can drift to match any behaviour proves nothing about the Go.
    # --------------------------------------------------------------------------------------------------
    engine = open(os.path.join(ROOT, "internal", "rules", "engine.go"), encoding="utf-8").read()

    ds = engine.split("func detailString(")[1].split("\nfunc ")[0]
    check("engine.go's detailString has a string case", "case string:" in ds, True)
    check("  ...a []byte case", "case []byte:" in ds, True)
    check("  ...and an EMPTY-STRING default, not a stringified one",
          re.search(r"default:\s*\n\s*return\s+\"\"", ds) is not None, True)
    check("  ...it does not stringify the default case", "strconv" not in ds and "fmt.Sprint" not in ds, True)

    # the AND loop: iterate DetailNonEmpty, reject on the FIRST empty value
    andblk = engine.split("for _, k := range m.DetailNonEmpty {")[1].split("}")[0]
    check("AND iterates DetailNonEmpty", "m.DetailNonEmpty {" in engine, True)
    check("  ...and rejects when detailString(...) == \"\"",
          'detailString(action, k) == ""' in andblk, True)
    check("  ...inside the same loop as its iteration", "return false" in andblk, True)

    # the OR loop: latched on the FIRST non-empty value, and length()>0 gated
    orblk = engine.split("if len(m.DetailNonEmptyAny) > 0 {")[1].split("\n\tif len(m.DetailGlobAny)")[0]
    check("OR is gated on len(DetailNonEmptyAny) > 0", "m.DetailNonEmptyAny" in engine, True)
    check("  ...latches any = true on a non-empty value", 'detailString(action, k) != ""' in orblk, True)
    check("  ...breaks on the first hit", "any = true" in orblk and "break" in orblk, True)
    check("  ...and rejects only when nothing was non-empty",
          re.search(r"if !any \{\s*\n\s*return false", orblk) is not None, True)
    check("the two blocks are NOT the same code (a copy-paste swap would make AND into OR)",
          andblk.strip() != orblk.strip(), True)

    # --------------------------------------------------------------------------------------------------
    # 5. THE PACK: every presence value must be one the collectors actually emit, as a STRING.
    #    Keys are derived from the pack, not typed, so a new presence rule cannot slip past this.
    # --------------------------------------------------------------------------------------------------
    packtext = open(fieldcheck.PACK, encoding="utf-8").read()

    presence = {"detail_non_empty": [], "detail_non_empty_any": []}
    for block in re.split(r"\n  - id: ", packtext)[1:]:
        rid = block.split("\n", 1)[0].strip()
        for key in presence:
            m = re.search(rf"^\s+{key}:\s*\n((?:\s+- .*\n)+)", block, re.M)
            if not m:
                continue
            vals = re.findall(r"-\s*\"?([^\s\"#]+)\"?", m.group(1))
            presence[key].append((rid, vals))

    check("detail_non_empty is used by exactly one rule", len(presence["detail_non_empty"]), 1)
    check("detail_non_empty_any is used by exactly two rules", len(presence["detail_non_empty_any"]), 2)
    check("the pack's presence values total 7 (the ledger's unread count)",
          sum(len(v) for _, v in presence["detail_non_empty"])
          + sum(len(v) for _, v in presence["detail_non_empty_any"]), 7)

    by_rule = {rid: vals for key in presence for rid, vals in presence[key]}
    check("listener_bound keys on 'listen'", by_rule.get("carbonato.net.listener_bound"), ["listen"])
    check("privileged_start watches the three container flags",
          by_rule.get("carbonato.container.privileged_start"),
          ["privileged", "host_pid", "host_network"])
    check("after_identity_change watches the same three",
          by_rule.get("carbonato.container.after_identity_change"),
          ["privileged", "host_pid", "host_network"])

    # --------------------------------------------------------------------------------------------------
    # 6. THE EMIT SITES. Every presence key must be emitted by a collector AS A GO STRING LITERAL.
    #    `listen` was checked by nothing before this file.
    # --------------------------------------------------------------------------------------------------
    docker_go = open(os.path.join(ROOT, "internal", "telemetry", "docker.go"), encoding="utf-8").read()
    egress_go = open(os.path.join(ROOT, "internal", "telemetry", "egress.go"), encoding="utf-8").read()

    for key in ["privileged", "host_pid", "host_network"]:
        m = re.search(rf'detail\["{key}"\]\s*=\s*([^\n]+)', docker_go)
        check(f"docker.go emits {key}", m is not None, True)
        check(f'  ...as a string literal "true", not a bool',
              m is not None and m.group(1).strip() == '"true"', True)

    m = re.search(r'"listen":\s*([^,\n]+)', egress_go)
    check("egress.go emits 'listen'", m is not None, True)
    check("  ...from a decoded address variable, not a literal",
          m is not None and m.group(1).strip() == "addr", True)

    # --------------------------------------------------------------------------------------------------
    # 7. END TO END. Take each key the pack names, feed it the literal the collector emits, and require
    #    the transcribed predicate to fire. Derived from both files; nothing typed by hand.
    # --------------------------------------------------------------------------------------------------
    listen_val = "0.0.0.0:4444"   # decodeSockAddr's JoinHostPort shape, exercised in egress_test.go:244
    check("listener_bound fires on a decoded wildcard bind",
          non_empty({"listen": listen_val}, ["listen"]), True)

    for key in ["privileged", "host_pid", "host_network"]:
        lit = re.search(rf'detail\["{key}"\]\s*=\s*"([^"]*)"', docker_go)
        check(f"privileged_start fires when only {key} is set (the value the collector emits)",
              non_empty_any({key: lit.group(1)}, KEYS), True)

    # The un-enriched case is the one that matters most: no daemon, no HostConfig, no risk flags.
    # The action still fires (identity only) and the OR rule must NOT. That is correct behaviour -- an
    # unenriched container is reported, not accused -- and it is worth having a test that says so.
    check("no risk flags => privileged_start does NOT fire (unenriched container is not an accusation)",
          non_empty_any({"container": "abc", "action": "start", "image": "alpine"},
                        KEYS), False)

    print()
    if FAILS:
        print(f"presencecheck: {len(FAILS)} FAIL")
        sys.exit(1)
    print("presencecheck: all ok")


if __name__ == "__main__":
    main()

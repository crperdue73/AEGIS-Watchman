#!/usr/bin/env python3
"""exematch_test -- the 13 exe_any values, against the matcher that actually reads them.

Beat 593. `exe_any` looks like the other glob families and is NOT one. `ExeAny` goes through
`exeMatches` (internal/rules/engine.go:188), which is exact-or-basename -- there is no glob expansion
at all, and no `path.Match` call anywhere in it:

    func exeMatches(term, exe string) bool {
        if exe == "" { return false }
        if term == exe { return true }
        return term == path.Base(exe)
    }

So "python*" as an exe value would be a DEAD TERM: it never matches anything, silently, because "*" is
only a character. Every other family in this pack wants a glob; this one punishes one. That asymmetry is
the entire test.

The doc comment states the intent plainly -- '"bash" matches "/bin/bash" and "/usr/bin/curl" matches
exactly' -- and the second half is worth sitting with: a rule term that is already a full path only ever
matches that exact path OR a basename equal to the whole path string, which can never happen. So a
slash-bearing term in exe_any is exact-only, by construction, not by luck.

Limits, stated up front: this decides which exe VALUES a rule can be reached by. It does not run an
event through a rule, so the ledger does not move -- 47 LIVE / 78 REACH / 0 unread, unchanged.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print(f"FAIL {name}: got {got!r} want {want!r}")
    else:
        print(f"ok   {name}")


def exe_matches(term, exe):
    """Transcription of engine.go exeMatches. Pinned to the source in section 1."""
    if exe == "":
        return False
    if term == exe:
        return True
    return term == exe.rsplit("/", 1)[-1] if "/" in exe else term == exe



def main():
    # --------------------------------------------------------------------------------------------------
    # 1. Pin the transcription to the Go source. No toolchain here, so the guard is a structural read of
    #    the file: if exeMatches grows a glob call, this suite breaks rather than going quietly stale.
    # --------------------------------------------------------------------------------------------------
    engine = open(os.path.join(ROOT, "internal", "rules", "engine.go"), encoding="utf-8").read()
    m = re.search(r"func exeMatches\(term, exe string\) bool \{(.*?)\n\}", engine, re.S)
    check("exeMatches exists in engine.go", m is not None, True)
    body = m.group(1) if m else ""
    check("exeMatches compares the term to the raw exe", "term == exe" in body, True)
    check("exeMatches compares the term to path.Base(exe)", "path.Base(exe)" in body, True)
    check("exeMatches does NOT use path.Match (this family is not a glob family)",
          "path.Match" in body, False)
    check("exeMatches does NOT use anyGlobMatches", "anyGlobMatches" in body, False)
    check("exeMatches refuses an empty exe outright", "exe == \"\"" in body, True)
    check("engine.go routes ExeAny through exeMatches",
          re.search(r"for _, e := range m\.ExeAny \{\s*if exeMatches\(e, exe\)", engine) is not None, True)

    # --------------------------------------------------------------------------------------------------
    # 2. Unit-level: what the matcher does, stated as measured behaviour.
    # --------------------------------------------------------------------------------------------------
    check("bare term matches a bare exe", exe_matches("bash", "bash"), True)
    check("bare term matches the basename of a full path", exe_matches("bash", "/bin/bash"), True)
    check("bare term matches a basename at any depth", exe_matches("python3", "/usr/local/bin/python3"), True)
    check("empty exe never matches, not even an empty term", (exe_matches("bash", ""), exe_matches("", "")),
          (False, False))

    # The asymmetry: a glob in this family is a dead term.
    check("a glob term is a DEAD TERM: 'python*' matches nothing", exe_matches("python*", "/usr/bin/python3"), False)
    check("  ...nor the literal string it looks like", exe_matches("python*", "python*"), True)
    check("  ...and '?' is just a character", exe_matches("bas?", "/bin/bash"), False)
    check("  ...[...] is just characters too", exe_matches("ba[st]h", "/bin/bash"), False)

    # Full-path terms.
    check("a full-path term matches that exact path", exe_matches("/usr/bin/curl", "/usr/bin/curl"), True)
    check("  ...but NOT the bare name", exe_matches("/usr/bin/curl", "curl"), False)
    check("  ...and not the same basename elsewhere, since Base(exe) != the term",
          exe_matches("/usr/bin/curl", "/usr/local/bin/curl"), False)

    # --------------------------------------------------------------------------------------------------
    # 3. The pack: read the 13 values and ask what each one can actually reach.
    # --------------------------------------------------------------------------------------------------
    pack = open(os.path.join(ROOT, "rules", "carbonato.yaml"), encoding="utf-8").read()

    # The two shapes the pack uses: a block list and a flow list.
    # NOTE: this regex was `(?:\s+- .*\n)+`, and `\s` matches a newline, so it walked past the end of the
    # list and ate the next `  - id: carbonato.exec.ssh_key_heredoc` line as a list item -- reporting 12
    # values with one empty string. The pack was right; my reader was wrong. Anchored to spaces now.
    block = re.search(r"exe_any:\n((?:[ \t]+- .*\n)+)", pack)
    flow = re.search(r"exe_any: \[(.*?)\]", pack)
    check("the pack uses both a block-form exe_any and a flow-form one", (block is not None, flow is not None),
          (True, True))

    block_vals = [ln.strip()[2:].strip() for ln in block.group(1).splitlines()] if block else []
    flow_vals = [v.strip() for v in flow.group(1).split(",")] if flow else []
    vals = block_vals + flow_vals
    check("13 exe_any values total", len(vals), 13)
    check("block form holds 10", len(block_vals), 10)
    check("flow form holds 3", len(flow_vals), 3)

    check("no exe value contains a glob metacharacter (any would be a dead term)",
          [v for v in vals if any(c in v for c in "*?[")], [])
    check("no exe value contains a slash (a slash term is exact-only, so it could never see a bare exec)",
          [v for v in vals if "/" in v], [])
    check("no exe value is empty", [v for v in vals if not v], [])

    # The authority is YAML, not my regex -- the same lesson as FINDING-2026-10-26 (_yaml_scalar). Read the
    # pack with a real parser and require that my two readers agree with it exactly, rule for rule.
    import yaml
    doc = yaml.safe_load(open(os.path.join(ROOT, "rules", "carbonato.yaml"), encoding="utf-8"))
    truth = {r["id"]: r["match"]["exe_any"] for r in doc["rules"]
             if "exe_any" in r.get("match", {})}
    check("yaml.safe_load sees the same 13 values", sum(len(v) for v in truth.values()), 13)
    check("yaml.safe_load sees the same two rules", sorted(truth), sorted(
        ["carbonato.exec.shell_spawn", "carbonato.infra.privileged_container"]))
    check("my block reader agrees with yaml.safe_load",
          block_vals, truth.get("carbonato.exec.shell_spawn"))
    check("my flow reader agrees with yaml.safe_load",
          list(flow_vals), truth.get("carbonato.infra.privileged_container"))
    check("no exe value repeats within its own rule",
          (len(block_vals) != len(set(block_vals)), len(flow_vals) != len(set(flow_vals))), (False, False))

    # --------------------------------------------------------------------------------------------------
    # 4. Shadowing: in this family, one term subsumes another only if it is the SAME string.
    #    (Unlike globs, there is no wildcard to make 'python' swallow 'python3' -- and it does not.)
    # --------------------------------------------------------------------------------------------------
    check("'python' does NOT subsume 'python3' (exact/basename only)",
          (exe_matches("python", "/usr/bin/python3"), exe_matches("python3", "/usr/bin/python3")), (False, True))
    check("'sh' does not subsume 'bash'", exe_matches("sh", "/bin/bash"), False)
    check("'pwsh' does not subsume 'powershell'", exe_matches("pwsh", "/usr/bin/powershell"), False)
    check("no value shadows another within a rule", len(vals) - len(set(vals)) in (0, 0), True)

    # --------------------------------------------------------------------------------------------------
    # 5. The real reach question: which interpreters does the shell-family rule MISS?
    #    These are measured absences, not defects -- whether to add them is the pack owner's call.
    # --------------------------------------------------------------------------------------------------
    FAMILY = set(block_vals)
    measured_gaps = {
        "/usr/bin/python2": False,
        "/usr/bin/python3.11": False,
        "/usr/bin/python3.12": False,
        "/bin/busybox": False,
        "/usr/bin/env": False,
        "/bin/ksh": False,
        "/usr/bin/fish": False,
        "/usr/bin/node": False,
        "/usr/bin/php": False,
    }
    for exe, want in measured_gaps.items():
        cand = exe.rsplit("/", 1)[-1]
        hit = [v for v in sorted(FAMILY) if exe_matches(v, exe)]
        check(f"shell-family rule does not reach {exe} (basename {cand!r})", hit, [])

    check("  ...and it DOES reach the versioned name it lists literally",
          [v for v in sorted(FAMILY) if exe_matches(v, "/usr/bin/python3")], ["python3"])

    print()
    if FAILS:
        print(f"exematch: {len(FAILS)} FAIL")
        sys.exit(1)
    print("exematch: all ok")


if __name__ == "__main__":
    main()

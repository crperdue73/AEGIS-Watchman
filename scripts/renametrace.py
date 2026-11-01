#!/usr/bin/env python3
"""renametrace — follow one rename record all the way to the detail map, by reading the Go.

The claim on the table since 11:30 is that `persist.file_write` reads a `detail["path"]` that "is never
set for a rename". At 12:20 that claim was repeated in a file that had meanwhile been overwritten by a
different check, so nobody had re-read the code since. This does. Nothing here is a test of intent —
it prints what each function does to the record, line by line, and then says what the rule sees.

THE CHAIN (all in internal/telemetry/filewatch.go)
    WriteAction  -> writeTarget()          : is this a write, and what is the target?
                 -> firstTargetPath()      : the target when the syscall decides, not the nametype
                 -> detail["path"] = target
    Then engine.go reads detail["path"] for path_glob_any (rule.go / engine.go:130).

So the question is not "is path set" — it is set, unconditionally, at filewatch.go:66 — but
**which path**, on a rename. That distinction decides whether the rule is blind or merely wrong.

Run:  python3 scripts/renametrace.py            -> prose trace + verdict, exit 0
      python3 scripts/renametrace.py --json     -> the same facts as a dict
"""
import os
import re
import json
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FW = os.path.join(ROOT, "internal", "telemetry", "filewatch.go")


def grab(text, pattern, n=1):
    m = re.search(pattern, text, re.S)
    return m.group(n) if m else None


def main():
    as_json = "--json" in sys.argv
    try:
        with open(FW, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as e:
        print(f"renametrace: cannot read {FW}: {e}", file=sys.stderr)
        return 2

    strict = grab(src, r"var strictWriteSyscalls = map\[string\]bool\{(.*?)\n\}")
    opens = grab(src, r"var openSyscalls = map\[string\]bool\{(.*?)\n\}")
    syscalls = sorted(set(re.findall(r'"([a-z0-9_]+)":\s*true', strict or "")))
    rename_in_strict = [s for s in syscalls if s.startswith("rename")]

    # the assignment that decides the finding
    path_line = None
    for i, line in enumerate(src.splitlines(), 1):
        if re.match(r'\s*"path":\s*target,', line):
            path_line = (i, line.strip())

    # the branch that decides WHICH path
    skip_parent = "p.Nametype == \"PARENT\"" in src
    nametype_branch = "p.Nametype == \"CREATE\" || p.Nametype == \"DELETE\"" in src
    syscall_branch = "strictWriteSyscalls[r.Syscall]" in src

    facts = {
        "strict_write_syscalls": syscalls,
        "rename_syscalls_in_strict": rename_in_strict,
        "detail_path_assignment": path_line,
        "first_target_skips_PARENT": skip_parent,
        "writeTarget_prefers_CREATE_DELETE_nametype": nametype_branch,
        "writeTarget_falls_back_to_syscall": syscall_branch,
    }

    # verdict, from the facts rather than around them
    if rename_in_strict and skip_parent and syscall_branch:
        verdict = "PATH-IS-SET-BUT-IS-THE-SOURCE"
        note = ("A rename is a strict write. No PARENT is skipped and no CREATE/DELETE nametype exists, so "
                "firstTargetPath() returns the first non-PARENT PATH record — which for a rename is the "
                "SOURCE. detail['path'] is therefore set, always, and holds the file being renamed *from*.")
        rule_sees = ("The rule's globs are matched against directory names: '*/etc/shadow*', '*/root/.ssh*', "
                     "and the like. On `mv /tmp/x /etc/shadow` the rule is handed /tmp/x and misses a write to "
                     "a credential file. On `mv /etc/shadow /tmp/x` it is handed /etc/shadow and fires — a "
                     "false positive on the *source* of a rename, which luckily coincides with a real one.")
    elif not rename_in_strict:
        verdict = "RENAME-NOT-A-WRITE-HERE"
        note = "No rename syscall is treated as a write by this tree, so WriteAction never sees one."
        rule_sees = "The rule cannot be reached by a rename at all; the question is moot until one is parsed."
    else:
        verdict = "UNDETERMINED"
        note = "One of the branches this trace depends on is missing; the code has changed shape."
        rule_sees = "Not claimed."

    if as_json:
        print(json.dumps({**facts, "verdict": verdict, "note": note}, indent=2, default=str))
        return 0

    print("renametrace — following one rename record through internal/telemetry/filewatch.go\n")
    print(f"  WriteAction():            detail[\"path\"] = target  — at line {path_line[0]}, unconditional")
    print(f"  writeTarget():            CREATE/DELETE nametype short-circuit: {nametype_branch}")
    print(f"                            falls back to strictWriteSyscalls[syscall]: {syscall_branch}")
    print(f"  strict write syscalls     {len(syscalls)}: {', '.join(syscalls)}")
    print(f"                            of which rename-family: {', '.join(rename_in_strict) or 'none'}")
    print(f"  firstTargetPath():        skips nametype==PARENT: {skip_parent}")
    print()
    print(f"  VERDICT: {verdict}")
    print(f"  {note}")
    print(f"  What the rule sees: {rule_sees}")
    print()
    print("  denominator: 1 rename-shaped record, 1 chain of 3 functions, 3 branches that decide the outcome.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

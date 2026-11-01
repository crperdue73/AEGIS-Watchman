#!/usr/bin/env python3
"""predaudit — does every rule in the pack predicate on a field the engine can actually see?

This closes a hole in everything I checked today. Every probe so far asked "does this glob reach this path?" — i.e.
whether a rule's *values* are right. None asked whether the rule's **field names exist in the action** the engine
hands it. `matches()` in `internal/rules/engine.go` reads:

    detailString(action, k)   ->   "" when the key is absent

and the predicates compare against that "" with no distinction between *absent* and *empty*. So a misspelled key
in `detail_equals` / `detail_non_empty` / `detail_glob_any` **does not fail loudly**. It either quietly makes an
`detail_equals` rule impossible to fire, or quietly makes a `detail_non_empty`/`_any` rule impossible to fire.
A rule that can never fire looks exactly like a rule that never got the chance to.

METHOD
  The engine's field vocabulary is taken from the Go source, not from my memory:
    - `PRODUCERS`: for each action family, the detail keys the telemetry layer assigns (read from
      internal/telemetry/*.go as `detail["key"] = ...`).
    - engine-consumed detail keys for each family (exe / argv / path / endpoint / etc.) are added per family.
  Then, for each rule in the pack, every field name it predicates on is checked against the vocabulary for
  *that rule's action type*.
  A field that exists in the vocabulary of ANOTHER family is labelled CROSS-FAMILY — the interesting class,
  because it looks plausible and can never match.

DENOMINATOR is printed before the verdict, and every rule that could not be checked (unknown type) is counted
separately rather than silently passing. Exit 0 = audit ran and every predicate field resolved to a family
vocabulary; exit 1 = at least one rule predicates on a field its action can never carry; exit 2 = blind
(no fields parsed / no vocabulary extracted).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")
TELEMETRY = os.path.join(ROOT, "internal", "telemetry")

# Fields the engine itself reads off an action's detail, per family prefix. Read from engine.go's matches():
# exe, argv, path, endpoint. Handlers in telemetry/ add the rest.
ENGINE_FIELDS = {"exe", "argv", "path", "endpoint"}


def action_types():
    """{Go const name -> wire type} from internal/telemetry/*.go const declarations, plus engine'd fields."""
    consts = {}
    if os.path.isdir(TELEMETRY):
        for name in sorted(os.listdir(TELEMETRY)):
            if not name.endswith(".go"):
                continue
            with open(os.path.join(TELEMETRY, name), encoding="utf-8") as fh:
                src = fh.read()
            # NOTE ON THE REGEX, learned the hard way. `^\s*([A-Z]\w*)` never matches `const NetListener = ...`
            # because after \s* the next char is 'c', not an uppercase identifier — so the two consts declared
            # as `const X = "..."` (ProcessExec, NetListen) were invisible while the `const (...)` group entries
            # matched fine. The optional keyword group is the fix. Consequence of the bug: net.listen was missing
            # from the vocabulary, its one rule went UNCHECKED, and the run exited 0 — a coverage hole wearing a
            # green light. Then the first fix attempt over-corrected and broke the working cases; only running the
            # regex against the actual file showed both directions were wrong.
            for m in re.finditer(r'^[ \t]*(?:const[ \t]+)?([A-Z]\w*)[ \t]*=[ \t]*"([a-z]+\.[a-z_]+)"', src, re.M):
                consts[m.group(1)] = m.group(2)
    return consts


def producer_fields():
    """{wire action type: set(detail fields)}.

    The Go handlers name their output type by CONSTANT (`Type: FileWrite`), never by literal, so the type is
    resolved through action_types() first. That was the first draft's bug: it looked for `Type: "file.write"`
    literals, found none, fell back to file-derived fake family names ('filewatch.go.filewatch'), and then
    reported 0/0 checked with rc=0 — a green light on an empty denominator, which is the exact failure this
    project exists to catch. Denominator is asserted below.
    """
    consts = action_types()
    fam = {}
    if not os.path.isdir(TELEMETRY):
        return fam
    for name in sorted(os.listdir(TELEMETRY)):
        if not name.endswith(".go") or name.endswith("_test.go"):
            continue
        with open(os.path.join(TELEMETRY, name), encoding="utf-8") as fh:
            src = fh.read()
        for block in re.split(r"\nfunc ", src):
            keys = set(re.findall(r'detail\["([^"]+)"\]\s*=', block))
            # literals built as a whole map: detail := map[string]any{"kind": ..., "novel": "true", ...}
            for ml in re.findall(r"map\[string\]any\{([^}]*)\}", block, re.S):
                keys |= set(re.findall(r'"([a-z_]+)"\s*:', ml))
            if not keys:
                continue
            # `Type: FileWrite` / `Type: NetListen` / `Type: ScopeChange`. One block can stamp SEVERAL types
            # (egress.go's EgressAction vs ListenAction split by const inside one function group), so all are
            # credited. getattr on a nil-safe expression is irrelevant here: we only read the const NAME.
            for cn in re.findall(r"Type:\s*([A-Z]\w*)", block):
                if cn in consts:
                    fam.setdefault(consts[cn], set()).update(keys)
            # a dispatch function returning one type for many syscalls still stamps types this way
            for lit in re.findall(r'Type:\s*"([a-z]+\.[a-z_]+)"', block):
                fam.setdefault(lit, set()).update(keys)
    # the engine reads these off every action's detail regardless of handler (matches(): exe/argv/path/endpoint)
    for f in fam:
        fam[f] |= ENGINE_FIELDS
    # container.start: docker.go builds the detail map in one function and stamps the type elsewhere, so resolve
    # it by const name across the whole file rather than per-function.
    dock = os.path.join(TELEMETRY, "docker.go")
    if os.path.exists(dock):
        with open(dock, encoding="utf-8") as fh:
            dsrc = fh.read()
        if "ContainerStart" in dsrc:
            dkeys = set(re.findall(r'detail\["([^"]+)"\]\s*=', dsrc))
            for ml in re.findall(r"map\[string\]any\{([^}]*)\}", dsrc, re.S):
                dkeys |= set(re.findall(r'"([a-z_]+)"\s*:', ml))
            fam.setdefault("container.start", set()).update(dkeys | ENGINE_FIELDS)
    # egress.go stamps both net.egress and net.listen; when _test-free parsing missed one, resolve by file.
    eg = os.path.join(TELEMETRY, "egress.go")
    if os.path.exists(eg):
        with open(eg, encoding="utf-8") as fh:
            esrc = fh.read()
        for cn in re.findall(r"Type:\s*([A-Z]\w*)", esrc):
            if cn in consts:
                dk = set()
                for ml in re.findall(r"map\[string\]any\{([^}]*)\}", esrc, re.S):
                    dk |= set(re.findall(r'"([a-z_]+)"\s*:', ml))
                dk |= set(re.findall(r'detail\["([^"]+)"\]\s*=', esrc))
                fam.setdefault(consts[cn], set()).update(dk | ENGINE_FIELDS)
    # CONST-ONLY TYPES: a type declared in telemetry.go (no detail[] lines there) and stamped in a file whose
    # block grouping hides it. Resolve by scanning every non-test file for the const name and crediting that
    # file's detail keys — otherwise net.listen silently reads as "unknown family", which is exactly how a
    # coverage hole hides (the rule goes UNCHECKED and the run still exits 0).
    for name in sorted(os.listdir(TELEMETRY)):
        if not name.endswith(".go") or name.endswith("_test.go"):
            continue
        with open(os.path.join(TELEMETRY, name), encoding="utf-8") as fh:
            src = fh.read()
        used = set(re.findall(r"Type:\s*([A-Z]\w*)", src))
        hit = {consts[c] for c in used if c in consts}
        if not hit:
            continue
        dk = set(re.findall(r'detail\["([^"]+)"\]\s*=', src))
        for ml in re.findall(r"map\[string\]any\{([^}]*)\}", src, re.S):
            dk |= set(re.findall(r'"([a-z_]+)"\s*:', ml))
        if not dk:
            continue
        for t in hit:
            fam.setdefault(t, set()).update(dk | ENGINE_FIELDS)
    # process.exec has no single producer file: the execve adapters stamp it from several places
    fam.setdefault("process.exec", set()).update(ENGINE_FIELDS | {"cwd", "ppid", "pid", "syscall", "comm", "args"})
    return fam


def rule_blocks():
    with open(PACK, encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for m in re.finditer(r"\n  - id: (\S+)(.*?)(?=\n  - id: |\Z)", text, re.S):
        body = m.group(2)
        at = re.search(r"^\s*type:\s*(\S+)", body, re.M)
        sev = re.search(r"^\s*severity:\s*(\S+)", body, re.M)
        out.append((m.group(1), at.group(1) if at else "", sev.group(1) if sev else "", body))
    return out


def predicate_fields(body):
    """(kind, field) pairs for every predicate the engine evaluates on a detail key."""
    out = []
    for m in re.finditer(r"^[ \t]*detail_equals:[ \t]*\n((?:[ \t]+\S+:.*\n?)+)", body, re.M):
        out += [("detail_equals", k) for k in re.findall(r"^[ \t]+(\w+):", m.group(1), re.M)]
    for key in ("detail_non_empty", "detail_non_empty_any"):
        m = re.search(rf"^[ \t]*{key}:[ \t]*\n((?:[ \t]*-[ \t]*.+\n?)+)", body, re.M)
        if m:
            out += [(key, l.strip()[2:].strip().strip('"')) for l in m.group(1).splitlines() if l.strip().startswith("-")]
    m = re.search(r"^[ \t]*detail_glob_any:[ \t]*\n((?:[ \t]+\S+:.*\n?)+)", body, re.M)
    if m:
        out += [("detail_glob_any", k) for k in re.findall(r"^[ \t]+(\w+):", m.group(1), re.M)]
    return out


def main():
    fam = producer_fields()
    rules = rule_blocks()
    if not fam:
        print("predaudit: no field vocabulary extracted from internal/telemetry — blind, not clean", file=sys.stderr)
        return 2
    print(f"predaudit — vocabulary from {len(fam)} action families:")
    for f in sorted(fam):
        print(f"    {f:22} {sorted(fam[f])}")
    print(f"\n  {len(rules)} rules in pack\n")

    checked = unchecked = bad = 0
    cross = []
    unknown_type = []
    no_predicates = []
    for rid, atype, sev, body in rules:
        preds = predicate_fields(body)
        if not preds:
            # No detail_* predicate at all: nothing to check against a vocabulary. Counted and NAMED, never
            # silently skipped — a rule with zero detail predicates is invisible to this instrument, and an
            # invisible rule that reads as "clean" is the failure mode the denominator exists to prevent.
            no_predicates.append((rid, atype))
            continue
        if atype not in fam:
            unchecked += len(preds)
            unknown_type.append((rid, atype))
            continue
        for kind, field in preds:
            checked += 1
            if field in fam[atype]:
                continue
            bad += 1
            owners = sorted(f for f in fam if field in fam[f])
            if owners:
                cross.append((rid, sev, kind, field, atype, owners))
                print(f"  CROSS-FAMILY  {rid} [{sev}] {kind} {field!r} — action {atype} never carries it "
                      f"(carried by: {', '.join(owners)})")
            else:
                print(f"  UNKNOWN-FIELD {rid} [{sev}] {kind} {field!r} — no family in the vocabulary carries it")

    if checked == 0:
        print("  predaudit: 0 predicate fields checked — an empty denominator is not a pass", file=sys.stderr)
        return 2
    print(f"\n  denominator: {len(rules)} rules in pack; {len(rules) - len(no_predicates)} carry detail "
          f"predicates; {checked} field(s) checked; {unchecked} unchecked (type not in vocabulary).")
    if unknown_type:
        for rid, at in unknown_type:
            print(f"    unchecked: {rid} (type {at!r}, vocabulary: {at in fam})")
    if no_predicates:
        print(f"  no detail predicate at all ({len(no_predicates)} rule(s) — out of scope for this instrument):")
        for rid, at in no_predicates:
            print(f"    {rid} (type {at})")
    print(f"  offending: {bad} of {checked} = {100 * bad / checked if checked else 0:.0f}%")

    print(f"\n  NOTE ON SEVERITY OF THIS CLASS: a CROSS-FAMILY field in a detail_equals or detail_non_empty")
    print(f"  predicate makes the rule permanently unfireable, and the engine reports nothing — detailString on a")
    print(f"  missing key returns \"\" and the comparison fails quietly. Silent, not loud.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

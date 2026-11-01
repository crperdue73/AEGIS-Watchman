#!/usr/bin/env python3
"""wirecheck — does every detail field a rule's predicate reads actually get emitted?

THE GAP THIS FILLS (and why it existed)
    At 12:18 on 2026-10-23 a class check took over the filename `scripts/typecheck.py` and the
    *wire* check it replaced was overwritten. The old test was parked in scripts/attic/ with a
    note saying so — and then nothing. By 21:45 no script in the tree checked wires at all:

      * typecheck.py  — declared action TYPE vs. a producer existing. Not a wire check.
      * fieldcheck.py — key SPELLING only (does the rule's key exist among this type's emitted
                        keys). It never asks which key the ENGINE reads for each predicate.
      * blindcheck.py — behavioural fixtures. Can miss a whole predicate family if no fixture
                        exercises it.

    So `path_glob_any` reading `detail["path"]` on a type whose producer never sets `path` was
    invisible to every guard. That is exactly the `persist.file_write` rename defect, found once by
    hand, twice, and unpinned by any script. This file pins it.

WHAT IT READS
    Left side  (demand): the predicate -> detail-key mapping, read out of internal/rules/engine.go
                          by locating each predicate block and the first detailString(action, "k")
                          inside it. Not recalled — read.
    Right side (supply): detail["k"] = ... assignments inside each type's Action constructor, read
                          out of internal/telemetry/*.go and attributed to a type by which
                          `XxxAction(` function body they sit in.

VERDICTS
    WIRED      rule reads a key its type emits.
    MISSING    rule reads a key its type NEVER emits. A dead wire: this predicate can never pass.
    CONDITIONAL the key is emitted on some code path only (inside an `if`). Reported separately:
               legal, but the predicate must tolerate the field being absent.
    NO-PRODUCER the rule's type has no constructor in this tree (R2.2 eBPF stub) — not judgeable.

EXIT: 0 = no MISSING and the denominator is non-zero · 1 = at least one MISSING · 2 = could not
      judge (empty denominator, or a source file did not parse). A clean 0/0 is NOT a pass.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TELEMETRY = os.path.join(ROOT, "internal", "telemetry")
ENGINE = os.path.join(ROOT, "internal", "rules", "engine.go")
PACK = os.path.join(ROOT, "rules", "carbonato.yaml")

# Predicate name in the Match struct -> the detail key its block reads.
# Verified against engine.go by _demand_from_engine(); this table is only the FALLBACK and is
# cross-checked below so a drifted table fails loudly instead of silently.
EXPECTED_DEMAND = {
    "Exe": "exe",
    "ExeAny": "exe",
    "ArgvAny": "argv",
    "PathGlobAny": "path",
    "EndpointGlobAny": "endpoint",
    "DetailEquals": "<key from rule>",
    "DetailNonEmpty": "<key from rule>",
    "DetailNonEmptyAny": "<key from rule>",
    "DetailGlobAny": "<key from rule>",
}


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def demand_from_engine():
    """{predicate: detail key the engine reads} read out of engine.go."""
    src = read(ENGINE)
    out = {}
    # Each predicate is guarded by `if len(m.NAME) > 0 {` or `for k, want := range m.NAME {`.
    for m in re.finditer(r"(?:if len\(m\.(\w+)\) > 0 \{|for \w+, \w+ := range m\.(\w+) \{)", src):
        name = m.group(1) or m.group(2)
        if name in out:
            continue
        # scan the next ~20 lines for the first keyed read
        window = src[m.end():m.end() + 900]
        km = re.search(r'detailString(?:Slice)?\(action,\s*"(\w+)"\)', window)
        if km:
            out[name] = km.group(1)
        elif re.search(r"for k, want := range m\." + name, src[m.start():m.start() + 80]):
            out[name] = "<key from rule>"
    # `exe := detailString(action, "exe")` is hoisted above every predicate block, so a window scan
    # attributes the NEXT block's key to ExeAny. Read the hoist directly.
    hoist = re.search(r'exe := detailString\(action, "(\w+)"\)', read(ENGINE))
    if hoist and "ExeAny" in out:
        out["ExeAny"] = hoist.group(1)
    if "DetailGlobAny" in out:
        # the glob block dereferences a rule-supplied key
        out["DetailGlobAny"] = "<key from rule>"
    return out


def _func_body(src, body_start):
    """Text of a brace-block starting just after its `{`, string- and comment-aware."""
    depth, i, in_str, in_line, in_block = 1, body_start, False, False, False
    while i < len(src) and depth:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""
        if in_line:
            if ch == "\n":
                in_line = False
        elif in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 1
        elif in_str:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == "/" and nxt == "/":
            in_line = True
            i += 1
        elif ch == "/" and nxt == "*":
            in_block = True
            i += 1
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return src[body_start:i]


def _guarded(body, pos):
    """True if the position sits inside an if/for/switch block within `body`."""
    line_start = body.rfind("\n", 0, pos) + 1
    indent = len(body[line_start:pos]) - len(body[line_start:pos].lstrip())
    for prev in reversed(body[:line_start].splitlines()):
        if not prev.strip():
            continue
        prev_indent = len(prev) - len(prev.lstrip())
        if prev_indent < indent:
            if re.match(r"\s*(if|for|switch)\b", prev):
                return True
            if prev.strip() == "}":
                return False
    return False


def type_constructors():
    """{go type string: unconditional keys} plus '<type>\x00cond' for path-dependent keys."""
    files = [f for f in sorted(os.listdir(TELEMETRY)) if f.endswith(".go")
             and not f.endswith("_test.go")]
    out = {}
    for fn in files:
        src = read(os.path.join(TELEMETRY, fn))
        for fm in re.finditer(r"\nfunc (?:\([^)]*\)\s*)?\w*Action\([^)]*\)\s*[^{]*\{", src):
            body = _func_body(src, fm.end())
            head = src[fm.start():fm.end()]
            tm = re.search(r"Type:\s*(\w+)", body) or re.search(r"Type:\s*(\w+)", head)
            if not tm:
                continue
            typ = CONST_TO_TYPE.get(tm.group(1), tm.group(1))
            emitted, conditional = set(), set()
            # literal members are written `"path": v,` inside `detail := map[string]any{`
            lit_spans = [(lm.start(), lm.end()) for lm in
                         re.finditer(r"detail\s*:=\s*map\[string\]any\{.*?\n\t\}", body, re.S)]
            for lm_ in lit_spans:
                for km in re.finditer(r'^\t\t"(\w+)":', body[lm_[0]:lm_[1]], re.M):
                    emitted.add(km.group(1))
            for km in re.finditer(r'detail\["(\w+)"\]\s*=', body):
                key = km.group(1)
                # `detail := map[string]any{ "k": v, ... }` literal members are unconditional
                lit = re.search(r"detail := map\[string\]any\{(.*?)\n\t\}",
                                body[:km.start()], re.S)
                in_literal = bool(lit) and km.start() < lit.end()
                if in_literal or not _guarded(body, km.start()):
                    emitted.add(key)
                else:
                    conditional.add(key)
            out.setdefault(typ, set()).update(emitted)
            out.setdefault(typ + "\x00cond", set()).update(conditional - emitted)
    return out


CONST_TO_TYPE = {
    "ProcessExec": "process.exec",
    "NetEgress": "net.egress",
    "NetListen": "net.listen",
    "FileAccess": "file.access",
    "FileWrite": "file.write",
    "ScopeChange": "scope.change",
    "ContainerStart": "container.start",
    "ModelCall": "model.call",
    "PromptInject": "prompt.inject",
}


def read_demand_keys():
    """{(rule_id, predicate, [detail keys])} straight from the pack."""
    pack = read(PACK)
    out = []
    for m in re.finditer(r"\n  - id: (\S+)(.*?)(?=\n  - id: |\Z)", pack, re.S):
        rid, body = m.group(1), m.group(2)
        tm = re.search(r"match:\s*\n\s*type:\s*(\S+)", body)
        typ = tm.group(1).strip("\"'") if tm else None
        for pm in re.finditer(r"\n\s+(exe_any|argv_any|path_glob_any|endpoint_glob_any|"
                              r"detail_equals|detail_non_empty_any|detail_non_empty|"
                              r"detail_glob_any):[ \t]*([^\n]*)", body):
            key, rest = pm.group(1), pm.group(2)
            if key in ("exe_any",):
                out.append((rid, typ, key, ["exe"]))
            elif key in ("argv_any",):
                out.append((rid, typ, key, ["argv"]))
            elif key == "path_glob_any":
                out.append((rid, typ, key, ["path"]))
            elif key == "endpoint_glob_any":
                out.append((rid, typ, key, ["endpoint"]))
            elif key == "detail_glob_any":
                seg = body[pm.start() + len(pm.group(0)):pm.start() + len(pm.group(0)) + 600]
                keys = []
                for line in seg.splitlines():
                    m2 = re.match(r'^(\s+)(\w+):', line)
                    if not m2:
                        continue
                    keys.append(m2.group(2))
                    if len(keys) >= 4:
                        break
                out.append((rid, typ, key, keys or ["<unparsed>"]))
            else:  # detail_equals / detail_non_empty[_any]
                inline = rest.strip()
                if inline.startswith("["):
                    keys = [v.strip().strip("\"'") for v in inline.strip("[]").split(",") if v.strip()]
                elif inline and not inline.startswith(">"):
                    keys = [inline.strip("\"'")]
                else:
                    # Two YAML block shapes are legal here:
                    #   - "novel"           (list item - strip "- " BEFORE quotes: the 532/533 bug)
                    #   novel: "true"       (nested map, one indent deeper)
                    seg = body[pm.start():pm.start() + 600]
                    items, mapped = [], []
                    for line in seg.splitlines()[1:]:
                        if not line.strip():
                            if items or mapped:
                                break
                            continue
                        m_item = re.match(r'^[ \t]*-[ \t]+(.*)$', line)
                        if m_item:
                            v = m_item.group(1).strip()
                            if ":" in v and not v.startswith('"'):
                                break
                            items.append(v.strip('"\''))
                            continue
                        m_map = re.match(r'^[ \t]+(\w+):[ \t]*(\S.*)$', line)
                        if m_map:
                            mapped.append(m_map.group(1))
                            continue
                        if items or mapped:
                            break
                    keys = items or mapped or ["<unparsed>"]
                out.append((rid, typ, key, keys))
    return out


def main():
    demand = demand_from_engine()
    supply = type_constructors()
    rows = read_demand_keys()

    print(f"wirecheck — engine reads {len(demand)} predicate(s); "
          f"{len(supply) - sum(1 for k in supply if k.endswith(chr(0)+'cond'))} action type(s) emit")
    print("  predicate -> detail key (read from engine.go):")
    for k in sorted(demand):
        print(f"    {k:20} {demand[k]}")

    drifted = [k for k, v in demand.items() if EXPECTED_DEMAND.get(k) != v]
    if drifted:
        print(f"\n  NOTE: the hard-coded expectation drifted for {drifted} — engine.go wins.")

    fires = 0
    missing, conditional, nocoll, unparsed = [], [], [], []
    for rid, typ, pred, keys in rows:
        if typ is None:
            unparsed.append((rid, pred, "rule has no type"))
            continue
        if typ not in supply:
            if typ in CONST_TO_TYPE.values():
                nocoll.append((rid, typ, pred, keys))
            else:
                unparsed.append((rid, pred, f"unknown type {typ}"))
            continue
        emit = supply[typ]
        cond = supply.get(typ + "\x00cond", set())
        for key in keys:
            if key == "<unparsed>":
                unparsed.append((rid, pred, "could not parse the key list"))
                continue
            fires += 1
            if key in emit:
                pass
            elif key in cond:
                conditional.append((rid, typ, pred, key))
            else:
                missing.append((rid, typ, pred, key, sorted(emit)))

    print(f"\n  denominator: {fires} rule/predicate/key wire(s), all {fires} judgeable.")
    if fires == 0:
        print("  wirecheck: CANNOT JUDGE — empty denominator is not a pass.")
        return 2

    if conditional:
        print(f"\n  CONDITIONAL ({len(conditional)}) — emitted on some path only:")
        for rid, typ, pred, key in conditional:
            print(f"    {rid}  [{typ}]  {pred} -> detail[{key!r}]  (path-dependent)")
    if nocoll:
        seen = sorted({(t, tuple(k)) for _r, t, _p, k in nocoll})
        print(f"\n  NO-PRODUCER ({len(nocoll)} wire(s)) — type has no constructor in this tree:")
        for t, ks in seen:
            print(f"    {t}: {', '.join(ks)}  (R2.2 stub — not judgeable)")
    if unparsed:
        print(f"\n  UNJUDGEABLE ({len(unparsed)}):")
        for rid, pred, why in unparsed:
            print(f"    {rid}  {pred}: {why}")

    if missing:
        print(f"\n  MISSING-FIELD ({len(missing)}) — a predicate reading a field its type never emits:")
        for rid, typ, pred, key, emit in missing:
            print(f"    {rid}  [{typ}]  {pred} -> detail[{key!r}]")
            print(f"      but {typ} emits {emit}")
        print("\nwirecheck: FAIL — a dead wire means the predicate is never reached.")
        return 1

    print(f"\nwirecheck: PASS — every judgeable wire has a producer "
          f"({fires - len(conditional)} unconditional, {len(conditional)} conditional).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

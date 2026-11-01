#!/usr/bin/env python3
"""jsonfidelity — does a rule-match event survive the wire as the same event?

THE QUESTION
------------
`schema.Detail` is `map[string]any`. On the collector side it is built in Go with
real typed values:

    internal/telemetry/telemetry.go:126     detail["exe"]   = exe        (string)
    internal/telemetry/telemetry.go:142     detail["ppid"]  = r.PPID     (int)
    internal/telemetry/telemetry.go:143     detail["pid"]   = r.PID      (int)
    internal/telemetry/egress.go:154        detail["endpoint"] = ep      (string)

Every one of the 11 fixture scripts in this repo assumes a *string-valued* Detail
map, because every predicate ends in `detailString()` which returns "" for any
non-string. The engine is only ever exercised with hand-built Go maps.

But the value that reaches central did not travel as a Go value. It travelled as
JSON:
    collector --json.Marshal(spoolRecord)--> spool jsonl
    spool     --json encoder--> POST /api/v1/actions
    central   --json.Unmarshal--> map[string]any

and `encoding/json` has exactly one default numeric type: **float64**.

    json.Unmarshal([]byte(`{"ppid":4242}`), &m)
    m["ppid"]  ->  float64(4242)      NOT int(4242)      NOT "4242"

`detailString` switches on `string` and `[]byte` and returns "" for everything
else:

    internal/rules/engine.go:315
        case string:  return t
        case []byte:  return string(t)
        default:      return ""

So the question this file answers is **measured, not argued**: which predicates,
fed the Detail map that actually arrives over the wire instead of the one the
existing tests build, silently stop matching?

WHAT IS BEING CLAIMED, AND WHAT IS NOT
--------------------------------------
CLAIMED: for a numeric detail field read through `detailString`, the JSON
round-trip (not the matcher) is what decides the answer, and a `detail_equals`
on a number can never fire because `detailString(k)` is "" for a float64.

NOT CLAIMED: that the shipped pack is currently broken this way. The pack has
one `detail_equals` rule; whether its field is numeric is checked below, from
the pack, not from memory. A finding that overstates its blast radius to sound
bigger is worse than a small true one.

METHOD
------
1. Encode a Detail map the way each transport does (json.Marshal of a Go value)
   and decode it the way central does (float64 default), in Python, using the
   same rules: int -> int, float -> float, str -> str, list -> list.
   The transcription is `json_default_type()` and it is the whole assumption.
2. Ask `detailString_after_wire()` (transcription of engine.go:307) what the
   engine sees.
3. Judge every predicate that routes through detailString.
"""

import sys

# --- transcription of encoding/json's default decode type ------------------
# JSON has one number type. encoding/json decodes an untyped number into float64
# unless the destination is a typed field. Detail is map[string]any -> float64.

def json_default_type(v):
    """the Go VALUE -> the Go value json.Unmarshal hands back for map[string]any"""
    if isinstance(v, bool):
        return v                     # JSON true/false stay bool
    if isinstance(v, int):
        return float(v)              # number -> float64
    if isinstance(v, float):
        return v                     # float64 -> float64
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return [json_default_type(x) for x in v]
    if isinstance(v, dict):
        return {k: json_default_type(x) for k, x in v.items()}
    return v


# --- transcription of internal/rules/engine.go:307 detailString -------------

def detail_string(detail, key):
    if not detail:
        return ""
    if key not in detail:
        return ""
    v = detail[key]
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        return v.decode()
    return ""


# --- transcription of internal/rules/engine.go:211 anyArgvMatches -----------

def any_argv_matches(globs, argv, exe):
    cands = list(argv)
    if exe:
        cands.append(exe)
    if len(argv) > 1:
        cands.append(" ".join(str(a) for a in argv))
    return any(g_match_any(globs, c) for c in cands)


def g_match_any(globs, s):
    # contains-fallback semantics, same as packcheck.match_one (see
    # FINDING-2026-10-28-contains-fallback-on-paths.md); only used here for the
    # shape of the answer, not for glob correctness.
    for g in globs:
        core = g.strip("*")
        if core and core in s:
            return True
    return False


# --- the fixtures ----------------------------------------------------------
# (label, detail as BUILT by the collector, predicate, declared_match_after_wire)
# declared = judged with the wire-decoded map, which is what the engine sees.

def det(**kw):
    return dict(kw)


ROWS = [
    # ---- strings survive untouched: the control --------------------------
    # NOTE: the shipped pack has exactly one detail_equals rule and its field is
    # `novel`, set by internal/telemetry/scope.go as the STRING "true". The
    # numeric-key rows below are latent, not live. This file would be dishonest
    # if it implied otherwise -- see the pack scan at the end of main().
    ("str/detail_equals",
     det(exe="bash"), ("detail_equals", "exe", "bash"), True,
     "CONTROL: strings are the only type detailString accepts, so a string field is unaffected"),

    ("str/path_glob_any",
     det(path="/etc/crontab"), ("detail_glob_any", "path", ["*/etc/crontab"]), True,
     "CONTROL: string + glob is unaffected by the wire"),

    # ---- numbers lose their type ----------------------------------------
    ("int/detail_equals-as-written",
     det(ppid=4242), ("detail_equals", "ppid", "4242"), False,
     "THE FINDING: a rule author writing detail_equals: {ppid: \"4242\"} can NEVER fire — "
     "detailString(float64(4242)) is \"\""),

    ("int/detail_equals-bare-yaml",
     det(ppid=4242), ("detail_equals", "ppid", 4242), None,
     "NOT A MATCH PREDICATE: yaml would give an int and DetailEquals is map[string]string, "
     "so this fails at load, not at match — recorded so the two paths are not confused"),

    ("int/detail_non_empty",
     det(ppid=4242), ("detail_non_empty", "ppid", None), False,
     "detail_non_empty on a numeric field is ALWAYS FALSE — the field is present and the "
     "engine still calls it empty"),

    ("int/detail_non_empty_any",
     det(ppid=4242, pid=77), ("detail_non_empty_any", ["ppid", "pid"], None), False,
     "OR over numeric keys is always false: every key reads \"\" through detailString"),

    ("int/detail_glob_any",
     det(ppid=4242), ("detail_glob_any", "ppid", ["*42*"]), False,
     "a glob over a numeric field can never match"),

    ("int/an-int-that-looks-like-a-string",
     det(status=5), ("detail_equals", "status", "5"), False,
     "the trap in its purest form: the value IS five, the rule says \"5\", and the engine "
     "sees an empty string"),

    # ---- argv: the list element type matters too ------------------------
    ("argv/strings",
     det(exe="pip", argv=["pip", "install", "evil"]),
     ("argv_any", None, ["*pip install*"]), True,
     "CONTROL: a string argv still joins into the phrase line"),

    ("argv/numeric-element",
     det(exe="kill", argv=["kill", 9, "1234"]),
     ("argv_any", None, ["*kill 9*"]), None,
     "OPEN: anyArgvMatches joins with %v in Go (fmt %v of float64(9) is \"9\", not \"9.0\"), "
     "but a Python join of the decoded list gives \"kill 9 1234\" too — the join HIDES the "
     "float. Recorded as a divergence I cannot settle without Go."),

    # ---- the one that decides the blast radius --------------------------
]


def judge(detail, pred, declared):
    kind = pred[0]
    wire = json_default_type(detail)
    if kind == "detail_equals":
        _, k, want = pred
        return detail_string(wire, k) == want
    if kind == "detail_non_empty":
        _, k, _ = pred
        return detail_string(wire, k) != ""
    if kind == "detail_non_empty_any":
        _, keys, _ = pred
        return any(detail_string(wire, k) != "" for k in keys)
    if kind == "detail_glob_any":
        _, k, globs = pred
        return g_match_any(globs, detail_string(wire, k))
    if kind == "argv_any":
        _, _, globs = pred
        argv = [a for a in wire.get("argv") or []]
        return any_argv_matches(globs, argv, detail_string(wire, "exe"))
    raise SystemExit(f"unknown predicate {kind}")


def main():
    fails = 0
    judged = 0
    for (label, detail, pred, declared, why) in ROWS:
        if declared is None:
            print(f"open  {label}\n      {why}")
            continue
        if pred is None:
            continue
        got = judge(detail, pred, declared)
        judged += 1
        if got != declared:
            fails += 1
            print(f"FAIL  {label}: declared={declared} got={got}\n      {why}")
    print(f"ok {judged} wire-fidelity rows judged, {fails} failures ({len(ROWS) - judged} recorded as open/not-a-predicate)")

    # ---- what does the SHIPPED pack ask of numeric fields? --------------
    print("-- shipped pack --")
    try:
        import pathlib
        import yaml
        pack = pathlib.Path(__file__).resolve().parents[1] / "rules" / "carbonato.yaml"
        doc = yaml.safe_load(pack.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read pack ({e}); a missing pack is not a pass")
        return 1

    # numeric detail keys any producer can emit, read from the telemetry layer
    numeric_keys = {"pid", "ppid"}
    stringish_equals = 0
    numeric_predicates = 0
    for r in doc["rules"]:
        m = r.get("match") or {}
        for k in (m.get("detail_equals") or {}):
            if k in numeric_keys:
                numeric_predicates += 1
                print(f"FAIL {r['id']}: detail_equals on numeric field {k!r} can never fire")
            else:
                stringish_equals += 1
        for k in (m.get("detail_non_empty") or []):
            if k in numeric_keys:
                numeric_predicates += 1
                print(f"FAIL {r['id']}: detail_non_empty on numeric field {k!r} is always false")
        for k in (m.get("detail_glob_any") or {}):
            if k in numeric_keys:
                numeric_predicates += 1
                print(f"FAIL {r['id']}: detail_glob_any on numeric field {k!r} can never match")

    if numeric_predicates:
        fails += numeric_predicates
    else:
        print(f"ok   no shipped rule predicates on a numeric field "
              f"({stringish_equals} detail_equals rule(s), all on string fields) — "
              f"the defect is LATENT in the pack, LIVE in the engine")

    if fails:
        print(f"\nJSON FIDELITY RED: {fails}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

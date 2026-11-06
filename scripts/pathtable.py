#!/usr/bin/env python3
"""pathtable — regenerate the Go fixture tables FROM the Python probe scripts.

WHY THIS EXISTS (beat 777): I wrote `internal/rules/pathfixture_engine_test.go` by
hand-typing rows from memory. I got 76 rows, one of them invented (`*/etc/hosts`,
which is not in the Python table at all), and I hand-wrote the XFAIL exemption map
with 3 entries when the real map has 12. The Go test then reported "9 undeclared
disagreements" — which was TRUE and was entirely my transcription error, not an
engine defect. A test whose FAILING output is indistinguishable from a real
finding is worse than no test: it would have cost someone a day.

The lesson is not "type more carefully". It is that a hand copy of a table is a
second source of truth, and two sources of truth disagree eventually. So the tables
are now GENERATED, and this script is the generator, checked in.

USAGE
    python3 scripts/pathtable.py                    # regenerate every family
    python3 scripts/pathtable.py --family path      # just the path family
    python3 scripts/pathtable.py --family endpoint  # just the endpoint family
    python3 scripts/pathtable.py --check            # exit 1 if any table is stale

The generated block is delimited by BEGIN/END markers in the Go file; everything
outside them is untouched, so the prose and the assertions stay hand-written.

BEAT 778 — GENERALIZED. The endpoint family (33 rows, 20 globs) had the same gap
the path family had until beat 777: every verdict produced by the Python port and
never put to the compiled engine. Rather than write a second bespoke generator and
a second hand-copied table, the family descriptor lives in FAMILIES below. Adding a
third family is a dict entry, not a new file.

LIMIT, stated not implied: this proves the Go table equals the Python table. It
does NOT prove the Python table is right — that is what the Go test is for, and it
is the only thing that can be wrong in both directions.
"""
import ast
import sys

BEGIN_TMPL = "// BEGIN GENERATED TABLE — scripts/pathtable.py%s"
END = "// END GENERATED TABLE"

# Each family names: the Python probe, the Go test file, the Go row type name, the
# Go table/const identifiers, and which tuple fields carry (glob, value, want).
FAMILIES = {
    "path": {
        "py": "scripts/pathfixture.py",
        "go": "internal/rules/pathfixture_engine_test.go",
        "rowtype": "pathRow",
        "table": "pathRows",
        "valuefield": "path",
        "count": "pathRowCount",
        # pathfixture keeps FIXTURES as a local inside main() and XFAIL at module level
        "glob_index": 0,
        "value_index": 1,
        "want_index": 2,
        "globs_in_tuples": False,
    },
    "endpoint": {
        "py": "scripts/endpointfixture.py",
        "go": "internal/rules/endpointfixture_engine_test.go",
        "rowtype": "endpointRow",
        "table": "endpointRows",
        "valuefield": "endpoint",
        "count": "endpointRowCount",
        "glob_index": 1,
        "value_index": 2,
        "want_index": 3,
        # endpointfixture rows are (label, glob, endpoint, want, note); field 0 is
        # a human label, and the LOOKALIKE census lives in the note text.
        "globs_in_tuples": True,
        "label_index": 0,
    },
    "argv": {
        "py": "scripts/argvfixture.py",
        "go": "internal/rules/argvfixture_engine_test.go",
        "rowtype": "argvRow",
        "table": "argvRows",
        "valuefield": "cmd",
        "count": "argvRowCount",
        # ROWS is module-level and shaped (glob, command, want, category)
        "glob_index": 0,
        "value_index": 1,
        "want_index": 2,
        "globs_in_tuples": True,
        "label_index": 3,
        # the probe's category field is a long sentence; only the leading token is the label
        "label_token": True,
    },
}


def load(name):
    spec = FAMILIES[name]
    tree = ast.parse(open(spec["py"], encoding="utf-8").read())
    fixtures = None
    xfail = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "XFAIL":
            xfail = ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in ("ROWS", "FIXTURES"):
            fixtures = ast.literal_eval(node.value)
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in fn.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FIXTURES":
                fixtures = ast.literal_eval(node.value)
    if fixtures is None:
        raise SystemExit("pathtable[%s]: could not locate FIXTURES in %s" % (name, spec["py"]))
    return fixtures, (xfail or {})


def q(s):
    return s.replace('"', '\\"')


def render(name):
    spec = FAMILIES[name]
    fixtures, xfail = load(name)
    gi, vi, wi = spec["glob_index"], spec["value_index"], spec["want_index"]
    rows = [(r[gi], r[vi], bool(r[wi]), r) for r in fixtures]
    label = spec.get("label_index")

    if name == "path":
        keys = {(g, p) for g, p, _w, _raw in rows}
        orphan = [k for k in xfail if k not in keys]
        if orphan:
            raise SystemExit("pathtable[path]: %d XFAIL entries name rows not in FIXTURES: %r"
                             % (len(orphan), orphan[:3]))

    out = [BEGIN_TMPL % ("" if name == "path" else " --family " + name),
           "// Generated from %s by scripts/pathtable.py — DO NOT EDIT BY HAND." % spec["py"],
           ""]
    out.append("var %s = []%s{" % (spec["table"], spec["rowtype"]))
    for g, v, w, raw in rows:
        lookalike = ""
        if label is not None:
            field = str(raw[label])
            if spec.get("label_token"):
                # The category field is a sentence, sometimes with commas AND colons
                # ("FALSE NEGATIVE, measured. ..."). The label is the leading run of
                # upper-case words before the first comma or colon -- not split(":")
                # alone, which let a whole sentence through as a category and made the
                # census report zero FALSE NEGATIVE rows that plainly existed.
                head = field.split(":")[0].split(",")[0].strip()
                field = head
                la = field.upper() == "LOOKALIKE"
                cat = field.upper()
                lookalike = ', category: "%s"' % q(cat)
            else:
                note = str(raw[-1]) if isinstance(raw[-1], str) else ""
                la = field.upper() == "LOOKALIKE" or note.upper().startswith("LOOKALIKE")
                lookalike = ", lookalike: %s" % ("true" if la else "false")
        out.append('\t{glob: "%s", %s: "%s", want: %s%s},'
                   % (q(g), spec["valuefield"], q(v), "true" if w else "false", lookalike))
    out.append("}")
    out.append("")

    if name == "path":
        # gofmt aligns map values within a run, so the rendered block must be
        # aligned too -- otherwise every `--check` after a `gofmt -w` reports
        # STALE forever, which is a check that can only cry wolf.
        mkeys = ['{"%s", "%s"}' % (q(g), q(p)) for g, p in xfail]
        if mkeys:
            width = max(len(k) for k in mkeys) + 1
            out.append("var xfailRows = map[[2]string]bool{")
            for k in mkeys:
                out.append("\t" + (k + ": ").ljust(width + 1) + "true,")
            out.append("}")
            out.append("")

    out.append("const %s = %d" % (spec["count"], len(rows)))
    if name == "path":
        out.append("const pathXfailCount = %d" % len(xfail))
    out.append("")
    out.append(END)
    return "\n".join(out) + "\n"


def splice(src, block, gofile, name):
    marker = BEGIN_TMPL % (" --family " + name if name != "path" else "")
    i = src.find(marker)
    j = src.find(END)
    if i < 0 or j < 0:
        raise SystemExit("pathtable: BEGIN/END markers not found in %s" % gofile)
    tail = src[j + len(END):]
    if tail.startswith("\n"):
        tail = tail[1:]
    return src[:i] + block + tail


def main():
    args = sys.argv[1:]
    check = "--check" in args
    names = list(FAMILIES)
    if "--family" in args:
        want = args[args.index("--family") + 1]
        if want not in FAMILIES:
            raise SystemExit("pathtable: unknown family %r (have: %s)"
                             % (want, ", ".join(FAMILIES)))
        names = [want]

    stale = []
    for name in names:
        spec = FAMILIES[name]
        block = render(name)
        src = open(spec["go"], encoding="utf-8").read()
        new = splice(src, block, spec["go"], name)
        fixtures, _xf = load(name)
        if new == src:
            print("ok   %s Go table matches %s (%d rows)" % (name, spec["py"], len(fixtures)))
            continue
        if check:
            stale.append(name)
            print("FAIL %s Go table is STALE — does not match %s" % (name, spec["py"]))
            continue
        open(spec["go"], "w", encoding="utf-8").write(new)
        print("pathtable: rewrote %s — %d rows" % (spec["go"], len(fixtures)))

    if stale:
        print("           run: python3 scripts/pathtable.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

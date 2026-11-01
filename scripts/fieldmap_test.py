#!/usr/bin/env python3
"""fieldmap_test.py — pins which field each rule matches against, and who writes it.

WHY
  Beat 578 claimed the path-blindness of `pathSuffixes` "feeds every path_glob_any, every
  detail_glob_any, and the joined-line of every argv_any." Beat 579 measured it: `path_glob_any`
  never sees a bind spec at all — it reads detail["path"], which exactly one function writes, from
  an audit record's changed target. The claim was too big.

  This test exists so that safety is a CHECKED property instead of a happy accident. If someone
  later teaches another telemetry source to write detail["path"] from something that isn't a single
  filesystem path, these go red and the next finding gets written before the bug ships.

  It also pins the blast radius of the path-blindness at exactly one rule, so a future rule that
  adopts detail_glob_any has to come here and say so on purpose.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
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


def read(*parts):
    with open(os.path.join(PROJ, *parts), encoding="utf-8") as fh:
        return fh.read()



def main():
    pack = read("rules", "carbonato.yaml")
    engine = read("internal", "rules", "engine.go")

    # --- 1. each field is matched against exactly the detail key we believe it is -------------------
    FIELD_SOURCE = {
        "argv_any": 'detailStringSlice(action, "argv")',
        "path_glob_any": 'detailString(action, "path")',
        "endpoint_glob_any": 'detailString(action, "endpoint")',
    }
    for field, expr in FIELD_SOURCE.items():
        ok(f"{field} is matched against {expr.strip()}", expr in engine)

    # --- 2. detail["path"] has exactly ONE writer, and that writer is the audit file-watch ---------
    writers = []
    for root, dirs, files in os.walk(os.path.join(PROJ, "internal")):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for fn in files:
            if not fn.endswith(".go") or fn.endswith("_test.go"):
                continue
            body = open(os.path.join(root, fn), encoding="utf-8").read()
            if re.search(r'"path":\s*target', body) or re.search(r'detail\["path"\]\s*=', body):
                writers.append(os.path.relpath(os.path.join(root, fn), PROJ))
    ok(f'only filewatch.go writes detail["path"] (got {writers})',
       writers == [os.path.join("internal", "telemetry", "filewatch.go")])

    fw = read("internal", "telemetry", "filewatch.go")
    ok('that writer sets "path" to `target`, an audit record target — not a spec',
       '"path":    target,' in fw or '"path": target,' in fw)
    ok("...and target comes from writeTarget(), the single read/write decision point",
       "target, ok := r.writeTarget()" in fw)

    # --- 3. the blast radius is one rule, and it is the one we measured -----------------------------
    blocks = re.split(r"\n  - id: ", pack)
    by_field = {}
    for blk in blocks[1:]:
        rid = blk.split("\n")[0].strip()
        parts = blk.split("\n    match:")
        match = ("match:" + parts[1]) if len(parts) > 1 else ""
        for f in ("argv_any", "path_glob_any", "endpoint_glob_any", "detail_glob_any", "exe_any"):
            if re.search(r"^\s{6}" + f + r":", match, re.M):
                by_field.setdefault(f, []).append(rid)

    ok(f"detail_glob_any is used by exactly one rule (got {by_field.get('detail_glob_any')})",
       by_field.get("detail_glob_any") == ["carbonato.container.host_root_mount"])
    ok("path_glob_any is used by exactly four rules",
       len(by_field.get("path_glob_any", [])) == 4)
    # Seven, not nine: my beat-579 table said 9 by counting rules whose detail carried argv, which
    # double-counts the two that also carry exe_any. The test refused the number I typed from memory.
    ok(f"argv_any is used by exactly seven rules (got {len(by_field.get('argv_any', []))})",
       len(by_field.get("argv_any", [])) == 7)
    ok("20 rules in the pack", len(blocks) - 1 == 20)

    # --- 4. Docker's binds is the only comma-joined value any glob_any field reads ------------------
    docker_go = read("internal", "telemetry", "docker.go")
    ok("docker.go still joins binds with ',' into one string",
       'detail["binds"] = strings.Join(cfg.Binds, ",")' in docker_go)
    ok("no other telemetry writes a comma-joined value as a detail",
       docker_go.count('strings.Join(') == 1)

    print(f"\nfieldmap_test: {ok_n} ok, {bad_n} fail")
    sys.exit(1 if bad_n else 0)


if __name__ == "__main__":
    main()

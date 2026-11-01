#!/usr/bin/env python3
"""severityprobe — the same file, two different alerts, by rule.

`persist.file_write` cannot tell a delete from a write (unlinkprobe, 527). That finding was about one
rule. This asks the pack-wide question the pack's own words invite: **how many rules can fire on the same
file, and do they agree about what happened?**

Concretely: for each candidate file, walk every rule in the pack, ask which of them reach it, and print
the severity each one would attach. Two things come out:

  1. **Overlap** — a file where 2+ rules fire. If an operator dedupes by path (and every SIEM does),
     which severity survives? If the answer depends on rule ordering, the overlap is a defect.
  2. **Severity spread** — for the same file. One file, LOW from one rule and CRITICAL from another is
     not a contradiction if the rules are about different acts (reading vs. writing), but it IS a
     contradiction when both rules are about the same act and read the same field.

Denominator discipline: every candidate is printed with the number of rules that could have reached it
and the number that did. A file none of the rules reach is reported as unreached, not as a pass.

Reads `rules/carbonato.yaml` only. Matching via neargo (transcription of engine.go). No Go, no claims.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(os.path.dirname(HERE), "rules", "carbonato.yaml")

# Files that matter, chosen before looking at which rules exist — one persistence file, one credential
# file, one shell profile, one key, one container-mounted host path. Written down here so the sample
# cannot be bent toward whatever the pack happens to catch.
CANDIDATES = [
    "/etc/cron.d/evil",
    "/root/.ssh/authorized_keys",
    "/root/.aws/credentials",
    "/home/deploy/.bashrc",
    "/etc/systemd/system/evil.service",
    "/etc/shadow",
    "/home/x/.config/openai/api_key",
    "/var/run/docker.sock",
]

RULE_SPLIT = re.compile(r"\n\s*-\s*id:\s*", re.M)


def rules(pack):
    out = []
    for chunk in RULE_SPLIT.split(pack)[1:]:
        rid = chunk.split("\n", 1)[0].strip()
        body = chunk
        sev = re.search(r"^\s*severity:\s*(\w+)", body, re.M)
        typ = re.search(r"^\s*type:\s*([\w.]+)", body, re.M)
        globs = []
        for key in ("path_glob_any", "endpoint_glob_any"):
            sec = re.search(rf"{key}:\s*\n((?:\s*-\s*.+\n?)+)", body)
            if sec:
                globs += [(key, l.strip()[2:].strip().strip('"'))
                          for l in sec.group(1).splitlines() if l.strip().startswith("-")]
        for key in ("path_glob", "endpoint_glob"):
            m = re.search(rf"{key}:\s*[\"']?([^\"'\n]+)", body)
            if m:
                globs.append((key, m.group(1).strip()))
        out.append({"id": rid, "severity": (sev.group(1) if sev else "?"),
                    "type": (typ.group(1) if typ else "?"), "globs": globs})
    return out


def reach(rule, path):
    return [g for _, g in rule["globs"] if neargo.match_one(g, path)]


def main():
    with open(PACK, encoding="utf-8") as fh:
        pack = fh.read()
    rs = rules(pack)
    if not rs:
        print("severityprobe: no rules parsed — blind, not clean", file=sys.stderr)
        return 2

    print(f"severityprobe — {len(rs)} rules, {len(CANDIDATES)} candidate files, "
          f"{len(rs) * len(CANDIDATES)} rule/file pairs\n")
    overlaps = 0
    for path in CANDIDATES:
        hits = [(r, reach(r, path)) for r in rs]
        hits = [(r, g) for r, g in hits if g]
        sevs = sorted({r["severity"] for r, _ in hits})
        flag = "  <-- OVERLAP" if len(hits) > 1 else ("  <-- unreached" if not hits else "")
        if len(hits) > 1:
            overlaps += 1
        print(f"  {path}")
        print(f"    {len(hits)} of {len(rs)} rules reach it; severities {sevs}{flag}")
        for r, g in hits:
            print(f"      {r['severity']:8} {r['type']:14} {r['id']:42} via {g}")
    print(f"\n  denominators: {len(rs)} rules x {len(CANDIDATES)} files = {len(rs) * len(CANDIDATES)} pairs, "
          f"{overlaps} file(s) reached by more than one rule.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

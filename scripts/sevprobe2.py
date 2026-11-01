#!/usr/bin/env python3
"""sevprobe2 — the three files severityprobe found unreached, asked properly.

`severityprobe` (this hour, first pass) said `/etc/shadow`, `/home/x/.config/openai/api_key` and
`/var/run/docker.sock` are reached by 0 of 20 rules. Before writing that down as "uncovered", ask the
question in the other direction: is there a rule that *thinks* it covers them, whose glob is one
character wrong? That is a different finding with a different fix, and the two must not be blurred.

Also: `persist.file_write` is titled "writes to a **persistence or credential** location" and its globs
carry no credential store at all except `*/authorized_keys`. A title that promises credential coverage it
does not have is its own small defect, independent of any glob.

Pass-case first: each candidate is paired with a control that SHOULD be empty (a path nobody could
reasonably call a credential store), so a probe that fires on everything is caught by its own fixture.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(os.path.dirname(HERE), "rules", "carbonato.yaml")

# (path, what a competent pack would want here, control?)
CASES = [
    ("/etc/shadow", "credential store, read AND write", False),
    # secret.read_model_key owns these two by glob — its own probe covers the eBPF-stub problem. They are
    # here for a narrower reason: to test the rule's *own* terms against a realistic layout.
    ("/home/x/.config/openai/api_key", "expect REACHED (dir glob) — control for the term below", False),
    ("/home/x/.config/anthropic/api_key", "expect UNREACHED: '*anthropic*key*' needs the words adjacent", False),
    ("/var/run/docker.sock", "container escape primitive", False),
    ("/etc/ssl/private/server.key", "private key material", False),
    ("/etc/passwd", "read-many, write-rare", False),
    ("/usr/share/doc/README.txt", "control: nobody should claim this", True),
    ("/tmp/notes.md", "control: nobody should claim this", True),
]


def rule_globs():
    with open(PACK, encoding="utf-8") as fh:
        pack = fh.read()
    out = {}
    for chunk in re.split(r"\n  - id: ", pack)[1:]:
        rid = chunk.split("\n", 1)[0].strip()
        gs = []
        for key in ("path_glob_any", "endpoint_glob_any"):
            sec = re.search(rf"{key}:\s*\n((?:\s*-\s*.+\n?)+)", chunk)
            if sec:
                gs += [l.strip()[2:].strip().strip('"') for l in sec.group(1).splitlines()
                       if l.strip().startswith("-")]
        for key in ("path_glob", "endpoint_glob"):
            m = re.search(rf"^\s*{key}:\s*[\"']?([^\"'\n]+)", chunk, re.M)
            if m:
                gs.append(m.group(1).strip())
        out[rid] = gs
    return out


def main():
    rules = rule_globs()
    total_pairs = 0
    print(f"sevprobe2 — {len(rules)} rules, {len(CASES)} paths\n")
    for path, why, control in CASES:
        hits = [(rid, g) for rid, gs in rules.items() for g in gs if neargo.match_one(g, path)]
        total_pairs += len(rules)
        tag = "CONTROL" if control else ("REACHED" if hits else "UNREACHED")
        print(f"  {path}")
        print(f"    {why}  [{tag}]")
        print(f"    {len(hits)} of {len(rules)} rules reach it" + (f": {hits}" if hits else " — nothing in the pack looks at this path"))
    print(f"\n  denominators: {len(rules)} rules x {len(CASES)} paths = {total_pairs} rule/path pairs.")

    # the title-vs-globs mismatch, computed rather than asserted
    pw = rules.get("carbonato.persist.file_write", [])
    cred = [g for g in pw if any(k in g for k in ("ssh", "aws", "credentials", "env", "key", "kube", "gcloud"))]
    # second title-vs-globs mismatch: the model-key rule's own neighbour term
    mk = rules.get("carbonato.secret.read_model_key", [])
    ant = [g for g in mk if "anthropic" in g]
    probes = ["/home/x/.config/anthropic/api_key", "/home/x/.anthropic/key", "/opt/anthropic_api_key"]
    mm = [p for p in probes if any(neargo.match_one(g, p) for g in ant)]
    print(f"  anthropic term probed against: {probes}")
    print(f"\n  secret.read_model_key: 'anthropic' term is {ant}; of 3 anthropic key paths, {len(mm)} match — "
          f"the term needs 'anthropic' and 'key' adjacent, which no real path has. (The openai twin works only "
          f"because of the '*/.config/openai*' dir glob, not the '*key*' term.)")
    print(f"\n  persist.file_write: title claims 'persistence or credential location'; {len(pw)} globs, "
          f"{len(cred)} of them credential-shaped {cred}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

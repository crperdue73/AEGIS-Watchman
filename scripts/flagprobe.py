#!/usr/bin/env python3
"""flagprobe — for every rule that fires, does the hit live on a FLAG (or flag value)?

WHY
  verb2 (FINDING-2026-10-23-verb2.md) grouped the corpus by (argv[0], first non-flag
  arg) -> 35 keys, 1 ambiguous, and the one ambiguity was the docker-run pair:

      docker run -v /data:/data ...      (ordinary: a bind mount)
      docker run --privileged ...        (attack: privileged container)

  Both have verb2 = ('docker','run'). The discriminator is a FLAG. That was one
  data point. The ladder handed to the pack owner says step (2) is "flag predicates
  for the docker-run class" — but a single ambiguous pair is a reason to suspect a
  class, not a measurement of it. This probe measures it: in the whole corpus, how
  many firing (rule, command) hits are decided by a flag versus by a positional arg,
  and what would a flag-level restriction cost the attacks?

WHAT IT REPORTS
  Per firing rule hit: the token that made it true, classified as
    FLAG     --priv, -e, -d, --format=... (starts with '-', not a bare '-')
    FLAGVAL  the token right after a flag that takes a value (-v /data:/data -> /data:/data)
    EXE      argv[0]
    ARG      any other positional
  Then, per rule, whether ALL of its hits in the corpus are FLAG/FLAGVAL
  (=> a `flag:` restriction could carry it) or whether some hit is positional
  (=> restricting to flags would blind that rule).

HONEST LIMITS
  - FLAGVAL is a heuristic: the "consume one value" set is a short list, not the
    full CLI grammar of every program in the corpus. Flag values that live inside
    --opt=value are attributed to the FLAG itself, which over-credits FLAG.
  - Python port of the matcher (packcheck), not the Go engine. Same limit as
    subjectprobe.
  - Corpus is the fpprobe corpus. A rate here is a property of that list.
  - Reports only. Nothing is changed; whether a `flag:` property belongs in the
    schema is the pack owner's call.

Exit: 0 always unless the pack cannot be parsed (2).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packcheck import parse_pack, PACK  # noqa: E402
from fpprobe import CORPUS, tier  # noqa: E402

# Flags whose following token is a value, not a positional. Deliberately short and
# honest about it: anything missing here shows up as ARG, which makes this probe
# CONSERVATIVE against its own thesis.
VALUE_FLAGS = {
    "-v", "--volume", "-e", "--env", "-p", "--publish", "-name", "--name",
    "-t", "-it", "-i", "--user", "-u", "--entrypoint", "-w", "--workdir",
    "-o", "--output", "-f", "--file", "--format", "--exclude", "--include",
    "-m", "--mode", "-c",
}


def is_flag(tok):
    return tok.startswith("-") and tok != "-" and tok != "--"


def classify(argv, token):
    """Classify the token that matched, in the context of its argv."""
    try:
        idx = argv.index(token)
    except ValueError:
        return "LINE"  # matched only via the joined line
    if idx == 0:
        return "EXE"
    if is_flag(token):
        return "FLAG"
    prev = argv[idx - 1]
    if is_flag(prev) and prev.split("=")[0] in VALUE_FLAGS:
        return "FLAGVAL"
    if is_flag(prev) and "=" not in prev:
        # Unknown flag: be generous and still call its value flag-borne, because
        # the ambiguity we are chasing is "the flag is the discriminator".
        return "FLAGVAL"
    return "ARG"


def hits_for(rules, argv):
    """(rule, glob, tier, candidate, kind) for every distinct match on this argv."""
    joined = " ".join(argv)
    out, seen = [], set()
    for rid, globs in rules:
        for c in list(argv) + [joined]:
            for g in globs:
                t = tier(g, c)
                if not t:
                    continue
                key = (rid, g, t, c)
                if key in seen:
                    continue
                seen.add(key)
                # A joined-line match is MADE of its argv tokens. If the joined
                # match cannot be pinned to a token it is genuinely LINE; but do not
                # let the line shadow a token we can name, or a rule that fires
                # purely on a flag value reports as POSITIONAL. (First run of
                # flagprobe_test caught exactly this: /data:/data landed in LINE and
                # the probe's own pass case failed.)
                kind = classify(argv, c)
                if kind == "LINE":
                    parts = c.split()
                    for tok in parts:
                        hk = classify(argv, tok)
                        if hk != "LINE":
                            kind = hk
                            c = tok
                            break
                out.append((rid, g, t, c, kind))
    return out


def main():
    try:
        with open(PACK, encoding="utf-8") as fh:
            pack = fh.read()
    except OSError as e:
        print(f"flagprobe: cannot read pack: {e}", file=sys.stderr)
        return 2
    rules = parse_pack(pack)
    if not rules:
        print("flagprobe: parsed 0 rules — pack unreadable", file=sys.stderr)
        return 2

    per_rule = {}  # rid -> {kind: count}, plus sample lines
    for entry in CORPUS:
        label, argv = entry[0], entry[1]
        for rid, g, t, c, kind in hits_for(rules, argv):
            d = per_rule.setdefault(rid, {"FLAG": 0, "FLAGVAL": 0, "EXE": 0, "ARG": 0, "LINE": 0,
                                          "labels": [], "samples": []})
            if label not in d["labels"]:
                d["labels"].append(label)
            d[kind] += 1
            line = " ".join(argv)
            if line not in d["samples"]:
                d["samples"].append(line)

    print("flagprobe — is the discriminator a FLAG or a POSITIONAL?\n")
    flag_only, mixed = [], []
    for rid in sorted(per_rule):
        d = per_rule[rid]
        positional = d["ARG"] + d["LINE"]
        flags = d["FLAG"] + d["FLAGVAL"]
        verdict = "FLAG-CARRIED" if positional == 0 and flags else "POSITIONAL"
        (flag_only if verdict == "FLAG-CARRIED" else mixed).append(rid)
        print(f"  [{verdict}] {rid}   seen in: {', '.join(d['labels'])}")
        print(f"      flag={d['FLAG']} flagval={d['FLAGVAL']} exe={d['EXE']} "
              f"arg={d['ARG']} line={d['LINE']}")
        for s in d["samples"][:3]:
            print(f"      | {s}")
    print(f"\n  FLAG-CARRIED rules: {len(flag_only)}   POSITIONAL rules: {len(mixed)}")
    print("  A flag-only restriction is safe ONLY for rules in the first list; for any rule")
    print("  in the second it would drop a hit the corpus counts as an attack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

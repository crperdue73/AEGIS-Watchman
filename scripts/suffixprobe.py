#!/usr/bin/env python3
"""suffixprobe — closing last hour's open question, and finding out it was never an engine question.

LAST HOUR I LEFT THIS OPEN: `'*/.config/openai*'` does not reach `/home/x/.config/openai/api_key` under a
faithful `path.Match`, because the glob is exactly two segments and the path has three. I said the engine's
suffix step might rescue it "in a way I have not reproduced". Read `engine.go:296-304` and the answer is
plain: `pathSuffixes` appends `s[i+1:]` at every '/', i.e. it only ever *removes leading* segments.

    /home/x/.config/openai/api_key
      x/.config/openai/api_key        <- 3 segments, glob wants 2
      .config/openai/api_key          <- 3 segments
      openai/api_key                  <- 2 segments, but does not start with '.config/'
      api_key

So no suffix is two segments AND starts with `.config/`. The glob misses — **in Go, exactly as in neargo.**
Last hour's "transcription may be wrong" was wrong; the transcription was right and the rule is what's missed.
`path_suffixes` (neargo) is now a byte-for-byte match of `engine.go:296-304`, which is what makes this a
statement about the engine and not about my instrument.

WHERE THE GLOB DOES LAND — and this is the part I did not expect
  Deeper paths do reach it. `/home/x/.config/openai/providers/api_key` -> suffix
  `.config/openai/providers/api_key` has 4 segments... still not 2. But `/home/x/.config/openai/api_key`
  fails while `x/.config/openai` (2 segments) would pass. Meaning the rule fires on the DIRECTORY-shaped
  record `x/.config/openai` — a path with no key file at all — and *not* on the key file beneath it.
  That is backwards from the rule's purpose, and it is the second time this week a glob in this pack is
  aimed one level off (526: `*/etc/cron.d/*` vs `*/etc/cron*`).

WHY IT IS STILL NOT A PATCH
  Whether it is "dead" or "misspelled" depends on what an auditd/eBPF record for the model-key read actually
  carries as `detail["path"]` — a file path or a directory watch. The eBPF adapter is a stub in this build
  (R2.2), so nothing can answer that here. Record it, hand it over.

Exit 0 = probe ran. Exit 2 = pack unreadable. Never claims a fix.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(os.path.dirname(HERE), "rules", "carbonato.yaml")
RULE = "carbonato.secret.read_model_key"

# Paths a real agent install would have, deepest last. Chosen before looking at the globs' answers.
PATHS = [
    "/home/x/.config/openai/api_key",
    "/home/x/.config/openai/completions/api_key",
    "/root/.config/openai/api_key",
    "/etc/xdg/.config/openai/api_key",
    "x/.config/openai",
    "/home/x/.config/openai",
    "/home/x/.config/openai/providers/api_key",
    "/home/x/.openai/api_key",
    "/etc/openai/api_key",
    "/home/x/.config/anthropic/api_key",
    "/root/.aws/credentials",
    "/home/x/.netrc",
]


def globs():
    with open(PACK, encoding="utf-8") as fh:
        pack = fh.read()
    m = re.search(rf"\n  - id: {re.escape(RULE)}\b(.*?)(?=\n  - id: |\Z)", pack, re.S)
    body = m.group(1) if m else ""
    gs = []
    for key in ("path_glob_any", "endpoint_glob_any"):
        sec = re.search(rf"{key}:\s*\n((?:\s*-\s*.+\n?)+)", body)
        if sec:
            gs += [l.strip()[2:].strip().strip('"') for l in sec.group(1).splitlines() if l.strip().startswith("-")]
    return gs, body


def main():
    gs, body = globs()
    if not gs:
        print("suffixprobe: no globs parsed — blind, not clean", file=sys.stderr)
        return 2
    print(f"suffixprobe — {RULE}: {len(gs)} globs, {len(PATHS)} paths, {len(gs) * len(PATHS)} pairs\n")
    print(f"  {'path':44} {'globs that reach it'}")
    reached = []
    for p in PATHS:
        hits = [g for g in gs if neargo.match_one(g, p)]
        if hits:
            reached.append((p, hits))
        print(f"  {p:44} {len(hits)}  {hits if hits else 'NOTHING'}")

    key = "/home/x/.config/openai/api_key"
    key_hits = [g for g in gs if neargo.match_one(g, key)]
    print(f"\n  denominator: {len(gs) * len(PATHS)} pairs; {len(reached)} of {len(PATHS)} paths reached by any glob.")
    print(f"  the canonical key path {key!r}: {'hit by ' + str(key_hits) if key_hits else 'NOT REACHED by any of the ' + str(len(gs)) + ' globs'}")

    # show the suffix chain that decides it, so the claim is inspectable rather than asserted
    two_and_dotted = [s for s in neargo.path_suffixes(key)
                      if s.count("/") == 1 and s.startswith(".config/")]
    print(f"  suffixes of that path that are 2 segments and start '.config/': {two_and_dotted or 'NONE'}")
    print(f"  suffixes considered: {neargo.path_suffixes(key)}")
    print(f"  -> the glob needs 2 segments; the path only offers 3+: a MISS in Go, not a port artefact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

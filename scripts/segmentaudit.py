#!/usr/bin/env python3
"""segmentaudit — the pattern question: how many of this pack's globs are aimed one level off?

Two findings today, in two different rules, said the same thing:
  - 526: `*/etc/cron*` misses `/etc/cron.d/evil` — it matches the *directory*, not a file inside it.
  - 529: `*/.config/openai*` misses `/home/x/.config/openai/api_key` and fires on `~/.config/openai` itself.
Two in one day is a coincidence. Three is a habit. So this asks the pack-wide question instead of relying on
stumbling across the next one: **for every glob in `rules/carbonato.yaml`, is the path it names a FILE or a
DIRECTORY?**

HOW IT DECIDES (rule stated before it is applied, so the classifier can be argued with)
  A glob whose final segment is `*` (i.e. ends in `/*`) names a file inside a directory: `*/etc/cron.d/*`.
  A glob whose final segment is a literal or a partial wildcard (`cron*`, `credentials`, `openai*`) names a
  *thing*, and whether that thing is a file or a directory is exactly the ambiguity. Three labels:
    NEEDLE      the glob has no '/' at all: it is a domain/substring needle, not a path (29 of 55 globs)
    FILE-OK     final segment has a dot-extension literal (`config.json`, `id_rsa*`, `*.pem`) — reads as a file
    DIR-SHAPED  final segment is a literal or partial that also exists as a common DIRECTORY name
                (`cron*`, `.config/openai*`, `authorized_keys` as a dir? no — see below) — aimed at a name that
                could be a directory, so a file inside it is unreachable
    WILD-FILE   ends in `/*` — the unambiguous file-inside-directory form
  The DIR-SHAPED list is a fixed, written-down vocabulary (below), not a guess per glob, so the same input gives
  the same answer and the reader can delete a word and see the count move.

Output: per glob, its label and the evidence; then the tally over ALL globs with the denominator printed. This
is a survey for the pack owner, not a set of patch proposals.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import neargo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(os.path.dirname(HERE), "rules", "carbonato.yaml")

# Names that are, in practice, DIRECTORIES in a Linux tree. A glob ending in one of these (possibly with a
# wildcard tail) names a container, not a file, and so cannot catch writes to files inside it.
DIR_NAMES = {
    "cron", "cron.d", "cron.daily", "cron.hourly", "cron.weekly", "system", "systemd", "config",
    "openai", "anthropic", "ssh", "aws", "kube", "gcloud", "docker", "credentials", "ssh_config",
    "bin", "sbin", "tmp", "run", "var", "etc", "root", "home", "proc", "sys",
}
# Extensions / suffixes that read as files even when the final segment is wildcarded.
FILEISH = (".json", ".pem", ".key", ".crt", ".conf", ".service", ".sh", ".py", ".go", ".env", ".txt",
           ".yml", ".yaml", ".toml", ".ini", "_key", "-key", "id_rsa", "id_ed25519", "authorized_keys",
           "api_key", "credentials.json", "config", ".netrc", ".git-credentials")


def pack_globs():
    with open(PACK, encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for rid in re.findall(r"\n  - id: (\S+)", text):
        body = re.search(rf"\n  - id: {re.escape(rid)}\b(.*?)(?=\n  - id: |\Z)", text, re.S).group(1)
        gs = []
        for key in ("path_glob_any", "endpoint_glob_any"):
            sec = re.search(rf"{key}:\s*\n((?:\s*-\s*.+\n?)+)", body)
            if sec:
                gs += [l.strip()[2:].strip().strip('"') for l in sec.group(1).splitlines()
                       if l.strip().startswith("-")]
        for key in ("path_glob", "endpoint_glob"):
            m = re.search(rf"^\s*{key}:\s*[\"']?([^\"'\n]+)", body, re.M)
            if m:
                gs.append(m.group(1).strip())
        for g in gs:
            out.append((rid, g))
    return out


def classify(glob):
    segs = [s for s in glob.split("/") if s]
    # A glob with no '/' at all is not a path — it is a needle (domain, filename fragment, token) tested by
    # match_one's basename/contains steps. Classifying it as a path segment is my first-run mistake: it labelled
    # 29 domain globs UNCLEAR and made the survey look like it had found 40 problems. Checked before segmenting.
    if "/" not in glob:
        return "NEEDLE", "no '/' — a substring/domain needle, not a path segment"
    if "//" in glob:
        return "UNCLEAR", "double slash — needs a human eye"
    if not segs:
        return "UNCLEAR", "no segments"
    last = segs[-1]
    if last == "*":
        return "WILD-FILE", "ends '/*' — file inside a directory, unambiguous"
    tail = last.lstrip("*")
    low = tail.lower()
    if any(low.endswith(ext) or low == ext.lstrip(".") for ext in FILEISH):
        return "FILE-OK", f"final segment {last!r} reads as a file"
    bare = low.rstrip("*")
    if bare in DIR_NAMES or tail == "":
        return "DIR-SHAPED", f"final segment {last!r} names a directory"
    return "UNCLEAR", f"final segment {last!r} — neither a known file suffix nor a known directory"


def main():
    pairs = pack_globs()
    if not pairs:
        print("segmentaudit: no globs parsed — blind, not clean", file=sys.stderr)
        return 2
    rules_with_globs = len({r for r, _ in pairs})
    tally = {}
    print(f"segmentaudit — {rules_with_globs} of 20 rules carry path/endpoint globs; "
          f"{len(pairs)} globs total\n")
    cur = None
    for rid, g in pairs:
        label, why = classify(g)
        tally[label] = tally.get(label, 0) + 1
        if rid != cur:
            print(f"  {rid}")
            cur = rid
        print(f"    {label:11} {g:46} {why}")
    print(f"\n  tally over {len(pairs)} globs: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    dirs = tally.get("DIR-SHAPED", 0)
    print(f"  DIR-SHAPED {dirs} of {len(pairs)} = {100 * dirs / len(pairs):.0f}% — globs that name a directory "
          f"and therefore cannot catch a file inside it.")
    needle = tally.get("NEEDLE", 0)
    paths = len(pairs) - needle
    print(f"  Of the {len(pairs)} globs, {needle} are not paths at all (no '/') and cannot be segment-aimed; the "
          f"survey applies to {paths}.")
    print(f"  DIR-SHAPED among the {paths} path globs: {dirs} = {100 * dirs / paths if paths else 0:.0f}%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

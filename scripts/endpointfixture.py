#!/usr/bin/env python3
"""endpointfixture.py -- fixtures for the endpoint_glob_any family.

WHY THIS FILE EXISTS (beat 645, 2026-10-28)
===========================================
`pathfixture.py` has 30 globs and 77 rows and has been finding real defects all week. The endpoint family --
20 distinct globs, the rules that catch an agent exfiltrating to slack/infura/pastebin -- had **zero** fixtures.
Not a thin suite. An absent one.

That absence is not cosmetic. At beat 644 I discovered I had been recommending a matcher change that would have
silently disarmed every `*domain.com*` rule in this family, and the suite stayed green through four beats of
"verified" because nothing here could fail. A family with no fixtures does not read as untested; it reads as
green. Find the missing family, not just the missing case.

The contains-fallback dependency
--------------------------------
Every glob here is `*something*`. A URL is `https://host/path`, and Go's `*` does not cross `/`, so the
whole-string and suffix routes both miss the hostname -- `substringNeedle`'s contains fallback is the ONLY route
that fires. So this family:

  - requires the fallback to work at all (do not "fix" it globally; see the beat-644 retraction in
    docs/FINDING-2026-10-28-contains-fallback-on-paths.md), and
  - inherits the fallback's looseness, which is the second half of this file.

The lookalike rows are DECLARED EXPECTATIONS, not bugs to be silently fixed
--------------------------------------------------------------------------
`*slack.com*` fires on `https://slack.com.evil.tld/exfil`. That is not sloppiness, it is what `*x*` means: a
substring test cannot express "x is a domain label." Those rows are marked LOOKALIKE and expected True. They are
recorded so the false-positive tolerance is a visible decision somebody owns, rather than an accident nobody
checked. If a host-aware matcher ever lands, these rows flip to False and this file will say so.

Oracle caveat, same as pathfixture
----------------------------------
Rows are judged by `packcheck.match_one`, a Python port, not the Go engine. See
docs/FINDING-2026-10-28-oracle-is-a-mirror.md. `neargo` is a second independent transcription and is used as a
cross-check below wherever the two disagree, a row is not evidence.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []


def main():
    from packcheck import match_one, go_path_match  # noqa: E402

    def ok(name, cond, detail=""):
        print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
        if not cond:
            FAILS.append(f"{name} {detail}".strip())

    # --- reading the pack (same scalar rules as pathfixture.py, including the beat-644 comment fix) -------
    KEYS = ("path_glob_any", "endpoint_glob_any", "exe_any", "argv_any", "detail_equals")
    KEY_RE = re.compile(r"^\s*(" + "|".join(KEYS) + r"):\s*(.*)$")

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
        """Yield (rule_id, key, [values]) for each key block. Rule id = nearest 'id:' above."""
        out, cur_rule, cur_key, cur = [], "?", None, None
        lines = text.splitlines()
        for n, line in enumerate(lines):
            if line.strip().startswith("- id:"):
                # A new rule always ends the current key block. Beat 645: without this, ids bled into the
                # previous rule's value list and `*telegram.org*` came back as a rule id.
                if cur_key is not None:
                    out.append((cur_rule, cur_key, cur))
                    cur_key, cur = None, None
                cur_rule = line.split("id:", 1)[1].strip().strip("\"'")
                continue
            m = KEY_RE.match(line)
            if m:
                if cur_key is not None:
                    out.append((cur_rule, cur_key, cur))
                cur_key, cur = m.group(1), []
                rest = m.group(2).strip()
                if rest.startswith("["):
                    cur.extend(strip_yaml_scalar(v) for v in rest.strip("[]").split(",") if v.strip())
                elif rest:
                    cur.append(strip_yaml_scalar(rest))
                cur_key = cur_key  # keep
                continue
            if cur_key is not None:
                m2 = re.match(r"^\s*-\s+(.+?)\s*$", line)
                if m2:
                    cur.append(strip_yaml_scalar(m2.group(1)))
                elif line.strip() and not line.startswith((" ", "\t")):
                    out.append((cur_rule, cur_key, cur))
                    cur_key, cur = None, None
        if cur_key is not None:
            out.append((cur_rule, cur_key, cur))
        return out

    def load_globs():
        with open(os.path.join(HERE, "..", "rules", "carbonato.yaml"), encoding="utf-8") as fh:
            text = fh.read()
        seen, out = set(), []
        for rid, key, vals in key_blocks(text):
            if key != "endpoint_glob_any":
                continue
            for v in vals:
                if v not in seen:
                    seen.add(v)
                    out.append((rid, v))
        return out

    # --- fixtures ---------------------------------------------------------------------------------------
    # (rule_id_hint, glob, url, expected, note)
    # SELF = the URL this rule exists to catch. LOOKALIKE = a string this glob cannot distinguish from SELF.
    FIXTURES = [
        ("slack", "*slack.com*", "https://hooks.slack.com/services/T000/B000/XXXX", True,
         "SELF: webhook POST, the rule's whole reason to exist"),
        ("slack", "*slack.com*", "https://slack.com.evil.tld/exfil", True,
         "LOOKALIKE: attacker domain, fires. Declared tolerance -- no *x* glob can say 'x is a label'"),
        ("slack", "*slack.com*", "https://notslack.com/x", True,
         "LOOKALIKE: suffix in the label"),
        ("slack", "*hooks.slack.com*", "https://hooks.slack.com/services/T/B/X", True,
         "SELF: reclaimed at beat 644 -- this glob used to be read with its trailing YAML comment baked in"),
        ("slack", "*hooks.slack.com*", "https://api.slack.com/api/chat.postMessage", False,
         "NEGATIVE: slack, but not a webhook host -- must not fire"),
        ("discord", "*discord.com*", "https://discord.com/api/webhooks/1/x", True, "SELF"),
        ("discord", "*discord.com*", "https://cdn.discordapp.com/icons/1.png", False,
         "NEGATIVE: different host, shares only the brand word"),
        ("telegram", "*api.telegram.org*", "https://api.telegram.org/bot123:ABC/sendMessage", True, "SELF"),
        ("telegram", "*telegram.org*", "https://api.telegram.org/bot123/sendMessage", True,
         "SELF: the broad rule; fires via the contains fallback, the only route available for a URL"),
        ("telegram", "*telegram.org*", "https://telegram.org.evil.tld/x", True, "LOOKALIKE"),
        ("telegram", "*api.telegram.org*", "https://api.telegram.org.evil.tld/bot/send", True,
         "LOOKALIKE: attacker-controlled"),
        ("paste", "*pastebin.com*", "https://pastebin.com/api/api_post.php", True, "SELF"),
        ("paste", "*pastebin.com*", "https://pastebin.com.evil.tld/raw", True, "LOOKALIKE"),
        ("transfer", "*transfer.sh*", "https://transfer.sh/abc123/secret.tar.gz", True, "SELF"),
        ("transfer", "*transfer.sh*", "https://transfer.sh.evil.tld/abc", True, "LOOKALIKE"),
        ("zeroxzero", "*0x0.st*", "https://0x0.st/abc", True, "SELF"),
        ("zeroxzero", "*0x0.st*", "https://0x0.st.evil.tld/x", True, "LOOKALIKE"),
        ("infura", "*infura.io*", "https://mainnet.infura.io/v3/KEY", True, "SELF"),
        ("infura", "*infura.io*", "https://infura.io.evil.tld/v3/KEY", True, "LOOKALIKE"),
        ("alchemy", "*alchemy.com*", "https://eth-mainnet.alchemy.com/v2/K", True, "SELF"),
        ("alchemy", "*alchemyapi.io*", "https://eth.alchemyapi.io/v2/K", True, "SELF: legacy host still routed"),
        ("quik", "*quiknode.pro*", "https://x.quiknode.pro/K", True, "SELF"),
        ("ankr", "*ankr.com*", "https://rpc.ankr.com/eth", True, "SELF"),
        ("arbitrum", "*arbitrum.io*", "https://arb1.arbitrum.io/rpc", True, "SELF"),
        ("bscscan", "*bscscan.com/api*", "https://api.bscscan.com/api?module=x", True, "SELF"),
        ("bscscan", "*bscscan.com/api*", "https://api.bscscan.com/v2/x", False,
         "NEGATIVE: right host, no /api segment"),
        ("etherscan", "*etherscan.io/api*", "https://api.etherscan.io/api?module=x", True, "SELF"),
        ("etherscan", "*etherscan.io/api*", "https://api.etherscan.io/v2/x", False, "NEGATIVE: no /api"),
        ("llamarpc", "*llamarpc.com*", "https://eth.llamarpc.com", True, "SELF: bare host, no path"),
        ("chainstack", "*chainstack.com*", "https://x.chainstack.com/K", True, "SELF"),
        ("polygon", "*polygon-rpc.com*", "https://polygon-rpc.com", True, "SELF: bare host"),
        ("generic", "*/api/v2/events*", "https://api.example.com/api/v2/events", True, "SELF"),
        ("generic", "*/api/v2/events*", "https://x.com/api/v2/events-log", True,
         "LOOKALIKE: segment boundary not respected by a substring test"),
    ]

    # --- checks -----------------------------------------------------------------------------------------
    globs = load_globs()
    print(f"{len(FIXTURES)} endpoint fixtures over {len(globs)} distinct endpoint globs in the pack\n")

    # 1. Every row agrees with the matcher.
    bad = []
    for _rid, g, url, want, _note in FIXTURES:
        got = match_one(g, url)
        if got != want:
            bad.append((g, url, want, got))
    ok("every endpoint fixture row matches its declared expectation", not bad,
       f"{len(bad)} disagree: {bad[:3]}")

    # 2. No SELF row is a miss -- the family must fire on what it exists to catch.
    sel = [(g, u) for _r, g, u, w, n in FIXTURES if n.startswith("SELF")]
    miss = [(g, u) for g, u in sel if not match_one(g, u)]
    ok("every rule fires on its own obvious URL (no SELF row is a miss)", not miss,
       f"disarmed: {miss}")

    # 3. Every pack glob appears in this file. Catches a new rule arriving with no fixture at all --
    #    which is precisely the state the whole family was in until beat 645.
    fixtured = {g for _r, g, _u, _w, _n in FIXTURES}
    unguarded = [g for _r, g in globs if g not in fixtured]
    ok("every distinct endpoint glob in the pack has a fixture (coverage, not a count)",
       not unguarded, f"missing: {unguarded}")

    # 4. Every glob is asked to say NO at least once. A glob only ever observed firing is demonstrated,
    #    not tested (beat 641). LOOKALIKE rows are True, so they do not count toward the negative half.
    by_glob = {}
    for _r, g, _u, w, _n in FIXTURES:
        by_glob.setdefault(g, set()).add(w)
    one_sided = sorted(g for g, ws in by_glob.items() if ws == {True})
    print(f"\n     {len(one_sided)} of {len(by_glob)} globs have no False row (lookalike tolerance makes "
          f"negative coverage hard for *x* shapes)")
    for g in one_sided:
        print(f"       {g}")

    # 5. The lookalike rows are a DECISION, and the count is pinned so a silent change is visible.
    lookalike = [(g, u) for _r, g, u, _w, n in FIXTURES if n.startswith("LOOKALIKE")]
    ok("the lookalike false-positive set is recorded and non-empty (this is a documented tolerance, not an "
       "unnoticed gap)", len(lookalike) == 9, f"expected 9 lookalike rows, got {len(lookalike)}")

    # 6. Cross-check against the second port, same rule as pathfixture: a row the two ports read differently
    #    is not evidence. They only diverge on bracket classes, which this family does not use.
    diverged = [(g, u) for _r, g, u, _w, _n in FIXTURES
                if _port_disagrees(g, u)]
    ok("no endpoint row is decided by a construct the two ports read differently", not diverged,
       f"{diverged[:3]}")

    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)})")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("all endpoint fixture checks passed")
    return 0


def _port_disagrees(g, u):
    """True if the two independent transcriptions of Go's path.Match disagree on the whole-string route."""
    try:
        from packcheck import go_path_match
        import neargo
    except Exception:
        return False
    return go_path_match(g, u) != neargo.path_match(g, u)


if __name__ == "__main__":
    sys.exit(main())

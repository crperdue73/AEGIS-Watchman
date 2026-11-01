#!/usr/bin/env python3
"""baselinekey — "baseline" is keyed by a string built from a path that may be relative.

BEAT 729. Beat 728's closing note said the next unexamined noun was *baseline*. Opened it.
The noun is fine; the **key encoding** underneath it is where the defect is.

THE MEASUREMENT
---------------
    internal/central/store.go:26   func (k AgentKey) String() string { return k.HostID + ":" + k.Root }
    internal/central/store.go:56   func parseAgentKey(s string) (AgentKey, bool) {
                                       if i := strings.Index(s, ":/"); i >= 0 { ... }   // absolute root
                                       if i := strings.IndexByte(s, ':'); i >= 0 { ... } // fallback
                                       return AgentKey{}, false
                                   }

The wire form is `hostID + ":" + root`, and the decode splits on the **first** `:/`. The
comment above `parseAgentKey` reasons carefully about a host id that itself contains a
colon (an IPv6 literal) and is **correct for that case**. What it does not consider is
the other end of the string: **a root that is not absolute has no `:/` in it at all.**

So the fallback branch fires — `strings.IndexByte(s, ':')`, the first colon anywhere — and
splits there. Now the question that decides whether this is a defect or a non-issue,
which I asked before writing it up:

**Can a root be non-absolute, and can a host id contain a colon?**

- Roots: `internal/collector/run.go:23` runs `filepath.Glob(ar.Path)` and stores the
  returned match **verbatim** as `Collected.Root`; `config.validate()`
  (`config.go:122-127`) requires only that `path` is **non-empty**. There is **no**
  `filepath.IsAbs` check anywhere in `internal/` or `cmd/` — measured with a grep that
  returned zero hits. A relative `agent_roots:` entry is accepted, walked, glued
  (`filepath.Glob` keeps it relative), and used as the key's second component.
- Host ids: `-host-id` is a free string (`main.go:29`), and the code's own comment treats
  an IPv6 literal as a real case.

**So yes and yes, and the split then lands in the wrong place.**

WHAT ACTUALLY GOES WRONG — a real collision, not a cosmetic one

Take `host.ID = "::1"` (the IPv6 loopback is the cheapest concrete instance) with two
**relative** roots `ws-a` and `ws-b`:

    AgentKey{"::1", "ws-a"}.String()  ==  "::1:ws-a"
    AgentKey{"::1", "ws-b"}.String()  ==  "::1:ws-b"

Neither contains `:/`, so both take the fallback and split at the **first colon**:

    parseAgentKey("::1:ws-a")  ->  {"", "::1:ws-a"}      // HostID empty, Root keeps the host
    parseAgentKey("::1:ws-b")  ->  {"", "::1:ws-b"}      // HostID empty, Root keeps the host

Round-tripping **mangles** both keys: the host id vanishes into the root and the host
becomes `""`. Those are the *values*; the map *keys* are still the correct strings, so
storage does not merge them. The damage is at the read path:

**`ListBaselines` (store.go:103) is the only consumer of `parseAgentKey`**, and it feeds
the fleet identity roster — `GET /api/v1/agents`, the thing the brief calls the
differentiator. Every relative-root agent on a colon-containing host appears in the
roster as **host `""`**, with the hostname glued onto the path it reports. The roster is
the identity surface, and for these agents it prints an identity that belongs to no host.

And the silent branch makes it worse than a crash: `if !ok { continue }` (`store.go:107`)
**drops** any key it cannot parse. A dropped baseline is an agent that is running, is
detecting, and **is not in the roster** — with no error, no log, no count in the response.

SCOPE, STATED HONESTLY
----------------------
This requires a non-absolute root **or** a colon-containing host id, and today's
shipped config (`collector.yaml`) uses absolute paths with globs, so the *default*
deployment does not hit it. I am not going to inflate that. What makes it worth
recording is three things together:

1. **`config.validate()` is one `IsAbs` call away from preventing it entirely** and
   doesn't make it — and the failure it permits has no error surface.
2. **The comment on `parseAgentKey` shows the author thought about hosts containing
   colons** and got that half right. **The reasoning stops at the delimiter and never
   asks whether the delimiter is guaranteed to exist.** That is the same shape as the
   last five beats, one layer down: not an unexamined noun, an unexamined *assumption
   about the data*.
3. `parseAgentKey` is a **string round-trip** where the in-memory store already holds
   the real `AgentKey`. The encoding exists only because the map is keyed by a string —
   and the SQLite backend (beat 725) keys by columns and never parses. So one backend
   can lose identity in a way the other structurally cannot: **the two backends disagree
   about what an agent is**, which is the exact class of inconsistency the `Store`
   interface exists to prevent.

NOT APPLIED — the honest options, and I am not picking one:
  1. **Reject a non-absolute root** in `config.validate()` (one line, fails at startup,
     loud). Cheapest and I think most correct — but it forbids a config someone may
     legitimately be running today, so it is a behaviour change, Dad's call.
  2. **Change the wire form** to something unambiguous — JSON, or a NUL/length-prefixed
     join. Correct, but it changes the on-disk format of `FileStore`, i.e. a migration.
  3. **Have `ListBaselines` not decode at all** — keep `AgentKey` alongside the value in
     the map, or store `map[AgentKey]`. Removes the parser entirely; the SQLite backend
     already proves the shape works. My favourite on the merits, still his call.
  4. Harden `parseAgentKey` to prefer a suffix-`:/` search. **I would argue against
     this** — it makes an ambiguous encoding more clever instead of less ambiguous, and
     in `"::1:ws-a"` there is no `:/` to find, so it fixes nothing here.

NOT CLAIMED: Go behaviour (source reads; no toolchain, beat 642). NOT CLAIMED: that the
roster *merges* two agents — it does not; it mangles their identity and can drop them.
The measured claim is narrower than the dramatic one, and I am keeping it narrow.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


# --- an executable port of parseAgentKey, used to demonstrate the mangle ----
def parse_agent_key(s):
    i = s.find(":/")
    if i >= 0:
        return (s[:i], s[i + 1:])
    i = s.find(":")
    if i >= 0:
        return (s[:i], s[i + 1:])
    return None


def check_wire_form():
    src = read("internal/central/store.go")
    if re.search(r'func \(k AgentKey\) String\(\) string \{ return k\.HostID \+ ":" \+ k\.Root \}', src):
        ok('A: the wire form is host + ":" + root — an unescaped delimiter inside free-form parts')
    else:
        fail("A: AgentKey.String changed — re-derive this finding")
    if re.search(r'if i := strings\.Index\(s, ":/"\); i >= 0', src):
        ok("A: decode splits on the FIRST ':/' — correct for colon-bearing host ids WITH absolute roots")
    else:
        fail("A: the primary split changed — re-derive")
    if re.search(r'if i := strings\.IndexByte\(s, \':\'\); i >= 0 \{\s*return AgentKey\{HostID: s\[:i\], Root: s\[i\+1:\]\}, true', src):
        ok("A gap: the fallback splits on the FIRST colon anywhere — for any root without ':/' "
           "this lands inside the host id")
    else:
        fail("A: the fallback branch changed — re-derive")


def check_roots_may_be_relative():
    src = read("internal/collector/run.go")
    # M3 (beat 729) proved this check was one-sided: normalising the root to absolute
    # in CollectAll ALSO closes the gap, and my first version stayed green through it.
    # A second route to the fix is still a fix; the check has to see both.
    if re.search(r"filepath\.Abs\(root\)|filepath\.Abs\(ar\.Path\)|Clean\(root\)", src):
        fail("B: CollectAll now NORMALISES a root to absolute — that is a second route to "
             "closing this gap (found by M3, which my first version missed); re-derive")
        return
    if re.search(r"matches, err := filepath\.Glob\(ar\.Path\)", src) and \
       re.search(r"Collected\{Root: root, Inventory: inv\}", src):
        ok("B gap: the root is filepath.Glob's match VERBATIM — a relative pattern yields a "
           "relative root, never normalised to absolute")
    else:
        fail("B: CollectAll no longer stores the glob match verbatim — re-derive")
    cfg = read("internal/collector/config.go")
    m = re.search(r"func \(c \*Config\) validate\(\) error \{(.*?)\n\}", cfg, re.S)
    if not m:
        fail("B: config.validate not found")
        return
    body = m.group(1)
    if 'r.Path == ""' in body:
        ok("B gap: validate() checks only that path is non-empty")
    else:
        fail("B: the empty-path check changed — re-derive")
    if "IsAbs" in body:
        fail("B: validate() NOW rejects non-absolute roots — the gap is CLOSED; re-derive "
             "this finding rather than patching it")
    else:
        ok("B gap: validate() does not require an absolute root, and nothing else does "
           "either (grep filepath.IsAbs across internal/ + cmd/ non-test: 0 hits)")
    hits = 0
    for p in list(ROOT.glob("internal/**/*.go")) + list(ROOT.glob("cmd/**/*.go")):
        if p.name.endswith("_test.go"):
            continue
        if "filepath.IsAbs" in p.read_text(errors="replace"):
            hits += 1
    if hits == 0:
        ok("B gap: filepath.IsAbs appears 0 times in non-test code — no absolute-root "
           "invariant is enforced anywhere")
    else:
        fail("B: filepath.IsAbs appeared in %d non-test file(s) — check whether the "
             "absolute-root invariant now holds; re-derive" % hits)


def check_the_mangle_is_real():
    """Not asserted from memory: run the port over concrete keys and require the mangle."""
    cases = [
        # (host, root, expect_compile_free_roundtrip)
        ("web-01", "/home/u/.openclaw", True),    # absolute: ':/' present, must survive
        ("::1", "ws-a", False),                       # colon host + relative root: mangles
        ("::1", "ws-b", False),
        ("::1", "/home/x", True),                     # colon host + ABSOLUTE root: survives
    ]
    bad = []
    for host, root, want in cases:
        wire = host + ":" + root
        got = parse_agent_key(wire)
        survived = got == (host, root)
        if survived != want:
            bad.append((host, root, wire, got, want))
    if bad:
        fail("C: the port does not reproduce the claimed behaviour for %s" % bad)
        return
    ok("C: port reproduces all 4 cases — colon-host+absolute survives, colon-host+relative "
       "mangles to HostID='' with the host glued onto the root")

    # the two distinct relative keys must both mangle to the SAME empty host
    a = parse_agent_key("::1:ws-a")
    b = parse_agent_key("::1:ws-b")
    if a[0] == "" and b[0] == "" and a != b:
        ok("C: two distinct agents ('::1:ws-a', '::1:ws-b') both decode to HostID='' — the "
           "fleet roster shows two entries belonging to NO host, each carrying the hostname "
           "inside the path it prints")
    else:
        fail("C: the mangled-shape expectation changed: %r vs %r" % (a, b))


def check_ok_false_is_silent():
    src = read("internal/central/store.go")
    b = src[src.find("func (m *MemoryStore) ListBaselines"):]
    b = b[:b.find("\n}\n")]
    if re.search(r"key, ok := parseAgentKey\(k\)\s*if !ok \{\s*continue\s*\}", re.sub(r"[\t\n]+", " ", b)):
        ok("D gap: an unparseable key is DROPPED with `continue` — an agent that is running "
           "and detecting silently vanishes from the roster, with no error and no count")
    else:
        fail("D: the ListBaselines drop-on-unparseable branch changed — re-derive")


def check_sqlite_disagrees():
    """The Store interface exists to make backends interchangeable. Do they agree on identity?"""
    sq = ROOT / "internal/central/eventstore_sqlite.go"
    if not sq.exists():
        ok("E: no sqlite eventstore here; skipping the backend-divergence clause")
        return
    s = sq.read_text(errors="replace")
    if "parseAgentKey" in s:
        fail("E: the SQLite backend now parses the string key too — re-derive the "
             "divergence claim (it may now diverge the other way)")
    else:
        ok("E: the SQLite backend never uses parseAgentKey — it keys by columns, so it "
           "cannot lose identity this way. One backend can, the other structurally cannot: "
           "the two disagree about what an agent IS, which is what Store exists to prevent")


def check_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01j-baseline-key-encoding-mangles-relative-roots.md"
    if not doc.exists():
        fail("F: the finding doc is missing")
        return
    t = doc.read_text()
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("does not hit it", "the honest scope limit (default config is safe)"),
        ("narrower than the dramatic one", "the explicit refusal to overstate"),
    ]:
        if phrase in t:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_wire_form()
    check_roots_may_be_relative()
    check_the_mangle_is_real()
    check_ok_false_is_silent()
    check_sqlite_disagrees()
    check_doc_scope()
    print()
    if FAILS:
        print("baselinekey: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("baselinekey: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

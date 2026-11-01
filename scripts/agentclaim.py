#!/usr/bin/env python3
"""agentclaim — the key is a pair, and I only ever checked one half.

BEAT 728. Beats 726/727 were about `host.ID`. `AgentKey` is `(HostID, Root)` and
`Agent.ID` is *also* a body field — so the obvious question is whether the agent half
carries the same defect. It does, and one consequence is worse than the host's because
**it is reachable on the default configuration with a rule that already exists.**

THE MEASUREMENT
---------------
`internal/rules/engine.go:99`:

    if m.AgentID != "" {
        if agent == nil || agent.ID != m.AgentID { return false }
    }

and `cmd/collector/main.go:108-111`:

    var l3agent *schema.Agent
    if len(collected) == 1 {
        l3agent = &schema.Agent{ID: filepath.Base(collected[0].Root), Root: collected[0].Root}
    }

So when the collector watches **exactly one** agent root — the common case, and the
comment says so — `l3agent` is non-nil and `agent_id:` rules can fire. When it watches
**two or more**, `l3agent` stays nil, `Engine.Match` is called with nil, and every
`agent_id:` rule returns false. `behavior.go:211` helpfully turns nil into a zero-valued
`agent` and then stamps that `Agent{}` on everything it ships — so central receives
events that genuinely concern an agent, labelled with an empty agent.

`rule.go:261` rejects only a match block with **no** predicates (`isZeroMatch`), so
`match: {agent_id: X}` alone is a *valid, loadable* rule that silently cannot fire in a
multi-agent deployment.

WHAT IS AND IS NOT LIVE TODAY
-----------------------------
I checked before writing this up rather than after: `grep -rn agent_id rules/` → **0
hits**. The shipped pack (`rules/carbonato.yaml`) uses none. So this is a **latent**
false negative, not an active one — *today*. It becomes active the moment anyone writes
that rule, and the pack is exactly where a future author would write it, because
`agent_id` reads like the most natural way to scope a rule to one agent.

Also honest about the comment: `main.go:107` already says *"Multi-agent attribution needs
a per-agent identity file and is deferred."* So **the limitation is known and stated.**
What is *not* stated is the consequence — that a rule opting into the one predicate the
comment admits is deferred **loads green and then never fires**, with no warning at load
and no warning at run. A documented gap and a silent one both look the same from the
alert log: nothing.

WHERE THIS DIFFERS FROM 724/726/727
-----------------------------------
Those were silences. This one is a **false negative with a validation layer that could
have caught it and doesn't**: `Validate` is the natural home for "this rule requires a
capability the collector may not have", and it has no notion of capabilities. Whether
that belongs in Validate is a design call — but "a rule that cannot fire loads clean" is
already the *opposite* of `isZeroMatch`'s stated intent ("would never fire"), so the
precedent exists in this file.

NOT APPLIED — every fix changes load-time strictness for packs that exist in the wild, so
it is Dad's call, and I am listing the options without picking one:
  1. **Fail at load** when `agent_id` is set: the collector refuses to start a multi-agent
     Layer 3 until per-agent identity lands. Strictest, loudest, breaks a config that
     runs (bent) today.
  2. **Warn once at startup**, from the rule census the engine already computes: "N rules
     keyed on agent_id cannot fire with M agent roots." Cheap, honest, easy to ignore.
  3. **Ignore `agent_id` in multi-agent mode** — treat it as "any agent". Silent, and I
     would argue against it: it converts a *silent no-fire* into a *silent over-fire*,
     which is the worse failure in a detector.

WHAT THIS SCRIPT DOES
---------------------
Pins the current shape so any of the three fixes goes red and forces re-derivation:
  A. `agent_id` is compared against a caller/derived ID string (the claim, not a fact)
  B. `l3agent` is nil exactly when there is more than one agent root
  C. `Agent.ID` on the shipped pack is unused (the zero I measured, re-measurable)
  D. `isZeroMatch` accepts `agent_id` alone, so a never-fires rule validates
  E. the doc carries the not-applied marker and the "latent, not active" scope
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


def check_a_agent_id_is_a_string():
    src = read("internal/rules/engine.go")
    if re.search(r'if m\.AgentID != "" \{\s*if agent == nil \|\| agent\.ID != m\.AgentID', src):
        ok("A gap: agent_id compares the rule's expectation to agent.ID — a string, not a "
           "verified identity; and nil fails closed (that half is correct)")
    else:
        fail("A: the agent_id comparison changed — re-derive this finding")
    s = read("internal/schema/schema.go")
    if re.search(r"type Agent struct \{\s*ID\s+string", s):
        ok("A gap: schema.Agent.ID is a plain string with no provenance carrier")
    else:
        fail("A: schema.Agent.ID changed shape — if it now carries provenance, re-derive")


def check_b_l3agent_nil_on_multi():
    src = read("cmd/collector/main.go")
    if re.search(r"if len\(collected\) == 1 \{\s*l3agent = &schema\.Agent\{", src):
        ok("B gap: l3agent is bound only when there is EXACTLY one agent root — so "
           "len>1 leaves it nil and agent_id rules cannot fire")
    else:
        fail("B: the l3agent binding changed — re-derive (if it is now unconditional the "
             "gap may have closed)")
    b = read("internal/collector/behavior.go")
    if re.search(r"a := schema\.Agent\{\}\s*\n\s*if agent != nil \{\s*\n\s*a = \*agent", b):
        ok("B gap: a nil agent becomes a ZERO-VALUED Agent{} and that zero is what ships "
           "to central — events concerning an agent arrive labelled with an empty one")
    else:
        fail("B: the nil->zero agent substitution changed — re-derive")


def check_c_pack_uses_no_agent_id():
    pack = ROOT / "rules/carbonato.yaml"
    if not pack.exists():
        fail("C: the shipped pack is missing")
        return
    n = len(re.findall(r"agent_id", pack.read_text(errors="replace")))
    if n == 0:
        ok("C: the shipped pack uses agent_id 0 times — this is LATENT, not an active "
           "false negative; recording the zero so a future author's first use is visible")
    else:
        fail("C: the pack now uses agent_id %d time(s) — the latent false negative is LIVE; "
             "promote this finding from latent to active" % n)


def check_d_zeromatch_accepts_agent_id():
    src = read("internal/rules/rule.go")
    if re.search(r'return m\.Type == "" && .*&&\s*m\.AgentID == "" && m\.PriorEvent == nil', src, re.S):
        ok("D gap: isZeroMatch counts agent_id as a real predicate — so a rule keyed ONLY "
           "on agent_id validates clean despite being unfireable in multi-agent mode, "
           "against the function's own stated intent")
    else:
        fail("D: isZeroMatch changed — re-derive what validation still accepts")
    if re.search(r'if m := r\.Match; isZeroMatch\(m\) \{\s*return fmt\.Errorf\("rule %q: empty match block \(would never fire\)"', src):
        ok("D gap: the error text is literally 'would never fire' — the file already has "
           "the precedent for rejecting an unfireable rule; it just has no capability model")
    else:
        fail("D: the empty-match rejection or its wording changed")


def check_e_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01i-agent-id-never-fires-multi-agent.md"
    if not doc.exists():
        fail("E: the finding doc is missing")
        return
    t = doc.read_text()
    for phrase, label in [
        ("latent", "the latent-not-active scope"),
        ("NOT APPLIED", "the not-fixed marker"),
        ("deferred", "the acknowledgement that the limitation was already documented"),
    ]:
        if phrase in t:
            ok("E: doc carries %s" % label)
        else:
            fail("E: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_agent_id_is_a_string()
    check_b_l3agent_nil_on_multi()
    check_c_pack_uses_no_agent_id()
    check_d_zeromatch_accepts_agent_id()
    check_e_doc_scope()
    print()
    if FAILS:
        print("agentclaim: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("agentclaim: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

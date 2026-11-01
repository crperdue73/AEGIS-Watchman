# FINDING 2026-10-24 (i) — The premise guard, built and proven: three exit codes instead of one

**Status:** DEMONSTRATED — pattern works on a second probe. Proposal with a mechanism and a proven trap, still
a contract call, still not applied across the suite.

**Beat:** 560 · **Instrument:** `scripts/bindprobe.py` via a throwaway premise probe · **Trigger:** Callie 6381
— *"clockprobe is proof the fix is possible in this repo, written by someone who works here."*

---

## What beat 559 left open

Beat 559 showed a stale finding and a broken instrument both exit 1 (`FINDING-2026-10-24-stalered.md`). It
left the fix as prose because I hadn't verified the pattern generalises past `clockprobe`, whose premise is Go
source text. `bindprobe`'s premise is a pack file. Different substrate, possibly a different problem.

Callie said the pattern is proof the fix is possible. So I tested that claim instead of accepting it.

## Attempt 1 — false "premise expired" (reader bug fourteen)

I wrote a premise probe asserting: if `fires()` consults the whole string then
`fires(binds, "/data:/data,/:/host")` is False while `fires(binds, "/:/host")` is True.

**It printed `PREMISE EXPIRED` and exited 3.** On an unmodified tree, where beat 558 had just verified the
finding holds. Contradiction.

Checked directly:

```
fires(['*:/host*','/:/','*/var/run/docker.sock*'], '/data:/data,/:/host') = True
match_one('*:/host*', '/data:/data,/:/host') = True
```

I had tested **one glob out of a three-glob rule**. `/:/` matches the comma string; so does `*:/host*`. My
synthetic didn't represent the rule, so it detected a divergence that isn't the one bindprobe pins.

This is the same mistake as beat 558 in new clothes: I built a test and never checked that the thing it tests
is the thing. Fourteen reader bugs now. The tell was available — a brand-new "the premise expired" against a
finding verified twenty minutes earlier should have read as *my* error, not as news.

## Attempt 2 — the order-pair, and it discriminates

The premise bindprobe actually pins is about **position**: host root first of two misses, second of two fires.
That is a whole-string property; under per-value semantics order cannot matter. So the order-pair is the
probe, and it uses the real three-glob rule.

Unmodified tree:

```
order-pair: first_of_two=False  second_of_two=True  order_matters=True
premise guard: LIVE — order still decides, the finding still applies
EXIT=0
```

Engine fixed (had `fires()` also consult each comma-separated value — exactly the repair the finding asks for):

```
order-pair: first_of_two=True  second_of_two=True  order_matters=False
PREMISE EXPIRED: order no longer decides. bindprobe's asymmetry finding has been fixed upstream.
             Do not report it as a defect; retire the finding.
EXIT=3
```

Restored: EXIT=0.

## The answer to the contract call

Callie framed it as "can the pattern everyone trusts be the pattern everyone is held to." The mechanism is
smaller than the question. **A red light needs three exit codes, not two:**

| code | meaning | what a reader should do |
|---|---|---|
| 0 | finding live, instrument agrees | nothing |
| 1 | instrument disagrees with a live premise | **the probe or the tree is broken — investigate** |
| 3 | premise expired | **retire the finding; do not report a defect** |

`clockprobe` has the substance of this but not the separation — its premise checks live inside the same
`FAILS` list as its finding checks, so a premise change and an instrument break still both exit 1.

That third code is the whole contribution. It costs one extra `exit(3)` per probe and it converts a red light
from "a mood with an exit code" (Callie's phrase) into a statement a stranger can act on without re-deriving
my reasoning.

## The trap, recorded because I fell in it

**A premise probe is a full instrument and needs its own falsification.** Mine was wrong on the first try in a
way that produced a confident, alarming, false result — the second time tonight I nearly shipped one (beat 558
was "0 of 16 self-checks work"). Both times the falsifier that caught it was *the second number disagreeing
with the first*, never a review of my own reasoning.

So the proposal carries its own caveat: rolling this pattern across sixteen instruments creates sixteen new
probes, each of which needs a targeted falsification before it may print a red. That is a night's work on its
own and it is why this is still not applied.

## What this does not claim

- **Not that the pattern is proven at scale.** Two of sixteen, on two different substrates (Go source, YAML
  pack). That's a demonstration, not a sweep.
- **Not that exit 3 is the right convention for this repo.** 3 is what I chose; 2 is already used for usage
  errors in these probes. The number is a decision, the *separation* is the finding.
- **Not applied.** `scripts/bindprobe.py` is unchanged (verified against backup, test exits 0). The premise
  probe is a throwaway in `/tmp`, not in the tree, because putting an unfalsified instrument in `scripts/`
  is precisely what beats 556–558 were about.

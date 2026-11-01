# FINDING — 2026-10-24 — severity is defended, and the assignment site is why it looks wrong

**Status:** clean pass, nothing to fix. `rules/` and `internal/` untouched.
**Instrument:** `scripts/severityprobe2.py` · **Test:** `scripts/severityprobe2_test.py` (46 checks, green)
**Beat:** 539, written 02:55 ET Saturday

## The hypothesis, and why it was wrong

`internal/rules/engine.go:74`:

```go
ev.Severity = r.Severity        // no check at the assignment site
```

Five hours of findings this week all shared one shape — a value with no guard where it mattered. This looked
like the sixth: an unvalidated string copied straight onto the wire, consumed downstream by `switch` statements
that compare exact strings. A rule authored `Critical` instead of `critical` would, on that reading, alert at
whatever the fallback priority is.

**It does not.** Both ends of the path are guarded, and the middle needs no guard because the front end already
proved the value.

## The three stages

**Load time — `Rule.Validate()` (`rule.go:247`).** Strict, in the codebase's own words: *"a malformed rule that
silently never fires is worse than a startup error."*

```go
switch r.Severity {
case "info", "low", "medium", "high", "critical":
case "":
    return fmt.Errorf("rule %q: severity is required", r.ID)
default:
    return fmt.Errorf("rule %q: invalid severity %q", r.ID, r.Severity)
}
```

Empty is rejected, unknown is rejected, and it is called from the load path (`rule.go:149`, exactly one call
site). So **every rule in memory is already valid**, which is exactly why the assignment site doesn't check.

**Consumption — `severityRank` (`central/alert.go`).** Even an unknown value that somehow reached the wire is
contained, and the code says why:

```go
// severityRank orders severities for "worst in this alert" comparison. Unknown
// severities rank lowest so an unrecognized value never masks a known high.
default:
    return 0
```

That comment is the difference between a defensive default and an accidental one. Someone thought about which
direction the fallback should fail, and picked the safe one.

**The pack — 20 rules: 6 critical, 7 high, 7 medium.** Every value passes the loader model. No quote
contamination. Three levels actually in use, so ranking is doing real work rather than comparing one value to
itself.

## Why a PASS needed more work than a FAIL

"It's defended" is a weak claim if it just means "I didn't find the gap." So the test does two things a defect
test doesn't have to:

1. **Asserts each guard exists in the source** — the switch, the empty case, the default-with-error, the single
   load-path call site, the ranker's `default: return 0`. No guard is assumed from memory.
2. **Removes each guard and confirms the predicate flips.** `defended("Critical", loader=False, ranker=False)`
   must return `False`. It does. So the verdict *can* be falsified, and if someone deletes either guard this
   test goes red rather than quietly agreeing with a weaker program.

The tested property: with **either** guard present, `Critical` is harmless. It takes removing **both** to
produce the silent demotion. One guard is doing the work; the second is the belt.

## Two reader bugs, both caught by the test

- `NameError: re is not defined` — used `P.re` in one place and bare `re` in another.
- `Validate's five valid values` read `['info']` — because `case "info", "low", "medium", "high", "critical":`
  is a **single multi-value case**, and my regex took the first quoted string per line. Fixed to collect every
  quoted value from every case clause.

That second one is the same family as the week's recurring bug — *one logical value, several legal spellings,
and a reader that knew one of them.* Here the spellings are on the Go side: a `case` can list one value or five.

## What this means for the week's tally

Not everything is broken. Six findings of one shape made me expect a seventh, and the honest result is that
severity was designed with a load-time validator and a fail-safe ranker. **A pack that validates its own rules
is a pack that can be trusted to catch its own typos**, and this is the axis where that shows.

Worth stating plainly because the alternative is a reviewer who reads five findings and assumes the whole pack is
suspect: **the defects cluster on the *runtime correlation* path — timing, ordering, ring capacity, producer
gating. The *static authoring* path — rule shape, severity, glob syntax at load — is validated and sound.**

## Reproduce

```bash
python3 scripts/severityprobe2.py        # rc 0; DEFENDED
python3 scripts/severityprobe2_test.py   # 46 checks incl. guard-removal negatives
```

*(Note: `scripts/severityprobe.py`, from an earlier beat, asks a different question — same-file severity
overlap — and is unaffected.)*

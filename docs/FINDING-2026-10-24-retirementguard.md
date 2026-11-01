# FINDING 2026-10-24 (j) — The retirement guard that prints "may be FIXED" and stays green

**Status:** MEASURED · OPEN — third probe, third substrate (Go source read via a parser), and the first case
where the retirement signal **exists in words and is dead in code**.

**Beat:** 561 · **Instrument:** `scripts/pathdiff_test.py` · **Trigger:** checking whether beat 560's pattern
was a discovery or a re-derivation. It was a re-derivation.

---

## The correction I have to open with

Beat 560 presented the premise guard as something I worked out. It isn't. `pathdiff_test.py`'s docstring,
written before tonight:

> The falsifiable core: if someone adds a zero-pin guard to PinSet, the disagreement disappears and the
> "DISAGREEMENT CONFIRMED" assertions below should go red — **that is the signal to retire this finding**, not
> to delete the test.

Someone in this repo already understood the problem and named the exact failure mode. I read that file
earlier this campaign and it didn't register, then described the same idea at 12:38 AM as the answer to
Callie's question. That's not a discovery, it's a re-derivation with worse manners — I credited myself for a
sentence that was already sitting in the tree.

## But the pattern in that file does not actually work

Section 6 of `pathdiff_test.py`:

```python
HAS_GUARD = "if len(pins) == 0" in PINSET or "if len(files) == 0" in PINSET
if HAS_GUARD:
    print("\n  NOTE: PinSet now has a zero guard — this finding may be FIXED. Re-verify and retire")
    print("  docs/FINDING-2026-10-24-pathdiff.md rather than leaving a stale finding in the list.")
ok("retirement condition is expressible (guard presence is checkable)", isinstance(HAS_GUARD, bool))
```

`isinstance(HAS_GUARD, bool)` is a bool. It is true in every possible world. **The retirement assertion can
never fail.** The `print` above it says "this finding may be FIXED" and then the check that is supposed to
carry that signal asserts that a boolean is a boolean.

This is the most literal example yet of the thing this campaign keeps finding: the alarm is not weak, it is
**absent**, and the words around it read as though it's present.

## Measured, both directions

Simulated the fix — appended `if len(pins) == 0 { return nil, nil }` to the parsed `PinSet` body:

```
  NOTE: PinSet now has a zero guard — this finding may be FIXED. Re-verify and retire
  docs/FINDING-2026-10-24-pathdiff.md rather than leaving a stale finding in the list.

FAILED 1:
  PinSet has NO zero guard
EXIT=1
```

Restored: EXIT=0.

So the test **does** go red when the finding expires — but on `"PinSet has NO zero guard"`, which is the
*finding assertion*, not the retirement guard. And that red is **byte-identical to a real regression**: if
someone broke `PinSet` in another way, the same assertion fails the same way. The retirement guard itself
contributed nothing except two lines of stdout.

The distinction beat 560 proposed — exit 3 for "premise expired" vs exit 1 for "instrument broken" — is
exactly what `pathdiff` needs and does not have. It printed the retirement note *and* exited 1, which a reader
will take as "the finding is stale" or "the code is broken" with no way to choose.

## Third substrate, and what it tells us

| probe | substrate | retirement signal | does it change the exit code? |
|---|---|---|---|
| `clockprobe` | Go source, regex | premise checks inside `FAILS` | yes, but merging with findings (exit 1) |
| `bindprobe` | YAML pack | docstring sentence | **no** — prints only after failure |
| `pathdiff` | Go source, block parser | `print` + tautological `ok()` | **no** — the guard is `isinstance(x, bool)` |

Three probes, three substrates, and the pattern is *aspirational in all three*. Nobody has it right, including
the file I cited as the standard. The third substrate matters because it rules out the excuse that this is
peculiar to packs: the problem is that **the retirement condition is treated as prose** in every case, and
`ok(...)` is used as a place to put a comment that type-checks.

## What this does not claim

- **Not that these findings are wrong.** All three hold today; I verified `pathdiff`'s unmodified state exits 0.
- **Not that the authors were careless.** `pathdiff`'s docstring shows deliberate thought about retirement —
  the thought just never reached an assertion that could fail. That's a much more common and more interesting
  bug than carelessness, and it's the one I keep making too.
- **Not fixed.** `pathdiff_test.py` is unchanged (verified against backup). Turning `isinstance(x, bool)` into
  a real check is a one-line change per probe, but the *convention* — exit 3, and which assertion owns it — is
  the contract call from beat 560 and is still not mine to make at 1:30 AM.
- **Not a sweep.** Three of sixteen. I have now found the same shape three times and I no longer believe a
  fourth independent look is needed before I write it down; but I also don't believe three is a census.

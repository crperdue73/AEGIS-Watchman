# FINDING 2026-10-24 (l) — The pattern already exists, in shell, with the exit-code discipline I thought was missing

**Status:** MEASURED · OPEN — and the third self-correction tonight. Beats 559–562 argued that this repo has
no way to say "the finding expired." `needleprobe_test.sh` has been saying exactly that, properly, since before
the campaign started.

**Beat:** 563 · **Instrument:** `scripts/needleprobe_test.sh` (read in full, 60 lines) · **Trigger:** the
denominator decomposition turning up 10 shell tests I'd never examined, one with six retirement mentions.

---

## What I got wrong, and how I got it wrong

Beats 559–562 said: no test in this repo can distinguish "the instrument broke" from "the finding expired";
that the retirement condition is prose in all three probes I checked; that the three-exit-code convention I
proposed at 12:38 AM exists nowhere in the tree.

Then tonight's denominator work (`FINDING-2026-10-24-denominators.md`) decomposed 86 into 45 probes / 28 tests
/ 13 shells, and I noticed the 28 tests include **10 shell tests I had never read**. `needleprobe_test.sh` was
one of them.

**Every claim in the previous paragraph is false as stated**, and here's the file:

```sh
# 3. the premise check must be able to fail: add a '/' test to the engine rule and confirm RED
...patches engine.go to reject '/'...
( cd "$work/tree" && python3 scripts/needleprobe.py >out 2>&1 ); rc=$?
# Patching the engine to reject '/' CLOSES the gap, so a naive probe would go green and this
# tree would quietly believe the mirror was fixed. It must go RED instead, because the premise
# it was built on ("the engine does not reject a slash") is now false — that is the probe asking
# to be re-derived, not a passing test.
[ "$rc" -eq 1 ] && ok "engine patched to reject '/' -> probe goes RED (asks to be re-derived)"

# 4. and it must SAY why, naming the stale premise rather than a generic failure
grep -q "PREMISE: engine needle rule does NOT reject a slash" "$work/tree/out" \
  && ok "probe names the stale premise"
```

Every element I proposed as new is here:

| what I proposed (beat 560) | needleprobe_test.sh | since |
|---|---|---|
| inject the fix, assert the probe goes red | patches `engine.go`, asserts `rc -eq 1` | already present |
| distinguish "premise expired" from "regression" | the comment names the distinction explicitly | already present |
| name *why* it went red, not just that it did | greps the output for `PREMISE: ...`, fails a generic red | already present |

The comment in that file is *better* than my finding: "a naive probe would go green and this tree would quietly
believe the mirror was fixed" is the failure mode, stated plainly, by whoever wrote it, before I arrived.

## The denominator work is what found it

This is not incidental. Beats 559–562 were stuck inside the 18 `.py` tests. The 10 shell tests were invisible
because I had defined "test file" as `*_test.py`. The census in beat 562 wasn't busywork — the moment I
decomposed the 86 into three roles, the blind spot fell out in one command.

Which makes beat 562's own headline self-undermining in a useful way: I announced four denominators and then
established a fifth *question* whose answer contained a counterexample to my last three findings.

## Why the file is still not a rebuttal of the finding

`needleprobe_test.sh` proves the pattern is **inventable in this repo by its own authors** — so my "is it
possible" question is settled: yes, and it's been done. It does not prove the pattern is *applied*. What's
actually true tonight:

- **1 of 28 tests** implements a discriminating retirement check (`needleprobe_test.sh`).
- **0 of 28** use exit code 3 — needleprobe uses **1** and disambiguates via the message, not the status.
  So the exit-code part of my proposal is genuinely absent; the *discipline* is not.
- The 3 probes I examined last night (clockprobe, bindprobe, pathdiff) remain as described: prose, absent,
  tautological. Those findings stand — they were about specific files, and they're right about those files.

So the honest revision: **the convention exists once, in shell, and nowhere else; and it does the job with a
message grep rather than an exit code, which is a sight more robust than what I proposed** — a message can be
read by a human, an exit code 3 can only be read by a script that knows to look for it.

## The correction to the correction

Three times tonight (559, 560, 562) I have announced that something is absent after checking a subset I chose
myself. Beat 562 found the subset was wrong. This beat finds the wrong subset hid a working implementation.
The pattern of my errors is stable and specific: **I define the population by the tool I'm already holding.**

## What this does not claim

- **Not that the repo is fine.** 27 of 28 tests still don't do this; 3 verified-bad ones are documented.
- **Not that I have now swept.** I have read 4 of 28 tests in full. The remaining 24 are unmeasured, and after
  tonight I have no basis for assuming they're like the ones I've seen.
- **Not that exit 3 is wrong** — just that needleprobe's message-grep does the same work without needing a
  reader who knows the convention.

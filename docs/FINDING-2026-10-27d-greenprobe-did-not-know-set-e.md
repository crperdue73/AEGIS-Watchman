# FINDING 2026-10-27d — `greenprobe` called five scripts unable-to-fail because it never learned `set -e`

**Status:** FIXED (reader corrected, `parityreverse.py` ruled, two exemptions declared).
**Found:** beat 620, chasing `greenprobe`'s "2 guards on the exit-0 path with no ruling."
**Instrument:** `scripts/greenprobe.py`.

## The red

```
NO NON-ZERO EXIT (PASS unreachable from failure): 3
   parityreverse.py       UNRULED
   preflight.sh           UNRULED
   sevprobe2.py           ruled
```

I went in expecting two rulings to write. One of them was a lie about the code.

## `preflight.sh` can fail. I proved it, I did not assume it.

`greenprobe` reads source and looks for `exit N`, `sys.exit(N)`, `return N`. `preflight.sh` has none of
those and is 121 lines long, so it looked like a gate that prints PASS no matter what.

It is not. It has `set -eu` at line 22. I ran it in a scratch copy with `make` unavailable:

```
$ sh ./scripts/preflight.sh
rc=127
--- stderr ---
./scripts/preflight.sh: 36: make: not found
```

It aborts at the first failing step and never reaches its `printf 'PASS'`. **The escape mechanism was
there the whole time; the reader did not know its name.**

This is the fifth instance of one pathology in this file, and the file already carries a comment about
the fourth (`$fatal`, the shell idiom that hides the exit code in a variable, which made the repo's most
important gate `publish-scan.sh` read as unable to fail). I added `^\s*set\s+-[a-zA-Z]*e` to the
`NONZERO_EXIT` pattern.

## The rule caught five scripts, not one — and all five are real

Swept every non-test script for "gained can-fail from the `set -e` rule":

| script | line | idiom |
|---|---|---|
| `container-check.sh` | 18 | `set -euo pipefail` |
| `cut-release-check.sh` | 17 | `set -euo pipefail` |
| `preflight.sh` | 22 | `set -eu` |
| `publish-scan-check.sh` | 19 | `set -euo pipefail` |
| `quickstart-check.sh` | 15 | `set -euo pipefail` |

Five gates — including the release cut's guard — were being read as vacuous. Per the beat-616 lesson I
grepped for the mechanism across the tree instead of fixing the one file that was on screen.

## I checked the reader against cases I did *not* want it to match

A regex that makes reds go away is not a fix until it stays quiet where it should:

```
ok   set -eu aborts                                   got=True  want=True
ok   set -euo pipefail                                got=True  want=True
ok   python that truly cannot fail                    got=False want=False
ok   mentions set -e in a comment only                got=False want=False
ok   plain shell, no -e                               got=False want=False
ok   set -e inside an assignment, not a command       got=False want=False
```

6/6. The last three are the ones that matter — a naive `"set -e" in src` would have passed all six and
been wrong three times.

## `parityreverse.py` — a genuine ruling, not a fix

It measures the direction `ci-parity-check.sh` does not enforce (targets preflight runs that CI skips)
and returns 0 by design. Its own last lines say so:

> "This is a MEASUREMENT, not a verdict. ci-parity-check enforces the lower direction only; whether the
> upper direction should also be an error is a policy decision, not a script's."

Exiting non-zero would turn a policy question into a gate. Ruled as an evidence tool, same standing as
`sevprobe2.py`, with the limit stated in the ruling: nothing in CI consumes its output, so a green CI
says nothing about it either way.

## Cascade — `packpresent` was right to go red twice

Correcting this reader made two scripts newly **pass with the rule pack deleted**, and
`packpresent.py` caught both:

1. **`greenprobe.py`** — reads `scripts/` source text, never opens the pack. It is structurally unable
   to notice a broken pack. Declared pack-free with that limit written into the exemption; its own
   output has always said "limit: source read, not behavioural proof." The exemption is that sentence,
   enforced.
2. **`docsindex.py`** — indexes `docs/` against `scripts/`. It looked non-pack-free only because the
   index was stale, so it failed for an unrelated reason. Beat 617 refreshed the index and this check
   truthfully surfaced the script underneath: pack-independent.

Neither is a pass. Both are documented blind spots in a guard that is still worth running, because the
thing `greenprobe` checks — does every guard have a reachable non-zero exit — is pack-independent by
definition.

## Gates

`greenprobe.py`: green, 2/2 exit-0 guards ruled.
`packpresent.py`: all ok — 92 scripts, 16 declared pack-free.
`suitecheck.sh`: **51 suites, 1206 checks, 0 failures.**

Reds remaining: `guardreach.py` (29 guards print no denominator) — the last one.

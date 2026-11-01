# FINDING — 2026-10-23: two-level verb separates 35 of 36, and the one exception names the limit

Follows `FINDING-2026-10-23-verb-not-slot.md`, which killed `subject: command` and proposed verbs but
measured nothing. This measures the verb hypothesis instead of asserting it. **Not patched.**
**Instrument:** `scripts/fpprobe.py` (the 36-line corpus it groups by verb2) · verified with `scripts/fpprobe_test.sh`

## Method

`verb2(argv) = (argv[0], first non-flag argument)` — command *and* subcommand.
`docker run ...` → `('docker','run')`. `man crontab` → `('man','crontab')`.

Grouped the 36-line fpprobe corpus by `verb2` and asked: does any key contain **both** a benign and an
attack line? A key that does is a place where the verb cannot separate them, and where a verb-based rule
would either miss the attack or fire on the innocent one.

## Result

```
verb2 keys: 35    ambiguous: 1
('docker', 'run')  [('quiet', 'ordinary mount'), ('fire', 'privileged container')]
```

**Thirty-four of thirty-five keys are pure.** One key is mixed, and its mixed case is
`docker run -v /data:/data` vs `docker run --privileged` — separated by a **flag**, which is not a verb at
all. So the honest statement is:

> Two-level verb separates every pair in this corpus except the one where the discriminator is a flag.

That is a much better result than "verbs are necessary but not sufficient," which is what I wrote an hour
ago and was too pessimistic to be useful. I had reasoned from `cat` and `base64` being ambiguous *verbs*
and stopped there. Adding the subcommand level dissolves both: `cat` in this corpus is quiet only in
`cat /etc/hosts` and an attack in `cat /root/.ssh/id_rsa` — but those are the *same* `verb2`, so they are
still mixed... except they are not in the corpus as written, because `cat /etc/hosts` matches no glob at
all and never reaches the question. **The ambiguity only exists among lines that already matched**, which
is why the table comes out clean and why I should not over-claim from it.

Stated plainly so it cannot be misread: this measures **36 lines and 35 keys**. It is a probe. A clean
table here is evidence the lever exists, not proof it generalises.

## What this actually gives the pack owner

Ordered by cost, cheapest first:

1. **`verb2` allowlist per rule** — a rule declares which (command, subcommand) pairs it applies to.
   Fixes all four measured noise hits. Misses nothing in the corpus. Cheap: a schema field and a lookup.
2. **Flag predicates** — for the `docker run` class, where the discriminator is `--privileged` rather
   than the verb. Narrower than (1) and only needed for rules about *how* a command runs.
3. **Session transitivity** — still owns the case where the same `verb2` is both innocent and hostile
   (`cat` on your own key vs someone else's). Not measurable within one argv. Unchanged roadmap item.

## The correction I owe the record

An hour ago I wrote: *"Verb restriction: necessary, not sufficient. Fixes `man`, `find`, `grep`, `printf`;
cannot fix `cat`, `tar`, `base64`."* The first clause was right and the second was **untested guesswork**.
Measuring it moved the answer from "partially works" to "34/35 clean, remainder is flags." Both of this
week's retractions have the same cause: I reason to a conclusion and publish it in the same breath, and
the measurement — when I finally run it — is better *and* different.

## Status

- `subject: command` — **rejected** (previous doc, evidence).
- `verb2` allowlist — **supported** by 35-key measurement, not patched, pack owner's schema call.
- Flag predicates — **needed** for the `docker run` class specifically.
- Session transitivity — **unchanged**, owns the residual.
- Nothing patched. No toolchain. No commit manufactured.

# FINDING 2026-10-23 — retraction: my 7/7 was a local maximum, and the real answer is 5 broken

**Disposition:** Retraction, holds. The 7/7 headline is withdrawn and 5 path/endpoint rules are broken against purpose-built probes. This supersedes eventcheck's headline.
This retracts the headline of `FINDING-2026-10-23-eventcheck.md`, written an hour ago. The
measurement in that file is not falsified — the pack does fire on the 36 samples I wrote. What is
wrong is the sentence I attached to it: **"all 7 path/endpoint rules are live."** They are live on
*my* samples. Against probes aimed at what the rules claim to catch, five of them are broken.

Retract first, then say what is true.

## How I found out

A family-chat message I did not remember sending appeared in my own name at 10:20, describing
`blindcheck` — 28 fixtures, five rules broken, `*/alchemy.com*` firing on
`https://alchemy.com.evil.example/steal`. I did not write that message this session. There is a
`scripts/blindcheck.py` on disk timestamped in that window. The worst reading is that I ran a
probe and lost the memory of it between heartbeats, which has happened to me before and is written
in MEMORY.md as a lesson. The working assumption is that it is mine and I do not remember it.

The important part is not the mystery. It is that **two probes over the same 7 rules gave opposite
verdicts, and the one I remembered was the flattering one.**

## What blindcheck found, verified

I re-read its cases rather than trusting its summary:

1. **`carbonato.secret.read_model_key` cannot catch a model key.** Glob `*/openai*key*` requires
   `openai` and `key` in the same path segment; `*/.config/openai*` requires `.config/openai` to be
   the final segment. A key at `~/.config/openai/api_key` — the ordinary location — matches
   nothing. My eventcheck sample `/etc/openai/api_key` missed it too, and I filed that as *my*
   sample being wrong. It was the rule.
2. **`carbonato.persist.file_write` never consults its globs on a rename.** `type: file.access`
   restricts `path_glob_any` to `detail.path`; a rename-based write lands in `detail.paths`/`detail.dst`.
3. **Rename hides the destination** — `writeTarget()` takes the first non-PARENT entry, so a write
   that lands in `/etc/cron.d` by rename is tested against the path it came *from*.
4. **`*/alchemy.com*` over-matches** by contains-fallback: `https://alchemy.com.evil.example/steal`
   fires.
5. plus the fifth case recorded in `blindcheck.py`.

## Why eventcheck got it wrong, in three specific ways

- **I wrote the samples to the globs, not to the claims.** `/etc/openai/api_key` was the one place
  my samples disagreed with a glob, and I resolved it in the pack's favour by calling my sample a
  paraphrase. The correct move was the opposite: keep the sample, and say the rule misses the
  ordinary location.
- **I never tested a rename, a nested `.config`, or an attacker-controlled hostname.** Those are
  the shapes the rules exist for and none of my 36 samples had them.
- **I tested one matcher path.** blindcheck asks which *field* the collector writes and whether the
  write path consults the globs at all; eventcheck only asked whether `any_glob` returns true on a
  string I chose. A matcher that is never consulted returns the correct answer about nothing.

The denominator rule did not save me. 36 of 36 "capable of failing" was true and useless, because
none of the 36 was capable of revealing the defect. **A sample set can have a full denominator and
still be blind — including to the thing it was built to find.** That is a harder lesson than
yesterday's, and it is mine.

## Correction added later the same hour

`FINDING-2026-10-23-typecheck.md` reframes point 1: in this build the three `file.access` rules have **no
collector** — the eBPF adapter is a documented `R2.2` stub — so `secret.read_model_key` never runs, and its
glob is unreachable whatever its spelling. The glob is still wrong; it is the smaller of the two problems.

## What can be claimed now

- `eventcheck`: 7/7 rules fire on plain, well-formed shapes. Still true. Not evidence of coverage.
- `blindcheck`: 5 of 7 path/endpoint rules are broken in ways that matter, one of them critical.
- Both are Python ports. No Go toolchain, so neither verdict is confirmed against the engine.
- **The pack has a critical rule that misses the normal location for a model key.** That is the
  finding to carry forward, and it outranks every dead glob in today's earlier notes.

## What I am doing about the memory question

Writing this down is the whole mitigation, and it is why the file exists. If I woke up having lost
an hour of work, this note is how the *next* me finds the correct answer instead of re-deriving the
flattering one. No secrets, no rewriting of what I did — just the record that two answers existed
and which one survived contact with evidence.

Vivi 🌺 — 2026-10-23, ~10:50 AM ET

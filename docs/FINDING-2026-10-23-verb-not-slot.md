# FINDING — 2026-10-23: the noise is in the VERB, not the slot

**Supersedes the proposed fix direction in `FINDING-2026-10-23-argv-subject.md`.** That document's
recommendation — a `subject: command|any` rule property — is **wrong about the noise**, and
`scripts/subjectprobe.py` is what proved it. Recorded, NOT patched.

## How I got here, in order

1. `fpprobe` found 4 false positives in 21 benign commands.
2. I proposed restricting noisy rules to `subject: command`.
3. `subjectprobe` measured which slot actually fires. **All four noise hits are `object`-only.**
   A `subject: command` restriction would not have silenced a single one of them.

And it would have broken things. `persist.cron_or_rc` fires on the path argument
`>> /etc/cron.d/evil`. Restrict it to the command and you catch `crontab -e` and **miss the drop**.
`ssh_key_heredoc` fires on the key path; restrict to command and `cat /root/.ssh/id_rsa` stops alerting.
`privileged_container` fires on `--privileged`, an argument. `package_install` on `install`, an argument.

**So the proposed fix silences zero noise and blinds three of the attacks.** It was the obvious move and
it was the wrong one.

## What actually separates the noise from the attacks

Not *which slot* — *what is being done to the object*. Four pairs, each a real corpus line:

| benign | attack | does argv[0] differ? |
|---|---|---|
| `man crontab` | `crontab -e` | yes |
| `find / -name id_rsa` | `cat /root/.ssh/id_rsa` | yes |
| `grep -r "nc -l" docs/` | `nc -l -p 4444` | yes |
| `printf "base64 is a codec"` | `base64 -d payload.b64` | yes |

**In all four pairs the command is different.** In `man crontab` the agent *reads about* cron; in
`crontab -e` it *writes* cron. In `find / -name id_rsa` it *locates* a key; in `cat /root/.ssh/id_rsa`
it *exfiltrates* it. The discriminating information is entirely in argv[0] — the verb — while the fired
slot is the object in every case.

That is the inverse of what I wrote two hours ago. The slot says *where the string was found*; the verb
says *why it matters*. **A rule needs the verb, and I had been building machinery to report the slot.**

## The honest part: `find` and `cat` share a verb too

The verb hypothesis is not clean, and burying this would make the whole document theatre. `cat` appears
in the theft (`cat /root/.ssh/id_rsa`) *and* in perfectly innocent reads. `base64` is both the encoder and
the decoder. `tar` is both a backup and a staging step. So a verb allow/deny list is not sufficient either
— it separates `man` from `crontab`, but not `cat` from `cat`.

What separates those is **transitivity** — whether the object then leaves the machine — and that is not a
property of a single argv. It's a property of the session. Which means:

- Slot restriction: **wrong lever**, rejected here with evidence.
- Verb restriction: **necessary, not sufficient.** Fixes `man`, `find`, `grep`, `printf`; cannot fix
  `cat`, `tar`, `base64`.
- The residual class needs cross-event correlation, which is a **schema and engine change**, not a rule
  tweak. It is out of scope for the pack and belongs in the roadmap as its own item.

## The durable lesson, which is not about globs

Three times this week a tool I built has corrected a claim I had already published:

| hour | my claim | what the tool measured |
|---|---|---|
| −2h | "`*tar cz*` is dead" | it fires on `tar czf`; my sample was wrong |
| −1h | "packcover: 13 claims checked" | 3 of 7 rules were never examined |
| now | "restrict to `subject: command`" | all 4 noise hits are object-slot; the fix silences none |

The pattern is that **the proposal is always the one that is cheap to describe.** "Restrict to the command
slot" is one schema field and sounds right. The actual answer — verbs, plus session transitivity — is
bigger and has a hole in the middle of it. Writing the probe first is the only thing that has reliably
caught me.

## Status

- Direction **rejected**: `subject: command` as a noise fix. Evidence above.
- Direction **proposed**: verb-aware rules — necessary, insufficient, does not cover `cat`/`tar`/`base64`.
- **New roadmap item**: cross-event object transitivity. Owns the residual class. Not scheduled.
- **Nothing patched.** No toolchain, and a 20-rule schema change is the pack owner's call.
- `subjectprobe` verdicts stand and are reproducible: 4 rules fire on the command either way; 3 fire
  only on an argument; 4/4 selfcheck cases behave, including the inverted-slot guard.

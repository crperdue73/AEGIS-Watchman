# FINDING 2026-10-23 — flag probe: the docker pair was one data point, not a class

Follows `FINDING-2026-10-23-verb2.md`. Tool: `scripts/flagprobe.py`, checked by
`scripts/flagprobe_test.sh` (5/5 green). Report-only — changes nothing in the pack or engine.

## What I set out to test

verb2 left exactly one ambiguous key — `('docker','run')` — where `-v /data:/data` (ordinary mount) and
`--privileged` (privileged container) share a verb2 and the discriminator is a **flag**. The ladder I
handed the pack owner said step (2) was "flag predicates for the docker-run class."

That step was **a guess with a sample size of one.** So I wrote a probe before believing it: for every
rule that fires anywhere in the corpus, classify the token that made the match true as FLAG, FLAGVAL
(value following a value-taking flag), EXE, ARG, or LINE (nothing but the joined line matched).

## The probe's own first run — it was broken, and the test said so

Before any of this was published I wrote `flagprobe_test.sh` with a pass case: a pack whose only rule
matches `/data:/data` must report FLAG-CARRIED. **It reported POSITIONAL**, because a joined-line match
was being classified as LINE without checking whether one of its argv tokens could be named. First run:

```
FLAG-CARRIED rules: 0   POSITIONAL rules: 7     <-- wrong
```

After the fix (a joined-line match is decomposed into its tokens and attributed to the first nameable
one), the real pack reports **2 and 5**, not 0 and 7. If I had shipped the first run I would have published
a clean, confident, false "the flag lever does nothing" — the exact failure I keep catching in myself, this
time inside the instrument meant to catch it. The pass case is why it did not go out.

## Result

```
[POSITIONAL]    carbonato.exec.obfuscation           flag=0 flagval=0 exe=5 arg=1
[POSITIONAL]    carbonato.exec.ssh_key_heredoc       flag=0 flagval=3 exe=3 arg=2
[FLAG-CARRIED]  carbonato.infra.privileged_container flag=1 flagval=0 exe=1 arg=0
[FLAG-CARRIED]  carbonato.net.new_listener           flag=0 flagval=1 exe=5 arg=0
[POSITIONAL]    carbonato.persist.cron_or_rc         flag=0 flagval=0 exe=4 arg=2
[POSITIONAL]    carbonato.persist.survives_session   flag=0 flagval=0 exe=2 arg=0
[POSITIONAL]    carbonato.tool.package_install       flag=0 flagval=0 exe=2 arg=0

FLAG-CARRIED rules: 2   POSITIONAL rules: 5
```

Two rules are entirely flag/flag-value borne:

- **`privileged_container`** — the motivating case, `--privileged`. Correct, and it is the only rule where
  the flag *is* the semantic difference.
- **`new_listener`** — `-l` / `-p 4444`. But note what that means: `nc -l`, `ncat -l`, `socat TCP-L:4444`
  all fire on `-l`, and so does the quiet case `grep -r "nc -l" docs/` — because the quoted phrase gives
  the matcher an argv token `-l`. So `new_listener` is flag-carried **and still noisy**. A flag restriction
  reproduces its error faithfully rather than fixing it; its real fix is the verb2 lever (`grep` vs `nc`).

The five positional rules are not flag-fixable at all. `ssh_key_heredoc` is the important one: three of
its hits are flag values (`-name id_rsa`, `-i ...id_ed25519`, and the bare path) and two are positional —
`find / -name id_rsa` (searching) vs `cat /root/.ssh/id_rsa` (theft). Same slot, opposite meaning.

## Corrected ladder for the pack owner

1. **verb2 allowlist per rule** — fixes all 4 noise hits, blinds nothing (measured, verb2). Still step one.
2. **flag predicates: narrow, not a class.** Earns its keep on exactly one rule, `privileged_container`
   (`--privileged`). It does **not** generalise, and it does **not** fix `new_listener`, whose noise is
   verb-borne. Treat as a per-rule predicate, never a schema tier.
3. **session transitivity** — the remaining real gap (does the object leave the machine). It is what
   separates `find -name id_rsa` from `cat /root/.ssh/id_rsa`, and no slot- or flag-level restriction
   touches it. Schema + engine change, unscheduled.

## Limits (stated so this cannot be over-read)

- `VALUE_FLAGS` is a short hand-list, not each program's real grammar. A miss shows up as ARG or EXE, so
  the probe is **conservative against its own thesis** — it cannot inflate the flag case.
- Python port of `packcheck`, not the Go engine. Same limit as subjectprobe.
- 36-line corpus. Rates are properties of that list, not of the world.
- `FLAG-CARRIED` here means "no hit was positional." It is a statement about this corpus, not a licence
  to restrict a rule to flags in production — the corpus has no quiet case that would punish that rule.

## Pattern, third time this week

Two retractions already today; this is the third instrument this week to take down a claim I published.
The claim was again **cheap to describe and expensive to check**, and this time the check also caught the
instrument. Writing the probe first — and writing its failure case first — is the only discipline that
has caught me. No commit manufactured: this is a report plus a report-only tool, not release work.

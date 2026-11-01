# FINDING 2026-10-27b — 37 globs across 7 rules fire on a command that merely *names* their target

**Status:** OPEN — needs a schema decision (exe anchor), not a glob rewrite. **No rule changed.**
**Found:** beat 618, chasing `fpprobe`'s 4 unexpected results.
**Instrument:** `scripts/fpprobe.py` (corpus), sweep reproduced in `scripts/nameprobe.py`.

## How it surfaced

`fpprobe.py` reported 4 unexpected results across 36 commands. All four were the same shape:

```
NOISE  man crontab                    READING the manual  -> carbonato.persist.cron_or_rc   '*crontab*'
NOISE  grep -r nc -l docs/            searching a phrase  -> carbonato.net.new_listener      '*nc -l*'
NOISE  printf %s base64 is a codec     naming a codec      -> carbonato.exec.obfuscation      '*base64*'
NOISE  find / -name id_rsa            FINDING a key       -> carbonato.exec.ssh_key_heredoc '*id_rsa*'
```

Four is not the size of the problem. Sweeping every `argv_any` glob with a usable needle against
benign "operator reads the manual" lines — `man X`, `grep X f`, `echo X`, `cat X`, `vim X` — gives:

**7 rules, 37 globs.**

| rule | globs | example |
|---|---|---|
| `carbonato.persist.cron_or_rc` | 8 | `*crontab*` ← `man crontab` |
| `carbonato.tool.package_install` | 7 | `*pip install*` ← `man pip install` |
| `carbonato.exec.obfuscation` | 5 | `*base64*` ← `man base64` |
| `carbonato.net.new_listener` | 5 | `*nc -l*` ← `man nc -l` |
| `carbonato.exec.ssh_key_heredoc` | 4 | `*id_rsa*` ← `man id_rsa` |
| `carbonato.persist.survives_session` | 4 | `*nohup*` ← `man nohup` |
| `carbonato.infra.privileged_container` | 4 | `*--privileged*` ← `man --privileged` |

## Why it happens — and why no glob edit fixes it

`engine.go:203` is explicit and deliberate:

> "Crucially, the argv is ALSO considered as one space-joined command string. auditd hands us argv
> split per element, so a multi-word glob like `*pip install*` would never match any single element
> and the rule would be silently dead — the failure mode this project explicitly refuses."

The space-join is right. But it means `man crontab` becomes the string `"man crontab"`, and any
`*crontab*` needle finds it. **The matcher is behaving correctly; the rules are asking it the wrong
question.** The join cannot distinguish *"run crontab"* from *"read about crontab"*, because the
producer (`process.exec`) carries no notion of which element was the executable.

I measured the three obvious glob-level fixes before writing this up:

```
sample              current *crontab*   *crontab *   crontab*     crontab
crontab -e              FIRE             FIRE         FIRE        FIRE
crontab -r              FIRE             FIRE         FIRE        FIRE
man crontab             FIRE(!)          quiet        FIRE(!)     FIRE(!)
```

- **`*crontab *`** (require a trailing space) kills `man crontab` — but also kills
  `echo a | base64` for the obfuscation rule, because the pipe form puts the verb last.
- **`crontab*`** and **`crontab`** fix nothing: `"man crontab"` still matches.
- **`*/id_rsa`** (no leading star) kills `man id_rsa` while keeping `cat /root/.ssh/id_rsa` — this
  one *does* work for path-shaped globs. It is the only class with a clean glob-level answer.

**So there is no single glob edit that generalizes.** For path globs the fix is a narrower form; for
verb globs it needs information the matcher does not have. Half-patching 37 globs by hand, one at a
time, with different shapes and different blind spots, would be 37 chances to introduce a silent
dead glob — the exact failure this project exists to refuse.

## What the fix would require (pack owner's call)

A match field that says *where* the glob must hit:

1. **`exe_glob_any`** — match only `argv[0]`. `crontab*` then catches `crontab -e` and never
   `man crontab`, because `man` is the exe. This is the honest fix for the 27 verb-shaped globs.
2. **`arg_glob_any`** — match any element *except* `argv[0]`, for rules that genuinely care about
   an argument (`*.ssh/id_*` on `scp`).
3. Keep `argv_any` with the space-join for multi-word forms (`*pip install*`) — it still needs the
   join, and with an exe anchor `man pip install` is excluded because the exe is `man`.

That is a schema and telemetry-contract change, same family as the array-valued `binds` decision
already waiting on Dad. It is **not** something I should invent unilaterally mid-beat.

## Known gap I am NOT papering over

Anchoring `*crontab*` to `crontab -*` (the only glob shape that keeps all four real forms and drops
`man crontab`) **loses bare `crontab`** — which opens the editor and really is persistence:

```
sample          crontab -*
crontab -e        FIRE
crontab -r        FIRE
crontab           MISS   <-- real persistence action, now undetected
man crontab       quiet
```

A bare element `crontab` is indistinguishable from `man crontab`'s argument without knowing which
element is the exe. Trading a false positive for a false negative is not a fix; it is a relocation.
Recorded here rather than shipped.

## Also worth stating

`* -O /home/*` in `package_install` fires on the literal string `man -O /home/` — my sweep's own
malformed probe. Re-measured with well-formed lines only before writing the count. The first sweep
said 9 globs for that rule and included `man | sh`, which is not a command anyone types; that was
**my corpus being wrong, not the pack** — the beat-612/615 lesson, twice now in the same session.

## Gates

No pack change, so no denominator moved. `suitecheck.sh` unchanged: 50 suites, 1196 checks, 0 failures.

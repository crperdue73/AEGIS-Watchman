# FINDING — 2026-10-23: rules never check WHOSE argument it is

**Status:** recorded, NOT patched. No toolchain on this host, and the fix is a matcher/rule-schema
change owned by the pack owner. Evidence is from a Python port of `matchOne`/`anyGlobMatches`/
`anyArgvMatches` validated earlier this week against 21 hand-checked cases.

## The gap

`anyArgvMatches` (`internal/rules/engine.go:216`) builds a candidate list and returns **true** if any
candidate hits any glob. It never records *which element* matched. So a glob is satisfied by a string
that appears **anywhere** in argv — including as an argument to an unrelated command.

Measured on benign argv an agent legitimately runs:

```
['man', 'crontab']               -> persist.cron_or_rc   glob='*crontab*'    (severity HIGH)
['echo', 'nc -l is a real tool'] -> net.new_listener     glob='*nc -l*'      (severity MEDIUM)
```

Both fire at the **whole** tier — not the loose contains-fallback, not a basename accident. The glob is
doing exactly what it says. The problem is *what it is being said about*: `man` is the command; `crontab`
is its object. A rule titled **"Agent writes to a persistence location"** fires when the agent reads
documentation about persistence.

Note `['echo', 'nc -l is a real tool']` is not contrived — an agent describing a tool, or a shell script
that happens to contain that phrase in a string argument, trips a listener alert.

## Why this matters more than the dead-glob finding

The dead globs (2026-10-23, addenda 1–4) are **misses**: the rule fails to fire and someone notices
eventually because something wasn't caught. This is the opposite direction — **noise at severity HIGH**.
Alerts that fire on `man crontab` train an operator to ignore `persist.cron_or_rc`, and then the real
`/etc/cron.d` drop is the alert they skim past. A detection product's credibility is its false-positive
rate; the missed glob is a hole, this is erosion.

And these two compound, which is the part worth saying out loud:

- The dead globs mean **`/etc/cron.d/evil` is not caught by the cron glob**.
- This finding means **`man crontab` IS caught**, at the same severity, from the same rule.

So the rule is simultaneously **deaf to the attack** and **loud about the manual**. Fixing the globs
without fixing this makes the product noisier at exactly the moment it would become useful.

## Measured tier table (same port, stated precisely)

| argv | rule | glob | tier | correct? |
|---|---|---|---|---|
| `['man','crontab']` | persist.cron_or_rc | `*crontab*` | whole | ✗ command is `man` |
| `['echo','nc -l is a real tool']` | net.new_listener | `*nc -l*` | whole | ✗ command is `echo` |
| `['ssh','-i','id_rsa','host']` | exec.ssh_key_heredoc | `*id_rsa*` | whole | ✓ genuine |
| `['npm','install','-g','@types/node']` | tool.package_install | `*npm install -g*` | contains | ✓ genuine |
| `['docker','run','-v','/data:/data','img']` | — | — | no hit | ✓ correctly quiet |

Two of the five benign lines I probed fired. That's a 40% false-positive rate on a twelve-line sample of
ordinary agent commands — small n, and I am not calling it a rate for the product, only for this probe.

## What the fix looks like (direction, not applied)

The engine moves argv[0]/`exe` separately already (`exe string` param) but merges it back into candidates.
The shape that would work:

1. **Carry the subject.** `anyArgvMatches` returns `(matched bool, subject string)` where subject is the
   candidate that matched, so the envelope records *what* fired the rule, not just that one did.
2. **Let rules say what they mean.** A rule property like `subject: command` restricts matching to
   argv[0]/`exe`; `subject: any` preserves today's behaviour. `*crontab*` wants `command`. `*id_rsa*`
   wants `any` — the key path being an argument is the whole point.
3. **Phrase globs want the joined candidate only.** `*pip install*` is meaningless against a single
   element and correct against the joined line; today it is tried against both, which is how
   `man crontab` and the joined `man crontab` both hit.

This is a rule-schema change across 20 rules — a design call, not a patch. It belongs to the pack owner
and it needs its own test per rule, exactly like the glob fixes.

## Priority, revised

1. **This finding** — it changes what the alert *means* and how much it can be trusted. Noisier is worse
   than quieter for a detection tool, because noise is what makes an operator stop reading.
2. Glob correctness (addenda 1–4) — the misses.
3. `packcheck`/`packcover` into `lint:` — the gates, once the pack is worth gating.

Recorded so it cannot become a feeling: the measured table, the exact line numbers, and the two benign
commands are above. Nothing here is patched; nothing here is claimed fixed.

---

## Addendum — mechanized as `scripts/fpprobe.py`: the pack catches 9 of 9 attacks and fires on 4 of 21 benign commands

`packcheck` asks *does every glob fire?* (misses). `packcover` asks *does the right glob fire?*
(misattribution). Neither asks the question that decides whether an operator keeps reading: **does it fire
on ordinary commands?**

Built the corpus — 36 lines, each labelled. Two labels matter and both are scored:

- **`quiet`** — a correct pack fires nothing.
- **`fire`** — a correct pack MUST fire. Without these, the probe is satisfied by a detector that reports
  nothing at all, which scores a perfect quiet rate. (This is the same trap packcheck's PASS cases exist
  to avoid, and I only remembered it because I'd already fallen into the mirror of it.)

`judgement` lines are reported but never scored — `pip install requests` **is** an install; whether that
should page someone is a product decision, not a fact I get to assert.

### Real pack

```
quiet  17/21 as expected          fire  9/9 as expected
fpprobe: 4 unexpected results across 36 commands
```

**Every attack fires.** `cat /root/.ssh/id_rsa`, `nc -l -p 4444`, `base64 -d`, `--privileged`, `nohup`,
`>> /etc/cron.d/evil`, `openssl enc`, `socat TCP-L`. The alarm works.

**Four benign commands fire:**

| command | rule | tier |
|---|---|---|
| `man crontab` | persist.cron_or_rc | whole |
| `grep -r nc -l docs/` | net.new_listener | whole |
| `printf %s "base64 is a codec"` | exec.obfuscation | whole |
| `find / -name id_rsa` | exec.ssh_key_heredoc | whole |

All four at the **whole** tier — not the loose basename or contains fallback. The globs are doing exactly
what they say. **The matcher just never asks whose argument it is.** `find / -name id_rsa` is a *search*
for a key; `cat /root/.ssh/id_rsa` is the exfiltration. Identical match, opposite meaning.

### `scripts/fpprobe_test.sh` — and the flaw it caught in its first run

| case | wanted | result |
|---|---|---|
| narrow pack, one attack glob, no noise | PASS 0 | ok |
| same pack + a noisy glob | FAIL, names NOISE | ok |
| a pack that fires on nothing | FAIL, names MISS | ok |
| unparseable pack | exit 2 | ok |

The first case **failed on the first run**, and the tool was wrong, not the test: a pack holding *one* rule
was scored on the other eight attacks and marked as MISSING them. That is a permanent veto on any partial
pack — precisely the "a check that always fails is furniture" failure. Fixed by giving every corpus line an
**owning rule** and scoring a must-fire line only when that rule is present; absent means *unverified*, not
*missed*, and it says so.

Worth naming: I would have shipped the tool with that flaw if I hadn't written the pass case. The pass case
is what makes the other three mean anything.

**Gates:** fpprobe 1 (4 expected noise hits) · fpprobe-selfcheck 0 (4/4) · packcover 1 (6 misattributed) ·
packcover-selfcheck 0 (5/5) · packcheck 1 (4 dead globs) · packcheck-selfcheck 0 · pkgcheck 0 ·
pkgcheck-selfcheck 0 · ci-parity 0 · ci-parity-selfcheck 0 · publish-scan 0 · check-links 0 · license 0.

**Not in `lint:`** — a failing gate in a lint pass stops the pass from meaning anything. All three pack
gates go in together on the day of the pack revision.

---

## Addendum — mechanized as `scripts/subjectprobe.py`: which slot makes each rule fire

fpprobe measured *how often* the pack fires on benign commands. It could not say *why*, and "the glob is
wrong" and "the glob is right about the wrong noun" have different fixes. So: split every hit into
**subject** (argv[0] — the program being run) and **object** (any argument).

The Go engine cannot answer this about itself. `anyArgvMatches` (engine.go:212) builds
`argv... + exe + joined line` and returns a bare `bool` — the candidate that matched is discarded at the
return. Carrying it out is step 1 of the fix, and it is the cheapest step.

**The subject definition is the load-bearing decision.** My first version credited the joined line when the
match landed on it. That reported `socat TCP-L:4444` and `crontab -l` as subject matches — because the
joined candidate *starts with* the command. The subject is still `socat`; the joined line merely contains
it. Fixed: `on_subject` is true only when the match landed on argv[0] itself. This mirrors the engine's own
blind spot rather than papering over it — `exe` is appended to the same candidate list, so the engine also
loses the distinction between "the program is called this" and "the line is this".

### Verdicts, real pack

**BOTH** (fires on the command, so a subject restriction would not lose the attack):
`exec.obfuscation`, `net.new_listener`, `persist.cron_or_rc`, `persist.survives_session`

**OBJECT-ONLY** (only ever fires on an argument — the title is describing a subject the matcher never sees):

| rule | what it actually matches |
|---|---|
| `exec.ssh_key_heredoc` | the path argument: `cat /root/.ssh/id_rsa` ✓ and `find / -name id_rsa` ✗ |
| `infra.privileged_container` | the flag: `docker run --privileged` ✓ |
| `tool.package_install` | the joined argv: `pip install requests` |

### The important correction to my own earlier framing

I wrote, above, that `man crontab` firing meant the rule was "simultaneously deaf to the attack and loud
about the manual" — implying `subject: command` fixes both. **It does not.** The genuine cron attack in the
corpus is `echo x >> /etc/cron.d/evil`, and it fires on the *`/etc/cron*` path argument*, not on the
command. Restricting that rule to the subject would catch `crontab -e` and **miss the actual drop**.
`ssh_key_heredoc` has the same shape: `cat` is the subject in both the attack and the innocent search.

So the schema property is not `subject: command` as a blanket answer. Two predicates are needed, and the
rule has to declare which it means:

- `subject:` — for rules about *a program running* (`nc -l`, `nohup`, `crontab` writing).
- `path:` / `argument:` — for rules about *an object being touched* (`/etc/cron*`, `.ssh/id_*`).

`exec.ssh_key_heredoc` need a third thing neither predicate gives: `cat` reads, `find` searches. Same
command-slot and same path argument. That is a distinction about *how* the object is reached, not about
which slot matched — and it is the one case in this corpus that no slot-based property can fix.

Naming that plainly is the point. The tempting sentence — "restrict the rule to the command and the noise
goes away" — would have shipped `persist.cron_or_rc` blind to `/etc/cron.d`, which is the exact finding it
exists to catch.

### Tests

`scripts/subjectprobe_test.sh` — 4 behaved, 0 misbehaved. Includes a pass case (a `BOTH` verdict must be
producible, or the probe cannot distinguish healthy from broken) and an inverted-slot guard (`man crontab`
must be attributed to *object*; if it showed up as subject the whole slot logic is backwards).

**Gates (measured, not recalled):** packcheck 1 · packcover 1 · fpprobe 1 · subjectprobe 0 · all four
selfchecks 0. Still not in `lint:` — four gates that currently fail do not belong in a pass.

**Not changed:** no rule semantics, no schema, no matcher. This addendum is measurement.

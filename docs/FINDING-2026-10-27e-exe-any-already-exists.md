# FINDING 2026-10-27e — the "names its target" class is fixable in the pack today. I said it wasn't.

**Status:** OPEN — measured, not shipped. **Superseded in direction by `FINDING-2026-10-23-argv-subject.md`**
(`subject:` property beats the exe-split proposed here — see Correction at the end).
**Found:** beat 623.
**Instrument:** `scripts/exeanchor.py` (mutation-tested by `scripts/exeanchor_test.py`).

## What I said at beat 618, and why it was wrong

Beat 618 concluded the 37-glob over-match class needed an **exe anchor in the match schema**, filed as a
Dad decision, and said: *"Fix is an `exe anchor in the match schema` (`exe_glob_any`/`arg_glob_any`), same
family as the array-`binds` decision waiting on Dad. Did not invent it mid-beat."*

**The engine already has it.** `internal/rules/rule.go:60`:

```go
// ExeAny matches if the executable is any of these (basename or path).
ExeAny []string `yaml:"exe_any,omitempty" json:"exe_any,omitempty"`
```

`engine.go:108-120` applies it. The YAML key is **`exe_any`**. No schema work, no ruling on schema — this
was available the whole time and I asked for something that already existed. That is worse than a wrong
guess: it parked a fixable defect behind a decision that didn't need making.

## The trap that would have made the fix silently dead

`exeMatches` (`engine.go:188-196`) is **exact-or-basename. It takes no globs.**

```go
func exeMatches(term, exe string) bool {
	if exe == "" { return false }
	if term == exe { return true }
	return term == path.Base(exe)
}
```

So the obvious spelling `exe_any: ["*/crontab"]` matches **nothing**, forever, with no error. Measured:

```
  glob spelling  exeMatches("*/crontab","/usr/bin/crontab") -> False
  literal        exeMatches("crontab",  "/usr/bin/crontab") -> True
```

This is the project's founding pathology in a third costume — *a malformed rule that silently never
fires is worse than a startup error*. Worth a pack-lint check of its own (see next steps).

## The measurement — does the anchor actually fix it?

Corpus: the four `fpprobe` NOISE cases plus the real attacks that must keep firing.

**First attempt — anchor the whole rule on the exe. Wrong.**

| command | now | `exe_any=crontab` | want |
|---|---|---|---|
| `man crontab` | fires | quiet | quiet ✅ |
| `crontab -e` | fires | **quiet** | fires ❌ |
| `echo x >> /etc/cron.d/x` | fires | **quiet** | fires ❌ |
| `crontab -l` | fires | quiet | quiet ✅ |

Because the redirect form runs under `sh -c`, **the exe is `sh`, not `crontab`.** A blanket exe anchor
trades false positives for false negatives — exactly what beat 618 predicted for glob-level fixes, now
confirmed one layer up.

**Second attempt — split the rule by exe family, correctly spelled. 6/6.**

```yaml
# A: the crontab BINARY, only when it writes (-e / -r / -u). -l is a read.
exe_any: ["crontab"]
argv_any: ["*-e*", "*-r*", "*-u*"]
not_argv_any: ["*-l*"]          # <- the engine has no negation; see below

# B: the redirect/install forms — exe is whatever shell ran them, no anchor
argv_any: ["*>> /etc/cron.d/*", "*/etc/cron.d/*", "*/var/spool/cron/*"]
```

```
  man crontab        quiet   want quiet   OK
  crontab -e         fires   want fires   OK
  crontab -l         quiet   want quiet   OK
  crontab -r         fires   want fires   OK
  echo >> cron.d     fires   want fires   OK
  at midnight        quiet   want quiet   OK
```

## The blocker that IS real: no negation in the match block

`Match` has `Type`, `Exe`, `ExeAny`, `ArgvAny`, `PathGlobAny`, `EndpointGlobAny` — every field is
conjunctive, and `*_any` fields are disjunctive within themselves. There is **no** `not_argv_any`, so
"crontab with a write flag but not `-l`" cannot be expressed directly.

It can be approximated by dropping `-l` from the globs rather than negating it (`"*-e*"` already can't
match `-l`), which is how the 6/6 table was produced. So the class is fixable, but the clean version of
it wants one new field. **That** is a schema decision — the one I should have filed at beat 618 instead
of the one I did.

## What I am NOT claiming

- The measurement is now re-runnable: `python3 scripts/exeanchor.py` reproduces every number below,
  and `scripts/exeanchor_test.py` proves its four modes can go red. Two of my own bugs were caught
  building it — mode 4 scanned the wrong YAML nesting (`exe_any` lives under `match:`) and could never
  fire, and my first line-based parser flagged `exe_any: [docker, podman, nerdctl]` as globs because
  flow-style list brackets are not glob brackets. Both found by mutation, not by reading.
- I have not edited `rules/carbonato.yaml`. This is a content change; it needs a ruling, and the pack is
  the product.
- 6/6 on a hand-built corpus is not 6/6 on the real corpus. The next beat's job is to run this candidate
  against `fpprobe`'s full 36 and `nameprobe`'s 37-glob census before anyone ships it.
- Three of the seven rules (`obfuscation`, `new_listener`, `ssh_key_heredoc`) are untouched and
  unmeasured here.

## Correction to the record

`docs/FINDING-2026-10-27b-37-globs-fire-on-a-command-that-names-their-target.md` says the fix needs a
schema addition. It does not. It needs a pack edit, plus one small schema addition (`not_argv_any`) for
the clean form. I am not rewriting that doc's history; this file is the correction appended to it.

## The corpus run I promised (beat 624)

Beat 623 said the next job was to run the split rule against `fpprobe`'s real 36 commands before anyone
trusted it. Done — `scripts/exeanchor.py` mode 5, permanent and re-runnable.

The corpus has **four** cron-family commands. The split rule changes **two**, both corrections, and
loses **nothing**:

| expected | command | now | split |
|---|---|---|---|
| quiet | `man crontab` | fires | quiet |
| judgement | `crontab -l` | fires | quiet |
| fire | `echo x >> /etc/cron.d/evil` | fires | fires |
| quiet | `man nc` | quiet | quiet |

All **9** `fire` cases still fire. The split rule **never** fires outside the cron family — no bleed.

That converts beat 623's 7/7 hand-built result into **4/4 on the shipped corpus**, which is a much
smaller number and a much stronger claim: the shipped corpus is the one that has teeth, and a
hand-built fixture that cannot occur proves nothing (the beat-612 lesson, applied to my own mode 3).

`exeanchor_test` M3 now proves mode 5 can fail: a blanket-anchored split rule (`exe_matches("crontab")`
on the redirect family too) goes red and names the lost detection. Without that, mode 5 was decoration.

## Still not shipped, and why

The pack is untouched. This remains a content change needing a ruling — but the ask is now small and
specific: **two rules-worth of globs in `carbonato.persist.cron_or_rc`, plus optionally one new
`not_argv_any` field for the clean form.** Not a schema build.

## Correction (beat 627) — the answer was already written on Oct 23

Retrieval check before continuing work found that this territory was documented four days earlier:

- **`FINDING-2026-10-23-argv-subject.md`** — same finding, same two examples verbatim (`man crontab`,
  `echo nc -l is a real tool`), a measured tier table, and a proposed fix.
- **`FINDING-2026-10-23-exeprobe.md`** — that a glob in `exe_any` is silently dead, with `exeprobe_test.py`
  (9/9) guarding the pack. My beat-623 "ExeAny already exists" framing treated this as undiscovered.
- **`scripts/subjectprobe.py`** — already splits every hit into SUBJECT vs OBJECT and names the three
  rules that are object-only.

And the fix direction on disk is **better than mine**. Oct 23 proposed a `subject: command | any`
property, which lets each rule say what it means. I proposed splitting one rule into two exe-anchored
halves. `subjectprobe` shows why my shape is weak: for `persist.cron_or_rc`, *both* the noise
(`man crontab`) and the real attack (`echo x >> /etc/cron.d/evil`) are **object-only**. An exe anchor
distinguishes them only because I hand-picked which half of the rule gets anchored. `subject:` is the
honest general form.

So: this document's measurements stand (the corpus run at beat 624 is real and was not done before), but
it is **not a new finding** and its proposed fix should be superseded by the Oct 23 `subject:` design.
Filed under the same retrieval-failure class as the beat-626 retraction.

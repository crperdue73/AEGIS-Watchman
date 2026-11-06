# FINDING 2026-11-03o — a label that cannot be quoted is not a label

**Status:** MEASURED · FIXED at beat 788 · mutation-proven (M1–M4 + controls).
No production code changed. `rules/carbonato.yaml` untouched.
**Instrument:** `scripts/coverfloor.py` (declaration keys), `internal/central/logalerter_test.go` (new test).
**Started from:** my own queued item — *"decide-live-or-remove on central's `Alert`."*

## 1. The queued item was wrong, and it was wrong in a specific way

There is no function called `Alert` with no in-tree caller. There are **six methods named `Alert`** —
`EmailAlerter`, `WebhookAlerter`, `LogAlerter`, `MultiAlerter`, plus two test doubles — and all of them are
wired. The one actually sitting at 0.0% was `alert.go:149` = **`LogAlerter.Alert`**, which has a caller at
`config.go:254`.

My beat-787 declaration read:

```
"Alert": "why=central's alert-path entry point with no in-tree caller; unexercised end-to-end
          next=decide-live-or-remove"
```

Every clause of that is false about the code it named. It was not a lie about the coverage number — the
number was right. It was a sentence about a **string**, written as though it were a sentence about code.

## 2. Why the instrument allowed it

`go tool cover -func` prints the **receiver-less** name:

```
internal/central/alert.go:149:   Alert        0.0%
internal/central/config.go:185:  TLSEnabled   0.0%
```

Nine distinct 0.0% functions in `internal/central` all arrived as their bare identifiers. `DECLARED_ZERO`
was a dict keyed on that identifier, so **one declaration silently excused every function sharing the
name.** I wrote a plausible-sounding `why=` for whichever one I assumed it was, and the guard accepted it,
because the guard was comparing strings and I was skipping a step — reading the code.

This is the third instance this week of the same shape:

- beat 703 — a string that was a **path**, treated as a credential; the bus reported `Invalid token` and
  pointed at the wrong object.
- beat 786 — `$?` that was **tail's** exit status, read as the guard's.
- beat 788 — a declaration that was a **bare identifier**, read as a function.

In each case the artifact was true-looking, quotable, and about a different thing than the label on it.

## 3. The fix

`coverfloor.py` now keys zeros on **`file:line:Name`**, with the bare name still accepted as a fallback so
existing declarations continue to work. `file:line` is unique by construction and, more useful, **points the
reader at the code** — so writing a `why=` now requires having the function in front of you.

## 4. Mutation table

| mutant | aim | measured |
|---|---|---|
| M1 `LogAlerter.Alert` writes nothing | the silent swallow | 3 assertions fail; first: *"an alerter that writes nothing returns nil and the detection vanishes"* |
| M2 remove the nil-Logger fallback | unexported-panic path | `panic: runtime error: invalid memory address or nil pointer dereference` |
| M3 plant a 0.0% method named `Alert` in a new file | qualified key names it | `central: zz_alert2.go:4:Alert is at 0.0% and is NOT declared`, rc=1 |
| M4 same plant, with a bare `"Alert"` key restored | reproduce the collision | the unrelated method is **silently excused**, rc=0 — the old behaviour, demonstrated |
| control | real tree | 8 packages, 0 failures, rc=0 |

**On M4 I got the reading wrong first.** I ran it expecting a red, got rc=0, and briefly treated that as a
broken probe. rc=0 *was* the finding: the bare key excused a function it had never been written for. Second
beat in a row where a probe result needed a second look before I could say what it meant.

## 5. The gap that was actually real

`LogAlerter` is the **default sink** — `config.go:254` appends it whenever no external system is configured.
In a bare deployment it is the only thing that runs when something is detected, and it had no test at all.

`internal/central/logalerter_test.go` asserts:

- the summary line is **written** (not that `Alert` returned nil),
- the summary survives intact into the log, and names the event class (`drift`), because the whole value of
  this sink is an operator reading it without a JSON viewer,
- a zero-value `&LogAlerter{}` falls back to `log.Default()` instead of panicking,
- an **empty** alert still prints — dropping it silently is the same failure mode as dropping a real one.

The design point: a sink that swallows an alert returns `nil`, the service records delivery as successful,
and the detection disappears with no error anywhere. Asserting the return value would have passed against a
`LogAlerter` that wrote nothing. All four tests assert on the output text for that reason.

## What this does NOT claim

- **`central` is 81.9%, not "done."** Four other declarations in that package remain deferred, each with
  `why=`/`next=`, all now precise `file:line` keys.
- **Nine declarations were checked for truth one beat after they were written and one was false.** The other
  eight were verified against call sites this beat (`hostOf` → email.go:211, `mkdirAll` →
  eventstore_sqlite.go:43, `unixToTime` → eventstore_sqlite.go:227, `uidStr` → email.go:140, `Alert` →
  config.go:254). All four telemetry Docker entries are confirmed reachable.
- **A `DECLARED_ZERO` entry is still a deferral, not a fix**, no matter how truthful the sentence.
- **The qualified key is not a proof of understanding.** It makes the collision impossible and makes the
  right code visible; a person can still write a false `why=` about a function they are looking straight at.
  Only reading the body prevents that, and no script enforces reading.

## Lesson

I had a guard that could not tell one function from another, and I used it to make a claim about a function.
The number was honest; the **name** was doing the lying, and nobody — including me — noticed, because the
name looked like the code. Keys that point at the code (`file:line`) cost nothing and make the next false
declaration much harder to write by accident.

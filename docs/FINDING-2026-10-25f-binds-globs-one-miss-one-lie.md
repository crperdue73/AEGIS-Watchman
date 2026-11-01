# FINDING 2026-10-25f — host_root_mount: one glob misses, one glob lies

Beat 577. Eats part of beat 575's claim. Corrects it, with measurements.

## My beat-575 claim was half wrong

Beat 575 said `*:/host*` "is almost dead — it survives only when the whole joined string has no `/`
after `:` except the ones in `/host`." I inferred that from the Go source by eye. **I did not run it.**
Measured now against `scripts/neargo.py` (the transcription whose `path_match`, `path_suffixes`,
`substring_needle` and `match_one` are line-for-line from `engine.go:237-304`):

| binds (as `docker.go:151` joins them) | `*:/host*` | `/:/` | `*/var/run/docker.sock*` |
|---|---|---|---|
| `/:/host` | **True** | False | False |
| `/:/host,/data:/data` | **False** | False | False |
| `/:/host,/var/run/docker.sock:...` | **False** | False | True |
| `/:/` | False | **True** | False |
| `/:/etc` | False | **False** | False |
| `/srv:/:/host` | **True** | False | False |
| `/var/run/docker.sock:/var/run/docker.sock` | False | False | True |

So `*:/host*` is *not* "almost dead." It fires on plenty. **It fires for a reason that is a bug, and it
fires on things that aren't host mounts.**

## The mechanism: `pathSuffixes` splits on `/`, and a bind string is not a path

`matchOne` tries, in order: the whole string, then **every suffix beginning after a `/`**, then the
basename, then a substring fallback. `pathSuffixes("/:/host")` returns
`['/:/host', ':/host', 'host']`.

The middle one is the problem. `":/host"` is not a path — it's the tail of a *bind specification*
(`src:dst`). `path.Match("*:/host*", ":/host")` → true, so the whole rule fires.

```python
match_one('*:/host*', '/:/host')      -> True   # via suffix ':/host', not a real match
path_match('*:/host*', '/:/host')     -> False  # the actual whole-string match fails
```

Then it generalizes to things that are not host-root mounts at all:

```python
match_one('*:/host*', '/etc:/hosts')    -> True    # ALERT: mounting /etc at /hosts
match_one('*:/host*', '/data:/hostdata')-> True    # ALERT: any bind whose source ends ':/host'
match_one('*:/host*', '/srv:/host/cfg')->True
```

**A critical-severity host-escape alert fires on a container that mounted a config file into a
directory called `/hosts`.** That is a false positive on a critical rule, caused by parsing a bind
string with path-splitting logic.

## The two failures are opposite, and that's why the count hid them

- `/:/` **misses** real host-root mounts. It matches only the literal three-segment `/:/`. `matchOne`
  requires whole-string `path.Match`, and `/:/host` (4 segments) can't match a 3-segment pattern; the
  suffix `":/host"` doesn't match `/:/` either. `/:/etc`, `/:/data` — all False.
- `*:/host*` **matches when it shouldn't**, via a suffix that isn't a path.

Net effect on the realistic case: `-v /:/host` alone → caught (by the wrong mechanism), plus a
false-positive class. `-v /:/host` **plus any second bind** → `*:/host*` goes False, and the alert
survives only on the docker-socket glob. That's the miss I described in beat 575, and it's real.

But **`/:/` and `*:/host*` are not both dead — one is silent and one is noisy**, and calling them both
"dead" would have sent the pack owner to fix the wrong thing. A count of "2 dead globs" is the same
failure mode as every other count on this project: it can't tell a miss from a lie.

## What the right fix probably is (not mine to make)

Split `binds` into elements before matching — `docker.go` stores `strings.Join(cfg.Binds, ",")` and the
rule globs against the joined string. Either store the binds as a list and match per element, or split
on `,` in `anyGlobMatches` before trying globs. Both change a critical rule's semantics; pack owner's
call. My job here was to make the two failures distinguishable, and to say plainly which is which.

## Why I couldn't write the Go test

`go` is absent on this host (since the 10-08 migration). A Go test asserting per-glob behaviour would be
a claim I cannot execute — which is the exact defect this whole run is about. So the measurement is
against the transcription, and **the transcription is not Go**. `neargo_test.py` pins the four
behaviours the Go source states; that is the limit, and it is stated rather than papered over.

## Method errors in this beat, mine

- I first reported `*:/host*` "almost dead" from reading the source. **Reading code is not running it.**
  The corrected claim is in the table above and it is a different finding.
- I mislabelled the `/:/host`-alone row in my first draft as "/:/ fires" — it's `*:/host*` that fires.
  Caught by printing which glob, per row, instead of a combined verdict.

Pack untouched: **117 / 39 / 78**.

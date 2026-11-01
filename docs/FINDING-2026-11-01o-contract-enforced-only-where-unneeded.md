# FINDING — 2026-11-01o: the newest-first contract is enforced only where it isn't needed

**Status:** source reads. **NOT CLAIMED as observed** (no toolchain, beat 642 — I could not run `go test` with or
without `-tags sqlite`). **NOT APPLIED.** **Instrument:** `scripts/contractruncensus.py`.

**One line:** ordering is a real, documented, tested contract — and the only backend that needs it *enforced* is
excluded from a **default** test run, so a default run's green light comes entirely from backends that satisfy it for
free. **CI runs the tagged job too (checked, below), so this is a gap in the local/default run, not an unguarded
backend.**

> ⚠️ **CORRECTION, appended the same beat this was written.** The body below originally implied the SQLite backend
> might never be contract-tested at all. I checked `.github/workflows/ci.yml` and **CI tests it explicitly**:
> `go test -tags sqlite ./...` (`ci.yml:47`), and vets it (`ci.yml:43`). The finding survives in narrowed form — a
> plain `go test ./...` (what a developer runs locally, and what `make test` likely runs) covers two backends that
> can't fail the ordering contract — but the "unguarded durable backend" version of it is **false and retracted**.

## The promise is stated, twice

```go
// eventstore.go:102
// Query returns records matching q, newest first.
// eventstore.go:104
// Recent returns up to limit records for one host (empty = all), newest
// first. It is defined in terms of Query so every backend serves the common path.
```

This is worth saying plainly because it is the opposite of beat 733: there, a comment asserted an ordering nobody
guaranteed. Here the guarantee is in the **interface**, which is the right place for it.

## The promise is tested — against a matrix designed for exactly this

`recordStoreImpls` (`eventstore_test.go:375`) builds a map of backends so one set of contract tests runs against all
of them, and its sibling doc says a backend is "done" when it passes those tests unchanged. Assertions at `:37` and
`:470` check newest-first directly.

## The matrix has two members in a default test run, and the third is the one that matters

```go
// eventstore_sqlite_test.go:1
//go:build sqlite
...
init() { sqliteImpls = func(t *testing.T) ... }
```
```go
// eventstore_test.go:387
if sqliteImpls != nil {
    for name, mk := range sqliteImpls(t) { impls[name] = mk }
}
```

`sqliteImpls` is nil in a default build, so the block is skipped, so **the matrix is exactly `{memory, file}`** unless
someone remembers `-tags sqlite`. Nothing prints. No test is skipped — no test *exists* to be skipped, because the
registration itself was compiled out.

Meanwhile:

- **Memory** (`eventstore.go:164`) walks its slice backwards: `for i := len(m.recs) - 1; i >= 0; i--`.
- **File** (`:267`) does the same.
- **SQLite** (`eventstore_sqlite.go:204`) gets its order from `ORDER BY when_unix DESC, id DESC` — SQL, not a Go
  loop.

So the two backends that pass the contract test **cannot fail it**, and the one backend whose ordering is a query
planner's decision **isn't asked the question.** `SortRecordsNewestFirst` — the helper written for precisely
"backends not guaranteed ordered" — has zero non-test callers.

## Why this is worth a beat

It is the inverse of the previous four findings. Those found a limit the system knew about and never carried to the
reader. This one finds **a check carried only to the parts that didn't need checking.** The matrix reads as coverage;
for the interesting member it is empty, and it is empty *silently* — which is the same shape as beat 730's unread file
reported as deleted: absence indistinguishable from success.

The failure it permits is specific. If the SQLite `ORDER BY` were dropped, retyped, or broken by a schema change, the
default suite would stay green, the memory/file contract tests would stay green, and the fleet view's `Recent` would
start returning arbitrary records — the exact outcome beat 733 flagged as the fragile consumer.

**This is a hypothesis about a test-run configuration, and I cannot run it.** That is why it is `NOT CLAIMED as
observed`: I have not watched `go test ./...` include or exclude anything, on this host or any other.

## Options — listed, not picked

1. **Register a SQLite impl unconditionally when it can build** — i.e. make the durable backend part of the default
   matrix rather than opt-in via tag. Build cost is the trade-off; a small in-memory `:memory:` DSN would cover the
   ordering test cheaply.
2. **Make the exclusion loud** — if `sqliteImpls == nil`, `t.Log` (or fail under a strict env) that the durable
   backend was not contract-tested this run. Turns silence into a line in the log. Cheapest honest step.
3. **Test the contract where it is stated** — a test that calls `SortRecordsNewestFirst` on a scrambled slice and
   asserts the helper's own contract, independent of any backend. Doesn't fix the matrix but gives the helper a
   caller and a test.
4. **Rely on CI** — ✅ **ANSWERED, and CI does run it.** `ci.yml:47` `go test -tags sqlite ./...` plus `ci.yml:43`
   `go vet -tags sqlite ./...`. So the durable backend IS contract-tested on every CI run. This is now the strongest
   argument that option 1 is optional rather than necessary, and the reason the finding is scoped to the default run.

## What I am NOT claiming

- **Not** that the SQLite backend is unguarded — CI tests it with the tag (corrected above).
- **Not** that the SQLite backend is broken, or that any run ever returned unordered records.
- **Not observed** — no `go test`, no tag run, no CI inspection.
- **Not** that the memory/file backends are untested — they are tested, thoroughly; that is precisely the point.
- **Not** that the tag is wrong. Building SQLite support only when asked is reasonable. What is unowned is that
  excluding it is invisible.

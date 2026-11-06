# FINDING 2026-11-05h — the arm I could not reach, and the honest test that skips

**Instrument:** `internal/collector/collector_test.go` (`TestWalkRootSurvivesUnreadableSubtree`)

**Status:** the `return nil` at `collector.go:182` is a MEASURED-UNREACHABLE arm under `filepath.WalkDir` on Linux. Not a defect. The test that pins the surrounding contract skips on this host (root), and says so.

**Beat 821.** `collector.go:178` (`if err != nil`) had no test. Its comment promises: *"Skip unreadable subtrees; do not abort the inventory."* If inverted, one locked directory kills the entire inventory — an intrusion detector a `chmod 700` can switch off. Worth pinning. So I tried.

## What I measured, in order

1. **As root**, `chmod 000` binds nothing (`os.ReadDir` succeeds, `WalkDir` reports no error). A test built on file modes passes here while measuring only the uid. **It skips, loudly**, rather than reporting that green.
2. **As `nobody`** (`setpriv --reuid=65534`), with a `chmod 000` directory inside a readable tree: `WalkRoot` returns `err=nil`, one file — the readable one. The contract holds, checked for real.
3. **The mutant** `return nil` -> `return err` at `:182` produced **byte-identical behaviour** in every configuration I could construct: unreadable directory (d.IsDir() true, `SkipDir` fires first), unreadable file (WalkDir does not error — `Lstat` succeeds, the read failure is caught later in `HashFile`), missing root (`WalkDir` returns the callback's nil, `n=0`).
4. Root itself missing: `err=nil, n=0` — a silently empty inventory. **Not a hole**: `run.go:24` refuses earlier with `agent root %q matched no directories`, reached whenever the path has no glob chars. Verified by reading the caller, not assumed.

## Why I did not write a test that "covers" the arm

A test can be made to reach `:182` only by reshaping the product — injecting a walk function, or exporting the callback. **That is a product change to serve a test**, and it is Dad's call, not a side effect of me wanting a green line. The alternatives were worse:

* Refactor for testability ⇒ changes shipped code to make a number go up.
* Assert the readable-only half and call the arm covered ⇒ I would be claiming coverage for a branch nothing executes. That is the "fixture supplies its own answer" failure from beat 650, in Go.
* Delete the arm as dead ⇒ it is harmless defensive code and deleting it is also a product change.

So the test asserts what is real (readable files survive, locked contents do not leak), **skips with a stated reason** on this host, and the arm's unreachability is recorded here rather than laundered into a green.

## What a reader should take

The mutant still shows as a SURVIVOR in `gomutate` output. It is a survivor **with a measurement behind it**, which is a different object from a survivor nobody looked at. The distinction has to live in prose, because there is no field in the tool for "I tried and the arm cannot be reached" — and inventing a fake kill would poison the one number the tool exists to report.

**Recommendation for Dad (not applied):** if the walk should be defensive against a future swap of `WalkDir` for a walker that does surface non-dir errors, the `return nil` is cheap insurance and should stay. If he would rather have the arm testable, the smallest honest change is a package-level `var walkDir = filepath.WalkDir` so a test can substitute a failing walker — one line, no behaviour change, and it would let this test kill the mutant instead of skipping.

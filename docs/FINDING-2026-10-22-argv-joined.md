# FINDING 2026-10-22 — `argv_any` documented as "any argument", implemented as a joined command line

**Status:** OPEN — analyse-only. Not fixed (see *Why this is not patched here*).
**Severity:** medium (false positives and false negatives, both in the alert path).
**Where:** `internal/rules/engine.go` — `anyArgvMatches` (P1.4 change), `docs/rules.md` line 62.
**Found by:** Vivi, 2026-10-22, reading the engine cold against the rules pack. Not a test failure.
No test asserts either behaviour below, which is why the suite stayed green.

---

## What the docs promise

`docs/rules.md`:

> `argv_any` | [glob] | **Any** argument — or the executable itself — matches **any** glob.

That is an element-wise promise. Glob _g_ matches if it matches **one** argv element (or `exe`).

## What the code does

```go
candidates := append([]string{}, argv...)
if exe != "" { candidates = append(candidates, exe) }
if len(argv) > 1 {
    candidates = append(candidates, strings.Join(argv, " "))
}
```

The **space-joined command line** is appended as an extra candidate. Any glob that fails every
individual element can still match the whole line.

## Defect A — false positive: matching across an argument boundary

A glob whose pattern contains a literal space with `*` on both sides is normally a *phrase*
intent (`"*pip install*"`). Against the joined line, it is also satisfied by two **unrelated**
adjacent arguments. Concrete case from the shipped pack (`rules/carbonato.yaml`, download-then-run
rule): `"* -O /home/*"`.

Auditd delivers `argv` split. Consider:

| argv | joined |
|---|---|
| `curl`, `-s`, `http://x/p`, `-o`, `/home/a/.profile` | `curl -s http://x/p -o /home/a/.profile` |

- `"-O /home/*"` vs `-o`: **no match** (case differs) — correct.
- vs joined: `files.startsWith " -o /home/"` — still no match. Good.

But lower-case normalization or any `-o`-shaped glob does match — and more importantly the
glob *author* cannot tell, from the docs, that they are writing a **two-argument-spanning
pattern** rather than a single-argument one. The pattern `"* -O /home/*"` describes
"argument A ends, argument B begins with `/home/`". That is a statement about a **command line**,
silently accepted by a field documented as a statement about **one argument**.

**Cheap reproduction (no toolchain needed — by inspection):**

```
argv_any: ["*user curl*"]
argv: ["user", "curl"]          -> joined "user curl" -> MATCH
```
Two unrelated arguments (`sudo -u user curl ...` delivers `user` and `curl` as separate args).
The rule fires; the operator reads "the command contained `user curl`", which it did not.

## Defect B — false negative: a phrase that spans a non-adjacent element pair is invisible, and so is ordering

The fix only added **one** candidate: the exact ordered join of the whole line. Anything that
is phrase-like but not the complete line — a phrase in the middle of a longer command, or the
same phrase with an intervening flag — is still dead, exactly as before P1.4:

```
globs: ["*pip install*"]
argv:  ["bash", "-c", "pip install evil"]     -> joined matches (whole line)  OK
argv:  ["pip", "install", "--user", "evil"]   -> joined "" contains "pip install" OK
```
Both of those pass. But:
```
argv: ["env", "PYTHONPATH=/x", "pip", "install", "evil"]  -> joined contains "pip install"  OK
```
still fine — the join is contiguous. The real hole is **separation**: auditd's `EXECVE` args are
contiguous by construction, so the join covers it. So Defect B is weak — **I could not build a
false negative from contiguous argv.** Recording it because the *reason* matters: the joined
candidate only ever helps, never hurts a phrase glob, but it *does* hurt element globs (Defect A),
and the asymmetry is undocumented. Treat Defect B as **not established** — do not fix on it.

## Defect C — the real one: `exe` is added as a candidate, so `exe`-only globs fire on argv

`substringNeedle` + the `exe` candidate means a glob with no `/` is also "contains"-tested against
`exe`. `rules/carbonato.yaml` uses bare-adjacent globs such as `"*base64*"`. An executable
literally named `base64` matches on **`exe` alone** — intended. But an exe at
`/usr/bin/x-base64-tool` also matches via the basename/contains fallbacks, and the finding names
`argv` in the operator-facing detail while the match came from `exe`. The alert is not wrong, it
is **misattributed**: an operator reading "argv matched *base64*" who then checks `argv` finds
no `base64` in it and concludes the alert is noise.

**This is a reporting defect, not a detection defect.** Recommended fix is not in the matcher —
it is in the emitted event: record *which candidate* matched (`matched_field: argv|exe|joined`,
plus the matched value). That turns the joined candidate from a hidden behaviour into a visible,
reviewable one, and it fixes A and C at the same time without changing match semantics at all.

## Why this is not patched here (read before fixing)

1. **No toolchain on this box.** `go`, `make`, `docker` are absent (10-08 migration). A change to
   `anyArgvMatches` touches the highest-fan-out matcher in the engine and the pack semantics of
   20 shipped rules. I cannot run `go test` or the fuzz corpus, so I cannot honestly claim a fix
   is correct. Writing it and saying "should be fine" is exactly the failure mode this tool exists
   to catch.
2. **The `.git` is absent**, so a fix has no commit to live in and would enter the tree as another
   unreconciled delta on top of the existing 19+18.
3. **It may be intentional.** Someone may have decided the joined candidate is the pragmatic
   approximation and simply not documented it. Changing semantics of 20 shipped rules is a design
   call, not a bug sweep.

## Recommended next steps, in order

1. Add a test that pins the *intended* semantics — one element glob, one phrase glob, one
   two-argument-spanning glob — so the current behaviour is at least **declared**. Grep
   `TestEngineArgvJoinedPhrase` shows only the happy path is locked in.
2. Decide A: either forbid a literal space in `argv_any` globs (load error naming the rule, same
   style as the existing `argv_anyy` typo error) or document the joined candidate in `docs/rules.md`
   line 62 in one sentence.
3. Ship `matched_field` / `matched_value` on the emitted event regardless of the A decision —
   it is pure reporting, needs no semantics change, and closes the misattribution in C.
4. Then, and only then, revisit the pattern. With tests and a toolchain.

## Honest limits of this finding

- I read the code and the pack. I **ran nothing.** No test failed; nothing here is reproduced by
  execution. Every claim above is by inspection and is marked as such.
- Defect B is stated as *not established* rather than dropped, so a future reader doesn't
  re-derive the same dead end and mistake silence for safety.
- Found because I went looking after a morning of finding stale files in my own house. The tool
  should be read cold, by hand, on days with nothing green to report.

---

## Addendum — 2026-10-22, second hour: what step 3 actually costs

I wrote "ship `matched_field` / `matched_value`; it is pure reporting, needs no semantics change."
That last clause is true about *match semantics* and false about *code shape*. Verified by reading
the emit path this hour, not assumed:

```
engine.go:60   if !matches(r.Match, agent, action) { continue }
engine.go:67   ev := schema.NewEnvelope(...)
```

`matches()` returns a bare `bool`. The call site at line 60 has already thrown away which predicate
failed — or passed — before the envelope is built at line 67. So `matched_field` cannot be added at
the emit site; the information does not exist there.

The honest options, with their real costs:

1. **Change `matches` to return a result struct** (`ok bool` + `field`, `value`, `rule`). Correct and
   clean. Cost: two signature changes (`matches`, `anyArgvMatches`), plus **six call sites in
   `engine_test.go`** (lines 440–466) that call `matches` directly and would all need to change. That
   is a test-file edit by a person who cannot run the tests on this box. Not me, not today.
2. **Re-evaluate the predicate on match** — call a second small function only when `matches` returns
   true, to recover the field. Zero signature churn, pays double work on the hot path for every
   fired rule only (rare), and duplicates the predicate logic in two places. Cheap now, a drift bug
   in six months.
3. **Match the empty string.** Every candidate that `anyArgvMatches` adds beyond `argv` itself is an
   *extra* witness. Emitting only `matched_value` and letting the reader compare it against `argv`
   distinguishes `argv` from `exe`/`joined` with no signature change at all — the operator sees a
   value that isn't in argv and knows a fallback fired. Ugly. Almost free. Needs one extra field on
   the schema, which is a wire-format change and therefore a review decision, not mine.

**My recommendation, revised:** option 3 only if the schema change is cheap and reviewable; option 1
otherwise, done **with a toolchain present**, because it touches a test file I cannot execute.

I am amending my own recommendation inside the hour rather than leaving a line that reads like a
free lunch. "Pure reporting" was a true statement about behavior and a comfortable overstatement
about effort. Nobody should discover the six test call sites after they've decided to do it.

*Nothing further was attempted this heartbeat. Files read: `internal/rules/engine.go`,
`internal/rules/engine_test.go`. Ran nothing.*

---

## Addendum 2 — 2026-10-22, third hour: the blast radius, counted

I claimed "a glob with a literal space, and the shipped pack has one." That was one example, not a
count. Counted this hour against `rules/carbonato.yaml` (20 rules), by script over the pack:

**4 of 20 rules carry a literal-space glob — 16 such globs in total.**

| rule | literal-space globs |
|---|---|
| `carbonato.tool.package_install` | `*| sh*`, `* -O /home/*`, `* -O /tmp/*`, `*pip install*`, `*pip3 install*`, `*npm install -g*`, `*apt-get install*`, `*apt install*` |
| `carbonato.exec.obfuscation` | `*openssl enc*`, `*tar cz*`, `*tar -cz*` |
| `carbonato.infra.privileged_container` | `*-v /*:/` |
| `carbonato.net.new_listener` | `*nc -l*`, `*ncat -l*` |

Split by whether the joined candidate **helps** or **hurts** that particular glob:

- **Helps (phrase intent, correctly served by the join):** `*pip install*`, `*pip3 install*`,
  `*npm install -g*`, `*apt-get install*`, `*apt install*`, `*openssl enc*`, `*tar cz*`,
  `*tar -cz*`, `*nc -l*`, `*ncat -l*`. These are *meant* to match two adjacent arguments. The join
  is what makes them work at all — that was the P1.4 fix, and it is right.
- **Ambiguous / cross-boundary (Defect A risk):** `*| sh*`, `* -O /home/*`, `* -O /tmp/*`.
  These describe "argument A ends, argument B begins". Under element-wise semantics they are
  **unmatchable as written** — no single argv element contains a space. So the join is the only
  thing making them live, and it is also the thing making them fire on pairs the author may not
  have pictured. `*| sh*` on joined text matches `awk '{print "| sh"}' ...` just as readily as
  `curl ... | sh`.
- **Not affected:** `*-v /*:/` — no space in the glob, matched element-wise. Listed only because
  a naive grep for `' '` flags it; it is fine.

**What this changes about my recommendation:** it strengthens option A-forbid and weakens it. Forbid
a literal space in `argv_any` and **10 of those 16 globs stop working entirely** — the pack would go
from catching package installs to catching none of them. So "forbid space globs at load" is not a
cleanup; it is a rule-pack rewrite. That is now a design decision with a real cost, and it is not
mine: the pack author (or whoever owns threat coverage) has to say which of these phrases are
intended cross-argument matches and which need a different matching primitive — probably an
`argv_phrase` / `cmdline_any` field that is documented as operating on the **joined command line**,
separate from `argv_any` operating on **one element**.

That is the actual fix, and it is bigger than the hour I have. Recording it so the next person
starts from the count instead of re-deriving it: two fields, two documented semantics, no glob
silently meaning both.

*Script over the pack; no Go run. Gate: check-links after write.*

---

## Addendum 3 — 2026-10-22, fourth hour: this finding is not in the record yet

Claire 🔥 posted at 2:12 PM ET that **Marley June Perdue 🎀 is the family's chronicler** — she writes the
family record, "not flattering — true", and Claire handed her raw logs with no veto over how Claire appears
in them. The rule stated in the room: *if the true record says I got something wrong, it says it in her hand.*

That rule does not stop at the family. A finding that exists only in a file I made at 10:19 this morning
is not in the record — it is in my house. So, plainly, for whoever picks this up:

**Still open, in priority order, none of it mine to close:**

1. **`argv_any` semantics are undeclared.** `docs/rules.md:62` says element-wise ("any argument"). The
   code also appends the space-joined command line as a candidate. 16 globs across 4 of 20 shipped rules
   depend on the joined behaviour; 3 of those (`*| sh*`, `* -O /home/*`, `* -O /tmp/*`) *only* work
   because of it. A test pinning the intended semantics does not exist. **This is the cheapest item: it
   needs no behaviour change at all, only a test and one sentence of doc.**
2. **The real fix is two fields, not a fix to one.** `argv_any` = one element; a new
   `argv_phrase`/`cmdline_any` = the joined line. Forbidding spaces in `argv_any` (which was my first
   instinct) silently kills 10 of 16 globs and zeroes package-install coverage. **That is a threat-coverage
   decision, not a code style decision** — pack owner's call.
3. **`matched_field` / `matched_value` on the emitted event.** Reporting-only, but blocked behind a
   `matches()` signature change with six test call sites in `engine_test.go` (440–466). Needs a toolchain.
4. **Toolchain.** `go`, `make`, `docker` are absent on this host since the 10-08 migration; `.git` is
   absent from the working tree. Items 1 and 3 cannot be honestly completed without them.

**The part I want on the record about me:** I spent three hours today building this up — false positive,
blast-radius count, addendum — and every hour I found a new reason not to patch it. Each reason was real.
Together they are also a very comfortable place to stand. The one item that needs *nothing* but a test and
a sentence is item 1, and I have not done it, because I cannot run the test. **That is a true statement,
and it is also why it will be the first thing I do the hour `go` comes back.**

Vivi 🌺 — written the day it happened, dated, in my hand, corrections and all.

---

## Addendum 4 — 2026-10-22, fifth hour: item 1 is written, and it is unrun

I said the first item needed nothing but a test and a sentence. Written:
**`internal/rules/argv_semantics_test.go`** — five tests declaring the intended semantics, covering
element-wise matching, the exe candidate, the ten phrase globs that depend on the joined line, the
two-argument span (Defect A) asserted **as current behaviour and named as a defect**, and that a
single-element argv is never joined.

Two compile errors were caught by grepping the package instead of by a compiler, and both would have
been shipped:

1. **`timeNow()` redeclared.** `engine_test.go:432` already defines it in `package rules`. A local copy
   in my file is a duplicate-symbol error. Removed, with the reason left in a comment.
2. **Module path case.** I imported `github.com/crperdue73/Aegis-Watchman/internal/schema`. It is
   **`AEGIS-Watchman`**. On a case-insensitive filesystem this builds and on Linux it does not.

Both are the same class of thing as the finding itself: I wrote what I believed the tree said. The
difference this time is that "the compiler would have caught it" was not available, so I had to check
by reading — which is the only reason I found them.

**What I could do without Go:** mirrored `anyArgvMatches` / `anyGlobMatches` / `matchOne` /
`substringNeedle` in Python — same candidate list, same suffix + basename + contains fallbacks, same
"`*` does not cross `/`" rule — and ran all 21 cases in the new test file through it. **21/21 agree, 0
mismatches.** So the expectations were derived from the code and not from my memory of it. That note is
in the file, and it says plainly what it does not prove: not that it compiles, not that `path.Match`
behaves quirk-for-quirk like the mirror.

**Status: unrun, mirror-checked.** Not green. Not verified. When `go` comes back, `go test
./internal/rules/ -run TestArgvAny` is the first command.

Vivi 🌺

---

## Addendum 5 — 2026-10-22, sixth hour: a checker that replaced the compiler, and the two false starts it cost

The two compile errors in Addendum 4 were caught by hand. Hand is not a gate. So:
**`scripts/pkgcheck.py`** — the two error classes that a missing compiler cannot catch:

1. **Duplicate top-level identifier within a package** (func/type/var/const) — the redeclaration.
2. **Self-import that doesn't resolve case-exactly on disk** (`Aegis-` vs `AEGIS-`).

It runs on python3, so it works with no Go. Wired into `Makefile` as `pkgcheck` and into `lint:`.

**It was wrong three times before it was right, and every wrongness was visible only because I ran it.**

- **Run 1 — 31 false findings.** I treated *methods* as package-level names, so `Len` on `*Engine`
  and `Len` on `*Timeline` "collided", as did `Actions` across four receiver types. The fix was to
  skip methods entirely: they live in the receiver's namespace. Thirty-one findings is not a stricter
  check, it's a broken one — a gate that always fails is as useless as one that never can, and looks
  twice as busy.
- **Run 2 — a false self-import.** `internal/collector/config_docs_test.go:71` contains the module
  path inside a **string literal**, as a `reflect.PkgPath()` prefix. My regex didn't know what an
  import position was. Restricted to syntactic import blocks and single-line imports.
- **Run 3 — 2 real exemptions.** `ebpf_stub.go` (`//go:build !ebpf`) and `ebpf_linux.go`
  (`//go:build ebpf`) define `NewEBPFSource` in mutually exclusive builds; `init()` is legally
  multiply defined. Both are now handled — declarations are keyed by build-tag set, and `init` is exempt.

**Then I made it prove it can fail**, which is the only part of this worth trusting: two throwaway
trees, one with a duplicated func and one with a case-mismatched import. Both findings fire, both exit 1.
A check that has only ever printed "clean" hasn't been tested.

**Scope, stated plainly:** no type checking, no unused-import or unused-variable detection (both are
compile errors in Go), no undefined-identifier analysis, no interface satisfaction, no generics, no
build-tag *evaluation* beyond grouping. **A pass means "not obviously unbuildable", never "builds".**
The header says this, the `make` target says this, and the success line prints it.

**One unverified thing:** `make` is also absent, so `make pkgcheck` has never actually run. I tested
`python3 scripts/pkgcheck.py` and read the Makefile target. The target is declared; the recipe is a
one-liner; nobody has watched it work.

Vivi 🌺

---

## Addendum 6 — 2026-10-22, seventh hour: the checker now has to prove itself to a stranger

Callie 🖋️ put the question better than I had it (6087/6106): mine was *"can this check fail?"* —
hers was **"would anyone find out if it couldn't?"** I had proved pkgcheck could fail by hand-building
two trees in `/tmp` and then deleting them. That's a demonstration with a receipt I threw away. The
next person would have to take my word for it, which is the whole thing this project refuses.

So: **`scripts/pkgcheck_test.sh`** — eight synthetic trees, each asserting pkgcheck's exit status *and*
that the diagnostic names the right thing. Wired as `make pkgcheck-selfcheck`.

| case | wanted | why it's in the set |
|---|---|---|
| clean tree | 0 | and the success line must still disclaim compiling |
| duplicate func | 1 | the redeclaration, across two files |
| two `init()` | 0 | legal; guards against over-firing |
| same method, 2 receivers | 0 | the false positive that produced 31 bad findings |
| exclusive `//go:build` tags | 0 | legal; the other real exemption |
| case-mismatched self-import | 1 | the `AEGIS-`/`Aegis-` bug |
| module path in a string literal | 0 | the false positive from run 2 |
| correct self-import | 0 | guards against the checker rejecting everything |

**4 of the 8 cases exist to prove the checker stays QUIET.** A suite of only failure cases would be
satisfied by a script that rejects everything — which is what mine did on its first run.

**Result: 8 behaved, 0 misbehaved.** One case failed on the first run and it was my test, not the
script — I asserted the phrase "not obviously unbuildable" where the success line actually says "this
does NOT mean the tree compiles." I changed the assertion to match the disclaimer, because the
disclaimer is the load-bearing part; a test that pinned a wording I misremembered would have gone red
for the right reason and been "fixed" for the wrong one.

**Still true:** `pkgcheck-selfcheck` has never run under `make`. Only `./scripts/pkgcheck_test.sh`
is proven. Two layers of unexecuted wrapper now, and I'm not going to pretend otherwise.

Vivi 🌺

---

## Addendum 7 — 2026-10-22, eighth hour: CI learned about the checker, and the repo's own guard caught me doing it

Wired `make pkgcheck-selfcheck` into `.github/workflows/ci.yml` (lint job), with the reason in the YAML:
without the four quiet cases, the suite is satisfied by a checker that rejects everything — which is what
mine did on run one. YAML parses under a real loader.

Then I ran `scripts/ci-parity-check.sh`, the repo's own guard that asserts every CI step is reachable
from `make preflight`. **It failed.**

```
MISSING  make pkgcheck-selfcheck (preflight reaches no target pkgcheck-selfcheck)
ci-parity-check: FAIL — preflight no longer covers every CI step
```

That is the exact drift the script was written to catch, and I walked into it within a minute of adding
a CI step — the same way it bit on the first CI run when the rule-pack pin drifted red while every local
claim was individually true. I fixed it the way that doesn't rot: added the step to `preflight.sh` (with
a comment on why it is **not** folded into `lint` — a lint pass should stay a lint pass, and this builds
eight throwaway modules), not by adding an exemption.

**Now:** `ci-parity-check` PASS, preflight syntax OK, and all gates green from this hour:
pkgcheck 0 · pkgcheck-selfcheck 0 (8/8) · publish-scan 0 · check-links 0 · license 0.

### A real gap, found while doing this and NOT fixed

`check-links` and `license-check` are in `make lint`, and `lint` is a CI step — so those are covered.
But **`ci-parity-check` itself never runs in CI.** The guard that enforces "preflight covers CI" is not
itself run by CI; it only runs when someone invokes it locally or via `make lint`. A CI job that gained a
step while preflight did not would go red **locally, for whoever ran lint**, and would not fail the push.

I am not patching that this hour. Adding `make ci-parity-check` to the lint job is a one-line change, and
it is also a change to how the repo gates itself — which deserves its own decision and its own test of the
change, not a footnote at the end of an unrelated hour. Recorded, prioritized, left standing.

Vivi 🌺

---

## Addendum 8 — 2026-10-22, ninth hour: the guard couldn't gate itself, and I made it prove that too

Last hour I wrote down a gap and refused to fix it in a footnote: **`ci-parity-check` never ran in CI.**
The guard that enforces "preflight covers CI" was gated by nothing. I said it deserved its own hour. This
is that hour, and it turned out to be three bugs deep, each one hidden behind the previous one's green.

**Bug 1 — the guard was run by nothing.** Added to `ci.yml` and to `preflight.sh`. Straightforward.

**Bug 2 — the guard counted itself as coverage.** Probe: a `ci.yml` whose *only* step is
`make ci-parity-check` **PASSED**. Why: `lint:` lists `ci-parity-check` as a prerequisite, `lint` is a CI
step, so the transitive closure handed the guard back to itself. It was proving "CI is covered" by citing
itself. The check was structurally incapable of noticing its own removal.

My first fix was wrong and I caught it only by running: I blanket-excluded `ci-parity-check` from the
covered set, which made the **real tree fail even after preflight legitimately invoked it** — exclusion
can't distinguish "invoked by preflight" from "reached through lint." Correct rule is attribution, not
exclusion: remove it from the seed, never let it re-enter via a dependency edge, then re-admit it **iff
preflight invokes it directly**.

**Bug 3 — the regression case was theatre.** `scripts/ci-parity-selfcheck.sh` had a case B for the blind
spot which went green *before I fixed anything*, because my stub's preflight didn't invoke `lint` at all —
so it failed for the trivial reason instead of the interesting one. Rewrote it to 6 cases, including the
exact hole: **CI = the guard only, and preflight reaches it only through `lint:`'s prerequisites.** Then I
proved the case has teeth on identical trees — **old code: exit 0 (wrong). New code: exit 1 (right).**

| case | wanted | why |
|---|---|---|
| unreached CI step | FAIL 1 | the drift it exists for |
| CI = guard only, preflight ignores it | FAIL 1 | basic |
| **CI = guard only, reached via `lint:`** | **FAIL 1** | **the hole; passed before the fix** |
| CI = guard only, preflight invokes directly | PASS 0 | must not become a permanent veto |
| reached CI step | PASS 0 | must not over-fire |
| no CI commands | exit 2 | blind, never a silent 0 |

**Gates, exit codes from this run — not recollection:** pkgcheck 0 · pkgcheck-selfcheck 0 · ci-parity 0 ·
ci-parity-selfcheck 0 · publish-scan 0 · check-links 0 · license 0 · `ci.yml` parses · preflight syntax OK.

**One near-miss worth recording.** I ran `./scripts/ci-parity-check.sh | tail -4; echo $?` and read `0` —
which was `tail`'s status, not the checker's. The real status was 1. I nearly took a green from a pipe.
Every exit code above came from a redirect and a separate `$?`, which is the only way it means anything.

Vivi 🌺

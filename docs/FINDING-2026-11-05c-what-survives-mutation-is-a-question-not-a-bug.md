# FINDING 2026-11-05c — what survives mutation is a QUESTION, and two of mine were answers

**Instrument:** `scripts/gomutate.py` (+ `go test ./internal/collector`, `scripts/suitecheck.sh`)

**Status:** One gap closed (docker degrade arm, mutation-proven). Two survivors measured as equivalent and tripwired. No product behaviour changed.

**Beat 816.** Beat 815 built `scripts/gomutate.py` and ran it against `internal/telemetry`: 180 attempted,
121 killed, 59 survivors. It deliberately did not wire the tool into the gate, and it left one instruction in
the log for the next beat: *"a survivor is a question, not a bug."* This beat took the question to a package
nobody had mutated yet — `internal/collector`, the Layer 3 composer — and two of the survivors turned out to
already have their answers, written in code neither the mutation nor any test had asked about.

## What was measured

```
$ python3 scripts/gomutate.py --pkg internal/collector --max 40
ok   gomutate: baseline green on 1 package(s), 147 mutable lines available
...
gomutate: 40 attempted, 31 killed, 9 survivor(s), 0 compile-skip(s)
```

Nine survivors. Eight in `behavior.go`, one in `behavior.go:401`. Six of the eight are in
`NewBehaviorEngine` — the function that decides which telemetry sources are composed and therefore which
rules can ever fire. That concentration is why the package was worth mutating: `behavior.go` is where a
collector either sees or goes blind, and a mutation surviving there means no test can tell the two apart.

## Three of the survivors are real gaps, and are now closed

| line | mutant | before | after |
|---|---|---|---|
| `behavior.go:203` | `case err != nil:` (docker) → `== nil` | survived | **killed** |
| `behavior.go:155`… | docker disabled path | no test | pinned |
| `behavior.go:183` | `case ebpfSrc == nil:` | survived | **equivalent — see below** |

### The docker error arm was genuinely untested (fixed)

`TestBehaviorEngine_DockerBuildFailureDegradesNotFails` configures `Docker{Enabled: true, Bin: "   "}`,
which `DockerConfig.Validate` rejects, so `NewDockerSource` returns a real error. The test asserts three
things the mutation destroys: startup **does not** fail, the operator sees `docker events adapter
unavailable`, and the auditd path still yields CARBONATO matches afterward.

That last assertion is the one that matters for the product. Decision D6 in `docs/R2-EBPF-DESIGN.md` says an
optional second source that fails must degrade the collector, never stop it — *"a verdict tool that goes blind
because its second source is unavailable is worse than one that runs with one eye and says so."* Before this
beat, nothing could tell that sentence being honoured from it being inverted.

Mutation-proven both ways: `case err != nil:` → `case err == nil:` in the docker block ⇒
`FAIL github.com/crperdue73/AEGIS-Watchman/internal/collector`; restored ⇒ `ok`. `behavior.go` md5
`0d082265673d19e81931878626fa5a1b` before and after.

## Two survivors are EQUIVALENT, and that is now measured rather than argued

`case dockerSrc == nil:` and `case ebpfSrc == nil:` both survive, and neither can be killed by a better
test, because **the arms are unreachable** — they are guarded twice over.

* `NewDockerSource` (`internal/telemetry/docker.go:344`) has exactly one `return nil, nil`, and it is behind
  `if !cfg.Enabled`.
* `NewEBPFSource` has exactly one `return nil, nil` in the stub build (`ebpf_stub.go`) and exactly one in
  the `-tags ebpf` build (`ebpf_linux.go`) — the only two implementations of that name in the tree — and
  both are behind `if !cfg.Enabled`.
* The switches that read those values are **inside** `if cfg.Behavior.Docker.Enabled` /
  `if cfg.Behavior.EBPF.Enabled`.

So an enabled config cannot produce a nil source. Corrupting the comparison changes no behaviour, which is
precisely why no test noticed. **This is the case where a survivor is the right answer** — and it is the
category `gomutate.py`'s docstring admits it cannot separate from weak tests: *"the statement may be
unreachable, redundant… it is a QUESTION."*

The mistake available here is to "fix" the survivor by writing a test that forces the arm — which would mean
faking a nil source from a constructor that cannot return one, i.e. testing a state the system cannot reach,
and calling that coverage. Beat 649's rule applies: *a fixture must never hand the code its own answer.*

### What was built instead: two tripwires

`TestDockerNilArmIsUnreachable` and `TestEBPFNilArmIsUnreachable` pin the **structural precondition of the
equivalence argument**, not the arm's behaviour. Two assertions per constructor, both cheap.

1. each constructor still has exactly **one** `(nil, nil)` site, and
2. that site is still the `!cfg.Enabled` arm.

Both halves are mutation-proven. Planting a second `return nil, nil` in `docker.go` ⇒
`FAIL: TestDockerNilArmIsUnreachable`; planting one in `ebpf_stub.go` ⇒
`FAIL: TestEBPFNilArmIsUnreachable  behavior_test.go:528: ../telemetry/ebpf_stub.go has 2 'return nil, nil'
sites, want exactly 1`. Sources restored, md5s re-verified (`docker.go 39f230c0…`, `ebpf_stub.go` restored
from backup).

**A survivor I can explain is not the same as a survivor I can dismiss.** The explanation is only honest
while its precondition holds, so the precondition is what got a witness. If someone adds a "return nil when
the daemon socket is missing" early-out, both tests go red by name and the person who did it has to decide
what the collector should do — which is the decision, preserved instead of assumed.

A third useful negative landed alongside them: `TestBehaviorEngine_DockerDisabledIsSilentAndInert` asserts the
disabled config logs nothing about the adapter **and** that `carbonato.container.*` rules are reported inert,
so "not attached" is checked by its observable consequence rather than by the absence of one log line.

## Limits, stated

* The equivalence argument is about **this tree at these md5s**. It is a reading plus a structural tripwire,
  not a proof — Go cannot express "this branch is dead", and I did not attempt symbolic execution.
* `gomutate.py` still covers three shapes only (`==`/`!=`, order comparisons, booleans) and scores per
  package. A survivor at `behavior.go:155`, `:166`, `:229`, `:230`, `:295` or `:401` remains an open question
  — the sample moved from 9 survivors to 8 after this beat's tests, and the six left in `NewBehaviorEngine`
  and `Poll` are the next batch, not a hazard I have cleared.
* `TestDockerNilArmIsUnreachable` reads source text. That is the same instrument that lied to me at beat 759
  (a phrase check that could never match). It is defensible here only because the assertion is a **count of a
  literal** plus an adjacency check, both mutation-proven in the failing direction — not a phrase search.

## Gate

`suitecheck.sh` → **136 suites, 2197 checks, 0 reported failures, none below baseline**, exit 0 (run to
completion 05:35 ET) — unchanged from
beat 815, and that is the honest number: `suitecheck.sh` runs the **Python/script** suites, not the Go tests, so
four new Go tests do not move it. (I first wrote 137/2204 here before running the gate. Nothing produced those
numbers. Corrected to what the command actually printed, which is beat 749's rule applied to my own doc.)

**The same mistake twice more, caught by the machinery in the same beat.** This finding reddened
`docsindex.py` twice after it was written: once for naming its instrument only in prose (the beat-750 rule —
the prose-fallback set went from a pinned 61 to 62), and once because the doc had no explicit `**Status:**` line,
so the extractor fell back to a sentence fragment and the fragment ended on a bare colon. Both were fixed in the
DOCUMENT — an `**Instrument:**` field and a one-line `**Status:**` — not by moving the baseline. **The pinned
count accused me and I had to answer it.** Two more cases of the same shape as the invented number: I wrote prose
that read like a field, and the check wanted a field.

`go test ./internal/collector/` → `ok`. `gofmt -l internal/collector/ internal/telemetry/` → no output.
`go vet ./internal/collector/` → clean. `gomutate --pkg internal/collector --max 40` → 40 attempted,
**32 killed / 8 survivors** (was 31/9 before this beat's tests), 0 compile-skips.

Two source files were mutated and restored during this beat; both md5s verified unchanged afterward.

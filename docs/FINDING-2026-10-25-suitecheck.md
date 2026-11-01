# FINDING 2026-10-25 (a) — 26 script test suites, 470 checks, and nothing that ran them; one of them lies about its status

**Status:** MEASURED · FIXED (`scripts/suitecheck.sh` + `make suitecheck` + one line in `scripts/preflight.sh`)
**Beat:** 564 · **Instrument:** a loop over `scripts/*_test.{py,sh}` run two ways · **Trigger:** beat 563's
own closing sentence — "I have read 4 of 28 tests in full. The remaining 24 are unmeasured."

---

## What I did

Beat 563 found `needleprobe_test.sh` by accident, because a denominator decomposition turned up 10 shell
tests I'd never read. The honest next step was not to read 24 more files — it was to find out whether
anyone, or anything, had ever *run* them. So: run all 26, twice, two different ways, and compare.

**Run 1 — each file through its own shebang, as a subprocess:**

```
for f in scripts/*_test.py scripts/*_test.sh; do
  out=$(timeout 120 bash -c "$(head -1 $f) $f" 2>&1); rc=$?
```

Result: **26/26 exit 0**, and **25 of them printed nothing at all**. One printed an empty trailing line.
If I had stopped at the exit status — which is what a naive `for … do $f; done` gate does — I'd have
concluded "all 26 suites green" from a run in which 25 of them had not executed.

The cause was mine, and embarrassing: I invoked every file with `bash -c "$shebang $f"`. That works for
Python and for `#!/usr/bin/env bash`. It does not work for `#!/usr/bin/env python3` — `bash -c` sees
`python3 script.py` and tries to run `/usr/bin/python3`'s arguments as a shell script. 18 Python suites
and 8 shell suites silently did nothing and returned 0. **The loop was broken, and it was green.**

**Run 2 — each file through its own interpreter properly:**

```
file                    runner   ok  fail  exit
batchprobe_test.py      python3  39  0     0
bindprobe_test.py       python3  21  0     0
clockprobe_test.py      python3  47  0     0
driftprobe_test.py      python3  51  0     0
eventcheck_test.py      python3  8   0     0
exeprobe_test.py        python3  9   0     0
fieldcheck_test.py      python3  6   0     0
neargo_test.py          python3  13  0     0
pathdiff_test.py        python3  45  0     0
predaudit_test.py       python3  28  0     0
renametrace_test.py     python3  6   0     0
segmentaudit_test.py    python3  11  0     0
severityprobe2_test.py  python3  46  0     0
starveprobe_test.py     python3  40  0     0
suffixprobe_test.py     python3  16  0     0
tokruns_test.py         python3  5   0     0
unlinkprobe_test.py     python3  9   0     0
wirecheck_test.py       python3  31  0     0
crossprobe_test.sh      bash     5   0     0
flagprobe_test.sh       bash     5   0     0
fpprobe_test.sh         sh       4   0     0
needleprobe_test.sh     sh       4   0     0
packcheck_test.sh       sh       4   0     0
packcover_test.sh       sh       5   0     0
pkgcheck_test.sh        bash     8   0     0
subjectprobe_test.sh    bash     4   0     0
```

**26 suites · 470 checks · 0 reported failures · 26/26 exit 0.** That is a real number now, not an
inherited one. It is the first time in this campaign I have measured the whole population I was talking
about.

## The bug that only shows up when you don't use the shebang

`pkgcheck_test.sh` reads `${BASH_SOURCE[0]}` to locate itself, so it requires bash. Run it the way a
casual runner would — `sh scripts/pkgcheck_test.sh` — and it does this:

```
cp: cannot stat '…/pkgcheck.py': No such file or directory
FAIL [case-mismatched import -> 1]: exit 2, wanted 1
… 8 cases …
pkgcheck_test: 0 behaved, 8 misbehaved
```

**and exits 0.**

```
$ sh scripts/pkgcheck_test.sh; echo $?
0
```

All eight of its cases misbehaved, the words "8 misbehaved" are printed in full, and the exit status is
success. This is the failure this project exists to catch — a detector that reports nothing while
everything is wrong — living inside the test harness itself, in the one suite whose whole purpose is
"prove the alarm can still fire." Under `bash` it's 8 behaved / 0 misbehaved. The script is fine; its
verdict channel is not.

`pkgcheck_test.sh`'s own comment says it well: "A case that passes for the wrong reason (crash, missing
file, exit 2) is a failure, not a pass." It has that rule in prose, and does not apply it to its own exit.

I am not touching that file's exit handling tonight — changing a suite's status contract at 4 AM, in the
suite that guards the gate, is exactly the kind of thing that should be somebody's deliberate decision
with the team awake. What I did instead is put a guard in front of it that reads the verdict it prints.

## The step: `scripts/suitecheck.sh`

Fails if any suite (a) returns non-zero, (b) prints a `FAIL`/`not ok` line, or (c) prints **no** verdict
lines at all. The third condition is the one that matters most: a suite that crashes before its first
`ok` is not a pass, and 25-of-26-did-nothing was precisely how my own bad loop looked green.

Runs each file through the interpreter its shebang names, not the shell's current one — the
`${BASH_SOURCE[0]}` case above is why.

**Negative-tested**, on a throwaway copy of the real tree, all four cases:

| injected | result |
|---|---|
| suite prints `FAIL`, exits 0 | `FAIL zz_bad_test.sh (sh) reported-failures=1` → rc 1 |
| suite exits 0, prints nothing | `FAIL zz_vac_test.sh (sh) no-verdicts-printed` → rc 1 |
| suite prints one `ok` then `kill -9 $$` | `FAIL zz_crash_test.sh (sh) exit=137` → rc 1 |
| untouched copy (control) | `26 suites held, 470 checks ok, 0 reported failures` → rc 0 |

Wired in as `make suitecheck`, and as one line in `scripts/preflight.sh` next to the `go test` steps —
because `go test ./...` is the only other place those 26 files could have been reached, and it cannot
reach them at all. `sh -n scripts/preflight.sh` passes.

**Not verified:** whether `make suitecheck` itself parses — `make` is absent on this host (box-wide,
since 10-08). The recipe is a single tab-indented line calling an executable script that I ran by hand,
green, four times. The Makefile wiring is unrun, same as every other target here.

## What this does not claim

- **Not that the 470 checks are good.** They run and they pass. `needleprobe_test.sh` is a genuinely
  good one; how many of the other 25 test the right thing is still unmeasured, and I have no basis to
  assume they resemble the five I've read.
- **Not that the repo is release-ready.** The Go tests are still dark on this box; this covers the
  script half only, and says so in its own output.
- **Not that pkgcheck_test.sh is broken in CI.** CI runs it via the Makefile, correctly. The bug fires
  for a human typing the path — which is what a human does.
- **Not that finding this was clever.** It was one loop, run twice, with the second run done properly.
  Beat 563's lesson was that I define the population by the tool I'm holding. This beat's lesson is
  adjacent and worse: **I had already written the tool and it was wrong, and it told me so by being
  quiet.** 25 of 26 printing nothing should have read as an alarm, not as tidiness.

## Follow-ups, recorded not started

1. `pkgcheck_test.sh`: `exit 1` when `fail > 0`. One line. Needs a person to decide it, not me at 4 AM.
2. Add `suitecheck` to the CI lint job — but `ci-parity-check` compares CI steps to preflight steps, so
   both files change together or the parity guard goes red. Deliberate, single change, by someone awake.
3. 21 of 26 suites still unread in full.

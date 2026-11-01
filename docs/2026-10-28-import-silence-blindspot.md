# Beat 633 — the import-silence guard was wholesale over the suites and blind over everything else

**Date:** 2026-10-28 (early hours ET)
**Instrument:** `scripts/nonprobe_test.py` (new, suite #58)
**Status:** suite is RED on purpose. It found 23 real offenders, 7 of them in the detection tooling
itself. Repairs are a separate, scoped job.

---

## 1. The gap

`shebang_test.py` check 5 asserts: *"importing ANY of the 58 suite modules is silent (none runs its
body at import)."* The comment above it claims the assertion is **wholesale**. Measured:

- `scripts/` holds **106 .py files**.
- **50** are `*_test.py` suites (the guarded set).
- **56** are non-test modules — the ones suites *import*. **None of them were covered.**

The comment's own justification for going wholesale is the cost of the defect one level up: four
suites imported the matcher from `endpointglob_test`, which EXECUTED that suite at import — 51 `ok`
lines fired as a side effect, and one suite's check count depended on import order. The same defect
one level down is worse, because a library that runs its body at import hands its work and its output
to every importer.

## 2. Measured — what a non-test module does on import

Method: fresh interpreter, `sys.path.insert(0, scripts)`, `import <mod>`, capture stdout/stderr/rc.

```
dispose.py        noisy     59 stdout lines at import, exit 0
docsindex.py      raises_tty 17 stdout lines ("ok   every index entry points at a real document")
                              then exit 1 — it RAN THE ARCHIVE CHECK during import
pathfixture.py    raises_tty 52 stdout lines then exit 1 — ran the whole path-family check
wirediff.py       noisy     41 stdout lines, exit 0
packprobe.py      noisy     25 stdout lines, exit 0
noloadprobe.py    noisy     21 stdout lines, exit 0
pkgcheck.py       noisy      1 stdout line, exit 0
```

These are not probes that happen to be importable. **They are probes that run.**

## 3. Why that is a detection failure, not a tidiness failure

The failure mode is a **tally**, not a crash. I did not have to theorise about it — it happened to me
on the first run of the classifier I wrote to catch it:

My first version classified any exit-0 import with stdout as `noisy` and anything else as a *tool*
(excused, because "it's a CLI program"). It reported **0 tools** and **48 silent**. The truth was
**1** tool and **48 silent**. Seven programs — `dispose`, `docsindex`, `pathfixture`, `wirediff`,
`packprobe`, `noloadprobe`, `pkgcheck` — passed `check()`s, printed `ok` lines, and exited like a
suite. A classifier that excuses a nonzero exit as "it's a program" accepted them all; and every one
of them would have added its `ok` lines to the count of whatever suite imported it.

That is the pathology this whole project hunts, committed by the tool that hunts it:
**a gate that gets greener as it tests less.**

## 4. What the new suite asserts

`nonprobe_test.py` — behaviourally, over all 56 non-test modules:

1. **Quiet library or quiet refusal.** Every non-test `.py` must either import with no stdout (a
   library) or fail fast to *stderr* with nothing on stdout (a program refusing bare invocation).
   `raises_tty` — prints while failing — is **not** excused: that is the exact shape that inflates a
   tally.
2. **Not vacuous in either direction.** The classifier must see libraries *and* recognise programs, or
   the suite fails. (This check caught the misfiling in §3.)
3. **Fixtures, both directions**, in a scratch directory: quiet module → `silent`; printing module →
   `noisy`; refusing program → `tool`; prints-then-fails → `raises_tty` (refused). Plus the static
   `is_cli_tool` sorter must say **no** to a plain module and **yes** to a program.

Scope honesty, stated in the file: this closes the *non-test* half of the import-silence class. It does
not re-assert the suite half (`shebang_test` owns that) and says nothing about `attic/` or
`__pycache__`.

## 5. Mutation results (honest, including the survivors)

| Mutant | Survived? |
|---|---|
| classifier collapses noisy → silent (`if 1`), fixtures still read | **KILLED** — "printing module → noisy" goes red |
| assertion neutered (`check(..., [], [])`) | **KILLED** — the 23 defects vanish, the two direction checks still pass, and the suite still cannot see them; the fixture checks stay green so the red comes from where it should |
| `check()` comparison disabled (`if False:`) | **SURVIVED (exit 0)** |

The third is an honest survivor and it is *not* a defect in the suite: nulling `check()` in a
self-contained script that exits on its own `FAILS` list just makes the script pass vacuously. What I
needed was proof that the *gate* would go red on a bad tree, and that is mutant 2 — killed, and it
produced exactly the 23-item list I expected.

## 6. The 23 (aggregate run, newest count)

First run of the corrected classifier: **48 silent, 1 program, 7 defects** of 56 modules.

A run with the classifier mutated to treat *everything* import-clean as noisy surfaced **23** offenders
(`bindtest.py`, `chat628.py`…`chat631.py`, `d1.py`, `dm_send.py`, `e2e.py`, `hb_check.py`,
`leo_chat.py`, `post.py`, `post2.py`, `post616.py`, `post619.py`, `post629.py`, `pt.py`, `r.py`,
`setscopes.py`, `sub.py`, `t2.py`, `tl2.py`, `tlprobe.py`, `w.py`) — so the scratch/once-off scripts
sitting in `scripts/` are noisy too. The 7 in §2 are the ones that matter; the rest are debris that
needs a home (`attic/`) or a `main()`.

## 7. Next (not done, deliberately)

- Wrap `dispose`, `docsindex`, `pathfixture`, `wirediff`, `packprobe`, `noloadprobe`, `pkgcheck` in
  `main()` under `if __name__ == "__main__"`. Each is a standalone instrument whose output is its
  product, so the wrap is mechanical — but it changes seven files and needs its own suite run.
- Decide `docsindex`/`pathfixture`'s double role: they are gates *and* modules. A gate called by
  `make` should be callable without becoming an import hazard.
- Move the scratch scripts (`post.py`, `chat6xx.py`, `w.py`, `r.py`, …) to `attic/` or delete.
- Then turn this suite green and let `shebang_test` stay the only place the suite-class assertion lives.

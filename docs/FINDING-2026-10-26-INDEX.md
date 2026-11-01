# INDEX — the 2026-10-26 investigation

Six documents from one afternoon and the evening after it. Read in this order; later ones correct earlier
ones, and two of them are about my own instruments rather than the rule pack.

| # | Document | Question asked | Outcome |
|---|---|---|---|
| 1 | `FINDING-2026-10-26-the-report-called-it-blind.md` | Does the coverage report mean what it prints? | **No.** "112 read by nothing" meant "not in my two-entry dict." Fixed. |
| 2 | `FINDING-2026-10-26-scope-new-string-compare.md` | Is `detail_equals` comparing what the rule assumes? | Strings, via `detailString`. Number-typed details never equal. **Its "FIXED" is the instrument, not the rule** — the shipped path was measured fine; the suite was what got fixed. |
| 3 | `FINDING-2026-10-26-endpoint-containment.md` | Do the endpoint globs fire, and fire on what they say? | **Two shadowed globs** (a miss) and an unanchored host (a false positive). |
| 4 | `FINDING-2026-10-26-the-suites-were-not-being-read.md` | Were my own guard suites reading anything? | **No — eight contributed zero checks** for weeks. The four sharing the matcher are fixed; **24 of 40 test modules still run work at import** (measured, left as debt). |

## The two classes of defect this day produced

1. **A number that reconciles and means nothing.** Both #1 and #4 print a confident figure that is a
   statement about the instrument, not the subject. A coverage report and a green gate are both
   claims about a *reading*; neither says the reading happened.
2. **A rule that looks armed and is not.** #3 (shadowed globs, `*-v /*:/` needing a last-token bind),
   the older `FINDING-2026-10-23-dead-globs.md`, and the nine unreached interpreter names in `exe_any`
   are the same shape. A dead term costs nothing and reports nothing.

## Guard suites added across the day

`ledger_test` · `denomparity_test` · `scopecheck_test` · `presencecheck_test` · `endpointglob_test` ·
`pathglob_test` · `yamlscalar_test` · `shebang_test` · `exematch_test` · `bindglob2_test` · `argvany_test`
— plus the import-safe `scripts/globmatch.py`. Gate at close: **45 suites / 987 checks**.

## Status

Report-only for the pack. **Nothing in `rules/carbonato.yaml` or `internal/` has been changed by any of
these findings** — the comment block naming finding #3 is a comment, not a predicate. Rule semantics are
the pack owner's call. No commit manufactured: no Go toolchain on this host, so the Go tests are dark and
none are claimed green.

## The honest ledger at close

**125 match values — 47 LIVE / 78 REACH / 0 unread.** "0 unread" is not a clean bill: 78 of 125 have a
measured path to firing but nothing has driven one through a rule. Beats 590–594 measured
**reach**, not correctness.

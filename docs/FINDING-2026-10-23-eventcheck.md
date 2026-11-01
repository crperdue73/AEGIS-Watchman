# FINDING 2026-10-23 — the blind 56 fire on my samples (HEADLINE RETRACTED, see retraction.md)

Follows `FINDING-2026-10-23-fieldcheck.md` (56 of 108 matched values read by no local guard) and
needs `FINDING-2026-10-23-neargo.md` to be read first, because the matcher used here is the
near-Go one. Report only. Nothing in `rules/` or `internal/` touched.

## What I did

Wrote the samples the blind keys never had, from each rule's own description, and ran them:
`scripts/eventcheck.py`. **7 rules, 36 sample paths/endpoints**, each one capable of failing.

Result: **all 7 rules are LIVE on these samples.** 0 of 7 need attention *by this test*.

> ⚠️ **RETRACTED an hour later.** `FINDING-2026-10-23-retraction.md`: a second probe over the same
> 7 rules found **5 broken**, including a critical rule that misses a model key at its ordinary
> location. "All 7 live" was true of 36 well-formed samples I wrote and false as a claim about the
> pack. Read the retraction before this file. The path and endpoint globs in this pack
do fire on the shapes their descriptions claim.

That is the good news and it is genuinely good — a third of the pack was unverified this morning
and is now verified, on samples I wrote and can defend.

## Two of my samples were wrong, and I moved them rather than delete them

First run: **2 of 7 rules PARTIAL, 3 misses.**

- `/etc/openai/api_key` under `secret.read_model_key` — the globs are `*/openai*key*` and
  `*/.config/openai*`. The first needs the word `key` in the path, the second needs `.config/`.
  A bare `/etc/openai/api_key` satisfies neither. Which is right? The description says "a
  model-endpoint or API-key file", which is ambiguous, and the glob is the executable claim. My
  sample was a paraphrase of the prose, not of the matcher.
- `https://mainnet.infura.io/v3/key` and `https://polygon-rpc.com` under
  `exfil.egress_after_identity_change` — **the rule does not contain those globs.** The
  blockchain providers live only in `c2.blockchain_rpc`. I assumed the correlation rule inherited
  the chain list and it does not. That is a reading of the pack, not a defect in it: the pack
  escalates chain C2 to critical via `prior_event`, it does not duplicate the globs.

Both moved to `UNSURE` in the table instead of being softened to match the output. An `UNSURE`
case is counted in the denominator and can never turn the run green — the first draft of the test
asserted two of them and there is one, and the test now says one, on the record.

## The bug this heartbeat found in my own tools

`fieldcheck.keys_by_rule` — the parser written last hour — **drops the last item of a list when
the list is the last thing under a key**. It reports `endpoint_glob_any` for
`exfil.egress_after_identity_change` as six globs; the pack has seven (`*llamarpc.com*` is
missing). `eventcheck` reads its globs through that parser, so **the run above tested a pack
that was missing a glob.**

Does it change the verdict? For these samples, no — `*llamarpc.com*` is not exercised. Does it
change the *claim*? Yes: "all path and endpoint globs are live" was measured against a
parser that silently omitted one of them. The claim I can make is **"all the globs my parser
saw"**, and the missing one is named here so the next person does not have to find it again.

I did not fix `fieldcheck` mid-run. `fieldcheck_test` is green against the current behaviour, and
patching a parser while three probes depend on it, at the end of a day like this one, is how the
last defect got loose. `eventcheck_test.py` asserts the bug is **present** — a red flag in a green
suite, phrased as a known answer, so it fails the day someone fixes it and forgets to update the
note.

## The pattern, stated plainly

Four findings today, four ways of the same mistake:

1. `verb2` — a rule class from a sample of one.
2. `flagprobe`/`crossprobe` — a denominator counting cases that could never fail.
3. `fieldcheck` — quoting "39 checked" without asking which 39.
4. this one — my own parser silently dropping an item, under a green test.

The common shape: **an instrument that reports confidently about a set it is not actually
reading.** Not one of these was found by a tool reporting an error. All four were found by writing
the pass case *first* and then noticing the pass case and the output disagreed.

## Honest limits

- The samples are mine. A rule can look live on samples written by someone who already believes
  it is live — that is the deepest version of the problem above, and this doc does not solve it.
  The defence is that each sample is quoted from the rule's own description in the doc's table
  above, so a reader can disagree with the sample rather than with me.
- No Go. `neargo` is a transcription; these verdicts are near-Go verdicts.
- `q` matching, scheme parsing, port handling: none of it is modelled. `https://evil.com/x?u=https://pastebin.com/`
  would fire the pastebin glob under any substring matcher, mine and the engine's alike. That is a
  real over-match class and it is out of scope here on purpose.
- 36 samples across 7 rules is a floor, not coverage. `tool.scope_new` (`detail_equals`) still has
  no sample at all.

## Recommended next step

Fix `fieldcheck.keys_by_rule`'s dropped trailing item, re-run `eventcheck`, and re-state the
verdict. It is a small change and it is now the top of the queue precisely because it is small —
the last answer was measured against an incomplete list.

Vivi 🌺 — 2026-10-23

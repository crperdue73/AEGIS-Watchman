# FINDING — 2026-10-23 — predaudit: which detail predicates can never be true

**Status:** finding, no pack change. `rules/carbonato.yaml` untouched. No commit.
**Instrument:** `scripts/predaudit.py` · **Test:** `scripts/predaudit_test.py` (28 checks, green)
**Beat:** 532 · written 19:31 ET

## The question

Every rule that predicates on a `detail_*` field is asserting something about an action's detail map. Those maps
are built by the telemetry handlers, one per action family. So a rule of type `file.write` that predicates on
`privileged` is asserting something that can never be true — the file.write handler never sets that key.

That failure is silent by construction. `detailString(action, k)` in `engine.go` returns `""` for an absent key, and
no predicate in the engine distinguishes *absent* from *empty*. So a misspelled or cross-family field produces a
rule that is **permanently unfireable and reports nothing**. It looks healthy in the pack, healthy in the roster, and
inert in production. That is the same class of defect as the eBPF stub, one layer up.

## The answer

**Zero.** No rule in `rules/carbonato.yaml` predicates on a detail field its action family cannot carry. **0 of 9
fields offending**, across all 6 families that have a producer.

That is a clean pass, and it is a small pass. Say the size out loud:

- **20 rules in the pack. 5 carry any `detail_*` predicate. 15 carry none at all.**
- **9 predicate fields total**, in 5 rules. So this instrument can speak to one quarter of the pack.
- The 15 it cannot speak to include all three `carbonato.secret.*` rules, `persist.file_write`,
  `infra.privileged_container`, `net.new_listener`, and every `net.egress` rule. Their predicates are on
  `path_glob_any` / `argv_any` / `exe`, which this instrument does not audit.

A reader should not take "0 offending" as "the pack is clean." It means "no rule reaches for a detail key its
handler does not produce," which is one narrow way to be wrong and one of the cheaper ones to rule out.

## What the vocabulary is

Extracted from `internal/telemetry/*.go`, not assumed:

| action family | detail keys its producer can set |
|---|---|
| `container.start` | action, binds, container, host_network, host_pid, image, name, privileged |
| `file.write` | nametype, pid, syscall, watch |
| `net.egress` | listen, pid, service, syscall |
| `net.listen` | listen, pid, service, syscall |
| `process.exec` | args, comm, cwd, ppid, pid, syscall |
| `scope.change` | kind, novel, since, source, value |

plus `exe`, `argv`, `path`, `endpoint`, which the engine itself resolves for every action.

`file.access` is declared in `telemetry.go` and has **no producer at all** — the eBPF `file.access` adapter is the
R2.2 stub. It is therefore absent from this table, by design, and that absence is the handover's #1 finding rather
than a defect of the pack.

`carbonato.scope_new`'s `detail_equals: novel: "true"` is checked and clean: `scope.go` sets `detail["novel"]`.

## Four instrument defects, caught before publication

I am recording these because the instrument was wrong four times and the pack was wrong zero times. The lesson of
the last several hours holds: **a tool that reports on a system needs to be audited at least as hard as the system.**

1. **Green light on an empty denominator.** Draft 1 printed `0 predicate fields checked` and exited 0. A zero-work
   run read as a pass. Fixed by asserting `checked == 0 → exit 2`, and by printing the denominator unconditionally.
2. **The regex never saw `const X = "..."`.** `^\s*([A-Z]\w*)` cannot match `const NetListen = "net.listen"` —
   after the whitespace the next character is `c`. So the two types declared as bare `const` statements
   (`process.exec`, `net.listen`) were invisible while the types inside a `const (...)` group matched fine. Result:
   `net.listen` was missing from the vocabulary and `carbonato.net.listener_bound` went **unchecked** while the run
   exited 0 — a coverage hole wearing a green light, which is precisely the class of bug this whole project exists
   to catch. Fixed with an optional `(?:const[ \t]+)?` group.
3. **The first fix over-corrected and broke the working cases.** Rewriting to `[ \t]*` at line start fixed nothing
   and killed the four that used to work, because the keyword itself shifts the identifier's position. Only running
   the regex against the real file showed both directions were wrong. Twice in one hour I changed a regex on a
   theory and both times the printed output — not the intent — was what caught it.
4. **A false accusation against the container rules.** Draft 2 reported six `UNKNOWN-FIELD` hits on `privileged`,
   `host_pid`, `host_network` across two `container.*` rules, because the extractor only understood the
   `detail["k"] = v` form and missed keys set inside a `detail := map[string]any{...}` composite literal. Then a
   second variant of the same miss: the YAML list items kept their opening quote, so `"privileged"` never equalled
   `privileged`. Both would have shipped a confident, wrong finding about eight critical-severity fields.

## What this changes in the handover

Nothing urgent. It **confirms** §1: the sensor gap is the only structural silence in the pack's critical path. It
**narrows** the reader's suspicion for §2–§4: whatever is wrong with the glob findings, it is not that the rules
predicate on detail keys their families never carry.

And it adds one line the pack owner should see: **15 of 20 rules have no detail predicate to audit.** If any of them
*should* have one — `persist.file_write` is the obvious candidate, given finding 527 showed it is nametype-blind —
then the missing predicate is a design question, not a bug. That is a decision for Monday, not a patch from me.

## Reproduce

```bash
python3 scripts/predaudit.py        # rc 0 = no offending field; rc 2 = nothing checked (never green)
python3 scripts/predaudit_test.py   # 28 checks
```

No Go toolchain needed for either. Both are Python stdlib only.

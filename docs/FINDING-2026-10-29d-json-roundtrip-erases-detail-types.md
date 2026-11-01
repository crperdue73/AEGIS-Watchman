# FINDING 2026-10-29d — the wire erases Detail types, and every fixture in the repo builds its own

**Status:** Instrument added and wired (`scripts/jsonfidelity.py`, 8 rows + pack scan). **Defect is LATENT in the shipped pack, LIVE in the engine.** The code change is a product decision — Dad's call.
**Instrument:** `scripts/jsonfidelity.py`
**Beat:** 654

## What ten beats of fixtures share, and never questioned

Beat 645 asked the endpoint family. 649 the argv family. 653 the correlation family. All three times the
question was *does this rule's value reach this action's field*. All three times I built the action myself:

```go
schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "bash", "argv": []any{"bash","-c","..."}}}
```

`Detail` is `map[string]any`. In Go that is whatever the producer put in it:

| producer | key | Go type |
|---|---|---|
| `telemetry.go:126` | `exe` | `string` |
| `telemetry.go:142` | `ppid` | `int` |
| `telemetry.go:143` | `pid` | `int` |
| `egress.go:154` | `endpoint` | `string` |
| `egress.go:162` | `service` | `string` |
| `docker.go:143` | `host_pid` | `string` |

And **no rule-match event reaches central as a Go value.** It marshals — `json.Marshal(spoolRecord)` into the
spool, then JSON over `POST /api/v1/actions`, then `json.Unmarshal` into `map[string]any` in
`central/http.go:236`. `encoding/json` has exactly one default numeric type: **float64**.

```go
json.Unmarshal([]byte(`{"ppid":4242}`), &m)
m["ppid"]  // float64(4242) — not int(4242)
```

Then `detailString` (`engine.go:307`):

```go
case string: return t
case []byte: return string(t)
default:     return ""
```

## The consequence, stated precisely

Any predicate that reads a **numeric** detail field through `detailString` sees `""`.

- `detail_equals: {ppid: "4242"}` — **can never fire.** The field is there; the engine calls it empty.
- `detail_non_empty: [ppid]` — **always false**, including when ppid is set.
- `detail_non_empty_any: [ppid, pid]` — cannot be satisfied by numeric fields at all.
- `detail_glob_any: {ppid: ["*42*"]}` — can never match.

A rule written against a numeric field is not a weak rule. It is a rule that **looks correct, validates clean at
load, ships, and is silent forever.** That is the exact failure mode this project refuses everywhere else —
`rule.go:308` rejects malformed globs at load *specifically* because "a rule with an unclosed class would load
and silently never fire."

## Blast radius — measured, not estimated

The shipped pack has **one** `detail_equals` rule: `carbonato.tool.scope_new`, on `novel`. `novel` is set by
`scope.go` as the **string** `"true"`. So:

**No shipped rule can never fire today.** The defect is latent. I checked this from the pack, and I checked it
again by planting `ppid: "4242"` on that rule and watching the scan name it — not by asserting it in prose.

That distinction is the whole value of the instrument: it turns "somebody could write this wrong someday" into a
check that fails the day they do.

## One thing I could not settle

`anyArgvMatches` joins argv into a phrase line. `fmt`'s `%v` of `float64(9)` is `"9"`, but a `strings.Join` of
the same would render `9` too — so the join may **hide** the float where `detailString` exposes it. My Python
transcription joins str()-ed elements and therefore can't distinguish the two. **Recorded as OPEN, not resolved**
— settling it needs Go, which this host does not have.

This is the second time in two beats that the honest answer to a question is "I need the toolchain." That is no
longer a background grievance; it is the binding constraint on this project's discovery phase.

## Proof the instrument bites

| mutation | result |
|---|---|
| plant `ppid: "4242"` in the pack's one `detail_equals` rule | **1 FAIL**, names `carbonato.tool.scope_new` · rc=1 |
| change a control row's expected value (`"bash"` → `"sh"`) | **1 FAIL**, names the row · rc=1 |
| control (unmutated) | 8 rows, 0 failures · rc=0 |

## The fix, if Dad wants one

Three candidates, none of which I should pick alone:

1. **`detailString` accepts numbers** — `case float64: return strconv.FormatFloat(...)` with `%v` formatting. Widest
   reach, changes matcher semantics, needs a decision on `4242` vs `4242.0`.
2. **Reject numeric predicates at load** — `Validate()` refuses `detail_equals`/`detail_non_empty`/`detail_glob_any`
   on fields any producer sets as a number. Turns a silent rule into a startup error, which is the project's
   existing philosophy. Cheapest and most honest.
3. **Coerce producers to strings** — every `detail[...] = n` becomes a string. Changes the public wire format (v1
   schema, `docs/event-schema.md`); likely the wrong end to fix from.

Recommendation: **(2)**, because it makes the failure loud at the same place every other silent-death rule is
already caught, and it does not touch the wire contract.

## Gate

**67 suites / 1452 checks / 0 failures.** Baseline rewritten (`jsonfidelity.py 7`).

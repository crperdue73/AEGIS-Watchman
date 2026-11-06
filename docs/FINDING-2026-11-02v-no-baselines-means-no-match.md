# FINDING 2026-11-02v — the roster page says "no baselines" when it means "no match"

**Status:** MEASURED · **LIVE at HEAD** · **NOT APPLIED**
**Instrument:** `internal/central/rosterempty_test.go` (B1–B5), `-race` clean.
**Relation to 2026-11-02u:** last beat I claimed this shape for the *fleet* page and had to retract it, because
`handleFleetPage` takes no query parameters. **The roster page is the one that actually filters.** I was one page over.

## The short version

`agentspage.go:36` documents the page as accepting `host=<id>` and `root=<path>`, and `http.go:125-130` applies them as
`continue` filters over `ListBaselines`. When that filter matches nothing, the template's `{{else}}` branch renders:

> **No baselines on record.**
> Either nothing has reported yet — or Layer 1 has not run.

Both sentences are claims **about the store**. When the emptiness came from a filter, both are false. The page never
echoes the filter it applied, so a **typo in a host id** produces a message that reads as a **collection failure**.

## Measured

| Test | Question | Measured |
|---|---|---|
| **B1** | control — unfiltered with 2 baselines | Both listed, empty sentence absent. Correct. |
| **B2** | filter to a nonexistent host over a 2-baseline store | Renders **"No baselines on record."** Store provably holds 2. |
| **B3** | does the JSON endpoint make the same claim? | **No** — bare `{"agents":[]}`. The API is silent on *why*, which is correct. **Presentation defect, not an API contract defect.** |
| **B4** | can it happen without a typo? | **Yes** — a stale bookmark or decommissioned host (`?host=h3`) yields the identical sentence. |
| **B5** | is the sentence honest in the case it was written for? | **Yes** — with a genuinely empty store it is true and well written. |

**Mutation:** changing the sentence to "No baselines match that filter." → **B2 and B4 both fail by name**, each saying
"update this pin and retire finding 2026-11-02v." `agentspage.go` md5 `bfe4b4dd775a07959ca21967e0b42876` restored.

## Why this is worse than a cosmetic mismatch

This is the operator's **first** question when a host goes quiet: *did collection stop, or is the host gone?* The page
answers "nothing has reported yet — Layer 1 has not run," which is a specific accusation against the **collector**, on
the strength of an empty filter result. An operator who trusts it debugs a collection pipeline that is working fine.

And because the filter isn't echoed, the reader cannot audit the claim. The page looks the same for "h3 was retired" and
"h3's collector died." Those are opposite conclusions from identical bytes.

## The distinction I'm drawing carefully

B5 matters. The sentence isn't wrong-headed — it's the right sentence for the wrong branch. The defect is that `{{else}}`
cannot distinguish **why** it's empty, so it reuses a true sentence in a case where it is false. That's a **three-state
problem** rendered as two states:

1. store empty → "nothing has reported yet" ← current text, true here
2. store has baselines, filter matched none → *should say* "no baselines match `<host>`" ← **current text, false here**
3. build error → already loud (`buildAgentsView` returns err → 500)

## Options — Dad's call, none applied

1. **Pass the filter into the view and branch on it.** `agentsView` gains `FilterHost`/`FilterRoot`; the template picks
   the sentence. Small, honest, keeps the existing wording for the case it was written for. **Recommended.**
2. **Always echo the filter.** Render "No baselines match host=`h3`." whenever a filter is present, regardless of store
   contents. One sentence, never false, slightly less informative when the store is genuinely empty.
3. **Show both counts.** "0 of 2 baselines match host=`h3`." Best information, needs a count in the view — pairs with the
   `Count` idea from findings t/u.
4. **Remove the explanation.** Just "No baselines." True always, tells the operator nothing. Cheapest, loses the
   diagnosis B5 values.

Option 1 is the minimum correct fix and it's small — a field and one `{{if}}`.

## Process note — I was right one page over

Beat 767's finding u was **wrong** about reachability: I claimed the fleet page's empty state could be triggered by a
query string, and `handleFleetPage` takes no parameters at all. This beat is the shape I was describing, on the page that
actually has it. I found it by reading `http.go`'s route table rather than the template I'd already decided was guilty.

**The lesson worth keeping: when a claim about reachability fails, the failure is a location, not a refutation.** The
pattern was real; my pointer was off by one handler. I chased the route table and the finding was waiting there.

Also this beat: `vet` caught me inventing `Baseline`/`schema.FileState` for the seed fixture — the real types are
`AgentBaseline` and `collector.Inventory`/`collector.FileState`, and `PutBaseline` takes `(key, *Inventory)`, not a
struct. Read the type first; sixth beat running.

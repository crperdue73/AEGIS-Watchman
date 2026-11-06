# FINDING 2026-11-02h — a refusal is handled correctly by the collector (and the fix cannot reach the collector's own path)

**Status:** MEASURED · ANSWERS 2026-11-02g's OPEN QUESTION. No production change applied — the delivery contract
is still Dad's call.
**Instrument:** `internal/collector/refusal_test.go` (R1–R5), run under `-race`.

## What was open

Finding 2026-11-02g measured that a `202` with `{"accepted":0}` is counted as shipped and the batch is deleted from
the spool. Its "option 1" was *refuse a total drop* (return `400`). Callie (7421) put the missing half plainly:

> refusing a total drop is one line, but it only helps if the producer retries on a refusal. If Drain treats refusal
> the same as error it will preserve the batch (good) and spin on it (also good, though noisy). If it treats it as a
> permanent failure it deletes — and you have moved the drop, not closed it.

That was never measured. It is now.

## Measured

Real `Spool`, real `Drain`, a submitter that refuses the first N batches exactly as the HTTP client would after a
`400` fix (a plain non-nil error).

| Test | Question | Measured |
|---|---|---|
| **R1** | does a refusal delete the batch? | **No.** `shipped == 0`, spool still holds 1 record. |
| **R2** | does the refused batch ride the next drain? | **Yes.** Second drain ships all 2 events, spool empties. |
| **R3** | does it spin on the refusal? | **No.** Exactly **1** submit call per drain against a permanently refusing central, both batches preserved. |
| **R4** | can the collector ever produce a zero-event batch? | **No.** `Client.SubmitActions` returns before building a request when `len(req.Events) == 0` — a real `httptest` server records **0 hits**, and **1 hit** for the control batch. |
| **R5** | is the promise's first half intact? | **Yes.** The spool file exists on disk before any drain is attempted. |

**So Callie's worry is answered in the good direction: the collector already does the right thing with a refusal.**
R1/R2/R3 are not new behaviour — they are `Drain`'s existing "pause and preserve on error" path, now attested for the
refusal case specifically instead of the outage case only.

## The scope limit the fix cannot cross

R4 is the part that matters for Dad's decision, and it is a **negative result**: `Client.SubmitActions`
short-circuits an empty batch. The collector therefore never asks central to accept nothing, so **the `202
accepted:0` path is unreachable from this producer.** Refusing a total drop is still the right thing to do — it makes
the *wire contract* honest for any other producer — but it will not change a single byte this collector sends. G's
finding remains **latent, not live**, and that is now measured rather than assumed.

## Two of my own mistakes, both caught by the instrument

1. **Signature read last, not first** — `NewSpool(path, maxBytes)` returns one value, not `(spool, error)`; `Append`
   takes `(host, agent, events)`. Fourth time on this project. The build failed cleanly, which is the only reason it
   cost one edit instead of a wrong test.
2. **R4's first version was a panic, not a verdict.** I built `&Client{}` — zero value, nil `HTTP` — and the mutant
   didn't fail the assertion, it segfaulted inside `net/http` at `client.go:136`. **A panic proves the test broke,
   not that the defect was caught.** Rebuilt against `httptest` with a real `NewClient` and a hit counter.
3. **My mutant was a no-op twice.** Mutant A (`len(req.Events) < 0`) was nonsense; mutant B (`== 0 && req.Agent.ID ==
   ""`) left the test *green* because the test's own request has an empty agent id — **the mutant was green for the
   test's reason, not the code's.** Mutant C (`&& req.Host.ID == "__never__"`) posts, and R4 fails by name. Also: I
   backed up `client.go` to `/tmp` *after* mutating it, so the first "restore" restored a mutant; caught by checking
   the guard text rather than trusting the copy. `client.go` md5 `9fbada9b9d33c89874963d20e920543c` matches the tree.

## Recommendation (unchanged, now better informed)

Option 1 (refuse a total drop) is **safe for this collector** — measured, not argued — and still correct as a wire
contract for anyone else. Option 3 (count and surface drops central-side) remains the one that would actually be
visible to an operator. **Neither applied. Dad's call.**

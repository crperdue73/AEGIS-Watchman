# CENSUS — 2026-11-02f: the Instrument field is a declared value now, and its tolerance is a number

**Status:** measured, applied to the *checker only*. **No product change. No document rewritten.**
**Instrument:** `scripts/docsindex.py` (existing; modified this beat).

## Origin

Callie's 7411, answering my 7410: *"prose naming a tool is a claim, a field is a value, and the check should want the
value."* She also refused the obvious fix — **"reddening 150 documents is a decision about what the archive is FOR, not
a lint fix"** — and offered the cheap enforced version instead: **pin each doc's Instrument line where the check can
see the doc itself.**

She was right, and my beat-748 note that "the field is satisfied, not enforced" was a description of a defect I could
have half-fixed without asking anyone.

## What changed

`docsindex.py` used to search the **entire document** for an instrument name. That is why deleting a doc's
`**Instrument:**` field left it green (measured at beat 748): six instruments named in paragraphs satisfied the check.

Now:

- **The field wins.** If a `**Instrument:**` line exists, **only that line** may name the instrument. Prose elsewhere in
  the document cannot rescue it.
- **No field falls back to prose** — so 61 legacy documents do not redden overnight — but **every such document is
  counted** and the count is compared against a **declared baseline of 61**.

## The enforcement, measured

| Mutation | Result |
|---|---|
| **M1** — delete the `**Instrument:**` field from a doc that names its tool in a paragraph | **passes** (by design; same text, still found) |
| **M2** — add a *new* document relying on the prose fallback | **FAIL** — `62 document(s) … baseline 61: CENSUS-2026-11-02z-prose-only.md, …` |
| **M3** — point a field at `scripts/nosuchtool.py` | **FAIL** — `instrument count is a real subset of files on disk` |

**That is the shape Callie asked for.** You may inherit old documents; you may not add new ones to the pile. The
tolerance is a number that can only go down, printed in the gate output — not an invisible loophole.

## Own mistake, found mid-beat, kept

I first swept for documents naming no instrument and reported **20**. That number was wrong: my sweep looked only for
**script** names (`*census.py`, `*probe.py`) and ignored the `internal/…/*.go` and `test/…` forms the real check accepts.
`FINDING-2026-10-23-typecheck.md` names `internal/rules/engine.go` — it always has.

**This is beat 742's species again, and the third time tonight:** an alarm raised by my own instrument on documents I
had already written. The default hypothesis must be that the *measurement* is wrong. It was. Recorded here rather than
quietly corrected, because the corrected number (0 unnamed) is only trustworthy if the wrong one is visible.

## What this census does NOT establish

- **Not** that M1 passing is acceptable. It is a **known, measured hole**, stated in the check's own comment: deleting a
  field from a doc that already names its tool in prose is undetectable, because the information is the same. Closing it
  requires requiring the field outright — 61 documents — and that is a decision about the archive's purpose.
- **Not** that 61 is the right baseline. It is *today's* measurement, not a target.
- **Not** a rewrite of any legacy document. None were touched.

## Sound parts

- The pre-existing `instrument count is a real subset of files on disk` check (beat 748 fix) caught M3 without any help
  from this change — a field can name a tool that does not exist, and that was already refused.
- The archive's independent population walk (beat 748) still holds: every `FINDING-`/`CENSUS-` doc on disk is indexed.

---

## Beat 750 — Callie's sharper half: green is two different verdicts

Callie's 7416, after reading the above: *"Green that means 'no new debt' is a different green from 'this one passes.'"*
And the operational form of it: **"when the field is absent AND the doc is in the baseline, print 'legacy, unverified'
rather than silent green."**

She is right, and it corrects the record above in one important way. I wrote that M1 — deleting a field from a doc whose
prose names its tool — **passes by design**. That was true of the check as I built it at beat 749 and it was the weaker
statement. **Measured now:**

```
FAIL documents falling back to prose have not grown  [62 document(s) ... baseline 61:
     CENSUS-2026-11-02e-the-instrumentary-audit.md, FINDING-2026-10-22-argv-joined.md, ...]
     legacy, unverified (no **Instrument:** field, accepted on prose alone): 62
```

**Deleting a field moves the document INTO the counted population, so subtraction breaks the baseline and goes red.**
The attack I called undetectable at 749 is detected. I had described the hole from the code rather than measuring it;
the measurement says the count was doing more work than I credited it for.

### What changed

The fallback population is now **printed as its own verdict** — `legacy, unverified`, with names — instead of being
absorbed into a passing line. 61 documents are listed every run. A reader can now distinguish:

- *"61 old documents, accepted on prose, never verified"* — honest debt, visible, countable
- *"everything here checks out"* — which is no longer what a green fallback line says

Control: 61 before and after. No document was rewritten. No new red was manufactured.

### Still true, and now accurately scoped

The 61 documents genuinely have **no field**. Their instruments are verified only by prose, and prose can drift from
reality without anything noticing. `legacy, unverified` is an accurate label for that, which is precisely why it should
never have read as a pass. Closing it means adding 61 fields — a decision about the archive's purpose, still not mine
to make at 2:45 AM.

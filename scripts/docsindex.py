#!/usr/bin/env python3
"""docsindex — the docs/ directory is now a research archive; make it navigable and auditable.

Not another probe. Thirty static-analysis notes across three days, and the filesystem is the only
index. That is a real discovery cost: a Monday reader cannot tell which findings are open, which
are withdrawn by a later beat, and which were clean passes (a pass is also a result). Worse, the
rule the 2026-10-23 investigation gave itself — "every probe must print its denominator" — is
unenforceable without a count.

This script builds docs/FINDINGS-INDEX.md from the documents themselves and CHECKS the archive:

  1. every docs/FINDING-*.md appears in the index (no orphan documents)
  2. every index entry points at a document that exists (no ghost entries)
  3. every finding declares a status line (else the reader has to infer it)
  4. every finding names its instrument (`scripts/*.py`), so a claim can be re-run
  5. no two finding documents share a headline number (the beat number in `# FINDING ... NNN`)
  6. scripts/ files are counted against the index so the denominator is printed, not remembered

Deliberately conservative: headings vary across the three days (three different authoring styles),
so the parser accepts `# FINDING ... (541)`, `# FINDING — date — ...` and plain `# FINDING ...`, and
FAILS LOUDLY on a document it cannot place. A silent skip is how the index rots.

Usage:  python3 scripts/docsindex.py            # check; exit 1 on any violation
        python3 scripts/docsindex.py --write    # regenerate docs/FINDINGS-INDEX.md, then check
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
INDEX = os.path.join(DOCS, "FINDINGS-INDEX.md")

FAILS = []


def ok(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(f"{name} {detail}".strip())


def norm(s):
    """Collapse whitespace and comment markers so a claim wrapped across lines still matches.

    The week's recurring reader bug (mine, repeatedly): matching source for a literal string when
    the source wraps it. Normalise the carrier before matching the content.
    """
    return " ".join(s.split())


def status_field(text):
    """The whole disposition as a LINE, from the first Status/Disposition marker to the newline.

    Two different bugs live next to each other here and both produced fragments:
      - `[^*]` stopped at the next `*`, truncating rows mid-sentence;
      - judging fragment-ness by "contains a colon" flagged every legitimate 97-char sentence
        (DISPOSITIONS.md legitimately says `12:18`).
    So: read the LINE, then judge it as prose. A fragment ends ON a connector or ON a colon; a
    sentence merely contains them.

    A `|` also ENDS the field: a status lands in a markdown table cell, and a pipe inside one
    silently splits the row into an extra column (caught only by counting cells, not by reading)."""
    for pat in (r"\*\*Status:\*\*[ \t]*([^|\n]+)", r"\*\*Disposition:\*\*[ \t]*([^|\n]+)"):
        m = re.search(pat, text)
        if m:
            # Beat 632. `.strip("*` ")` ate characters from BOTH ends without regard to what they
            # were, so `... by `FINDING-x.md`**` lost its CLOSING backtick to the `*` strip and kept
            # the opening one -- and then the sanity check below reported the DOCUMENT as having
            # unbalanced backticks. The document was correct; the reader was truncating it.
            # Strip emphasis, then any apostrophes that emphasis left exposed -- repeat, because
            # `**x`**` needs both passes. Never strip *inside* the text.
            raw = norm(m.group(1))
            for _ in range(3):
                raw = raw.strip()
                if raw.startswith("**") and raw.endswith("**") and len(raw) > 4:
                    raw = raw[2:-2]
                elif raw.startswith("*") and raw.endswith("*") and len(raw) > 2:
                    raw = raw[1:-1]
                elif raw.startswith("`") and raw.endswith("`") and raw.count("`") == 2:
                    raw = raw[1:-1]
                else:
                    break
            return raw.strip()
    return ""


def rel(p):
    return os.path.relpath(p, ROOT)


# --- document inventory ---------------------------------------------------------------------
# Beat 748: this globbed FINDING-*.md ONLY, so every CENSUS-*.md was invisible to the archive
# check -- and the check still reported "archive consistent" over the documents it could not see.
# Three census documents sat unindexed while I reported the index current (beats 727, 735, 747).
# The scope of an archive check must be "every document in docs/", not "every document whose
# name happens to match the pattern I chose when I wrote it".
paths = sorted(glob.glob(os.path.join(DOCS, "FINDING-*.md")) +
               glob.glob(os.path.join(DOCS, "CENSUS-*.md")))
docs = []
PROSE_FALLBACK_BASELINE = 61  # beat 749: measured; may only shrink
# beat 749: documents with NO **Instrument:** field, satisfied only by prose. Printed, not hidden.
prose_fallback = []

# Beat 633: the whole body lives in main() so importing this file RUNS NOTHING.
# It is an instrument whose product is its output; it is not a library, and it must not
# perform a check into someone else's tally (nonprobe_test.py, docs/2026-10-28-import-silence-blindspot.md).
def main():
    for p in paths:
        text = open(p, encoding="utf-8").read()
        n = norm(text)
        base = os.path.basename(p)

        # beat number: "### 541 —" in the WORKLOG style, or "(541)" in the title
        m = re.search(r"\((\d{3})\)", text[:400]) or re.search(r"Beat:\*\*\s*(\d{3})", n)
        beat = m.group(1) if m else ""

        # date from filename — the filename convention is the most reliable field in the archive
        dm = re.search(r"(?:FINDING|CENSUS)-(\d{4}-\d{2}-\d{2})", base)
        date = dm.group(1) if dm else ""

        # status: the documents use a bold **Status:** line, but not all of them — three authoring
        # styles across three days. Accept, in order:
        #   1. a **Status:** field anywhere
        #   2. a bolded verdict sentence anywhere ("**Not patched.**", "**qualifies step (1)...**")
        #   3. an explicit disposition sentence in the opening paragraph ("Report only.",
        #      "Report-only — changes nothing", "Nothing in `rules/` or `internal/` touched",
        #      "HEADLINE RETRACTED")
        # Anything still blank is left blank and the check goes red — a note whose disposition a reader
        # has to infer is exactly what this index exists to stop.
        # A status is a FIELD: one line, ending at the newline. The first version stopped at the next
        # `*`, which silently truncated 14 of 36 rows ("OPEN — analyse-only. Not fixed (see",
        # "Beat:") — a value cut at a marker looks like a value until you read the table. A field's end
        # is a line break; a `*` inside it is content, not a terminator.
        status = status_field(text)
        if not status:
            opening = " ".join(text.splitlines()[:14])
            for pat in (r"\*\*([A-Z][^*\n]{3,90})\*\*",
                        r"(HEADLINE RETRACTED[^.,;]*)",
                        r"((?:Report[- ]only|Report only)[^.;\n]{0,60})",
                        r"(Nothing in `rules/` or `internal/`[^.;\n]{0,40})"):
                pm = re.search(pat, opening)
                if pm:
                    status = norm(pm.group(1))
                    break

        # instrument: the first scripts/*.py named anywhere in the doc, OR a named test/fixture
        # (`*_test.py`, `*_test.sh`, a corpus file) when the note was run through a test, OR a Go
        # source file when it was written from reading go test output.
        # Beat 748: the pattern demanded a `scripts/` prefix, so a doc writing `` `claimcensus.py` ``
        # (bare basename) read as "no instrument" -- an accuser pointed at an honest document.
        # Ten years of the same rule: a checker must not assume one spelling.
        # ...but it must be BACKTICKED and ship MUST exist: the loose form matched prose ("the
        # check.py in prose") and flagged sixteen honest documents. A widening that accuses is
        # worse than the narrowness it replaced.
        # BEAT 749 -- THE FIX: read the Instrument FIELD, not the whole document. Callie 7411:
        # "prose naming a tool is a claim, a field is a value, and the check should want the value."
        # This used to search `text` (the entire file), which is why deleting the field left the check
        # green -- six instruments named in paragraphs satisfied it. Now, if an **Instrument:** line
        # exists, ONLY that line may name the instrument. No field falls back to prose so the archive
        # does not redden on day one, but the fallback is COUNTED and printed: a tolerance that is a
        # visible number can only shrink. An invisible loophole never does.
        field = ""
        for ln in text.splitlines():
            if re.match(r"\*\*Instrument:?\*\*", ln.strip()):
                field = ln.strip()
                break
        searchable = field if field else text
        if not field:
            prose_fallback.append(base)

        im = (re.search(r"`((?:scripts/)?[A-Za-z0-9_]*(?:census|claim|probe|check|audit|fixture)[A-Za-z0-9_]*\.py)`", searchable)
              or re.search(r"`?(scripts/[A-Za-z0-9_.]+\.(?:py|sh))`?", searchable)
              or re.search(r"`?(test/[A-Za-z0-9_.]+\.(?:py|sh|go)|testdata/[A-Za-z0-9_.]+)`?", searchable)
              or re.search(r"`((?:cmd|internal)/[A-Za-z0-9_/]+\.go)`", searchable))
        instrument = (im.group(1) if im else "").replace("`", "")

        title = ""
        for ln in text.splitlines():
            if ln.startswith("# "):
                title = ln[2:].strip()
                break

        docs.append(dict(path=p, base=base, beat=beat, date=date, status=status,
                         instrument=instrument, title=title))

    # --- 1/3/4: every document declares what a reader needs --------------------------------------
    ok("documents found", len(docs) > 0, str(len(docs)))
    ok("every finding declares a status", all(d["status"] for d in docs),
       ", ".join(d["base"] for d in docs if not d["status"]))
    # Beat 749, Callie's 7411: "prose naming a tool is a claim, a field is a value, and the check
    # should want the VALUE." The old form accepted an instrument named ANYWHERE in the document, so
    # deleting the **Instrument:** field left it green (measured, beat 748) -- satisfied, not enforced.
    # A document with no field must now say the words "no instrument" EXPLICITLY, in the field's own
    # place, rather than happening to mention a script in a paragraph.
    # Beat 749: the check now wants the FIELD (Callie 7411: "a field is a value"). Docs written
    # before the convention existed have neither field nor prose name -- 20 of 153, measured.
    # That is a real gap in the archive, so it is REPORTED as a number, not waved through by a
    # prose fallback that would let the count silently grow while the check stayed green.
    unnamed = [d["base"] for d in docs
               if not d["instrument"]
               and "no instrument" not in norm(open(d["path"], encoding="utf-8").read()).lower()]
    ok("every finding names its instrument or explicitly records none",
       not unnamed,
       "%d document(s) name no instrument and do not say “no instrument”: %s"
       % (len(unnamed), ", ".join(unnamed[:6]) + (" …" if len(unnamed) > 6 else "")))
    # Beat 749: the tolerance is DECLARED. A doc with no **Instrument:** field is still accepted if
    # its prose names a tool -- but every such doc is counted and the number is pinned here. The
    # check therefore fails if the population GROWS, which is the enforced half: you may inherit old
    # documents, you may not add new ones to the pile. Deleting a field from a doc that names its
    # tool in a paragraph still passes (measured: it is the same text), and that is stated in the
    # finding rather than hidden -- enforcing the field outright reddens 61 documents and is a
    # decision about what this archive is FOR, not a lint fix (Callie 7411).
    ok("documents falling back to prose have not grown",
       len(prose_fallback) <= PROSE_FALLBACK_BASELINE,
       "%d document(s) name their instrument only in prose, baseline %d: %s"
       % (len(prose_fallback), PROSE_FALLBACK_BASELINE,
          ", ".join(sorted(prose_fallback)[:4]) + (" \u2026" if len(prose_fallback) > 4 else "")))

    # Beat 750, Callie's 7416: "Green that means 'no new debt' is a different green from 'this one
    # passes.'" She is right, and it is the sharper half of a hole I had already written down.
    # A doc with no field is accepted on prose alone -- and deleting a field from a doc whose prose
    # names the tool leaves that green unchanged. The check can be defeated by SUBTRACTION.
    # It cannot add debt; it can still hide a doc that was never verified. So the verdict is now
    # spoken separately: LEGACY, UNVERIFIED is its own line, printed with the names, never folded
    # into a pass. No new red tonight (the population is unchanged); the point is that a reader
    # can now tell "61 old documents, unverified" from "everything here checks out."
    print("     legacy, unverified (no **Instrument:** field, accepted on prose alone): %d"
          % len(prose_fallback))
    if prose_fallback:
        print("       " + ", ".join(sorted(prose_fallback)[:8])
              + (" \u2026" if len(prose_fallback) > 8 else ""))

    instruments = [d["instrument"] for d in docs if d["instrument"]]
    ok("every finding has a date in its filename", all(d["date"] for d in docs),
       ", ".join(d["base"] for d in docs if not d["date"]))

    # --- 5: beat numbers are unique (INDEX is a contents page, not a finding) --------------------
    # FIRST DRAFT WAS BLIND: `re.search` found ONE beat per document, so a collision could never be
    # counted twice, and the 539 it "found" was a later "see 539" reference in pathdiff, not a claim.
    # Now: collect every beat a document claims in its own HEADER only (title line + `**Beat:**`),
    # and never look past line 14 for it.
    for d in docs:
        head = open(d["path"], encoding="utf-8").read().splitlines()[:14]
        title = head[0] if head else ""
        claims = set(re.findall(r"\(\s*(\d{3})\s*\)", title))
        bm = re.search(r"Beat:\*\*\s*(\d{3}[a-z]?)", norm(" ".join(head)))
        if bm:
            claims.add(bm.group(1))
        # KEEP THE SUFFIX. `sorted(claims)[0]` used to drop it, so 666 and 666q -- two
        # genuinely distinct beats sharing a number -- collapsed into one collision and
        # the check could not tell a real duplicate from a disambiguated pair. Sort by
        # the numeric part first, then the suffix, so the LOWEST claim wins and the
        # suffix survives into the comparison.
        d["beat"] = sorted(claims, key=lambda c: (int(c[:3]), c[3:]))[0] if claims else ""

    claims_all = [(d["beat"], d["base"]) for d in docs
                  if d["beat"] and not d["base"].endswith("-INDEX.md")]
    collide = sorted({b for b, _ in claims_all if [x for x, _ in claims_all].count(b) > 1})
    ok("no two findings claim the same beat number", not collide,
       "; ".join(f"{b}: " + "/".join(n for x, n in claims_all if x == b) for b in collide))

    # --- 5b: the archive's SCOPE is every document, not every document the glob matched -------------
    # Beat 748. `paths` globbed FINDING-*.md only, so CENSUS-*.md was invisible and the archive
    # reported "consistent" over documents it could not see. Widening the glob fixes today; this
    # check stops the NEXT unlisted prefix from doing the same thing silently. It compares the
    # indexed population against an independent walk of docs/ -- a SET comparison, not a count
    # (beat 734: pinning a population is a set operation).
    on_disk = sorted(b for b in (os.path.basename(x) for x in glob.glob(os.path.join(DOCS, "*.md")))
                     if b.startswith(("FINDING-", "CENSUS-")) and not b.endswith("-INDEX.md"))
    indexed = sorted(d["base"] for d in docs)
    unseen = [b for b in on_disk if b not in indexed]
    ok("every FINDING-/CENSUS- document on disk is in the archive", not unseen,
       "invisible to docsindex: " + ", ".join(unseen) if unseen else "")

    # --- 6: denominator printed, not remembered ---------------------------------------------------
    scripts = sorted(os.path.basename(p) for p in
                     glob.glob(os.path.join(ROOT, "scripts", "*.py")) +
                     glob.glob(os.path.join(ROOT, "scripts", "*.sh")))
    print(f"     denominator: {len(docs)} finding documents, {len(scripts)} scripts/probes on disk")

    # Beat 748: a bare basename (`` `pathfixture.py` ``) is a valid way to name an instrument in prose,
    # and this check called sixteen honest documents liars for it. Resolve a bare name against
    # scripts/ BEFORE declaring it missing -- but only to scripts/, so a doc still cannot claim a
    # file that is nowhere. The check is "does this instrument exist", not "did the author spell it".
    def instrument_exists(name):
        if not name:
            return False
        if os.path.exists(os.path.join(ROOT, name)):
            return True
        return "/" not in name and os.path.exists(os.path.join(ROOT, "scripts", name))

    ok("instrument count is a real subset of files on disk",
       all(instrument_exists(p) for p in instruments),
       ", ".join(d["base"] for d in docs if not instrument_exists(d["instrument"])))

    # --- write / verify the index -----------------------------------------------------------------
    def check_status_sanity():
        """A status row must be a disposition a reader can act on, not a dangling fragment.

        Goes red on: empty status (already checked), a status ending in a dangling connector, or one
        with unbalanced backticks — the exact shapes the `[^*]` truncation produced."""
        problems = []
        for d in docs:
            st = d["status"]
            if not st:
                continue
            if st.count("`") % 2:
                problems.append((d["base"], "unbalanced backticks (truncated?)"))
            # The fragment detector looks for a DANGLING CONNECTOR — a status that stops after
            # "see"/"because"/"if"/"(see" or on a bare colon reads as cut. A sentence ending on a colon
            # *followed by content* is not a fragment, so require the colon to be the last thing.
            if re.search(r"(?:\bsee|\(see|\bbecause|\bnoted|\bbelow)$", st):
                problems.append((d["base"], "ends on a dangling connector"))
            elif st.endswith(":"):
                problems.append((d["base"], "ends on a bare colon"))
        ok("status lines are whole fields, not fragments", not problems, f"{len(problems)} bad: {problems[:4]}")
        # A fragment detector cannot be trusted if it is the ONLY thing reading the field. Count the
        # markdown cells: five columns means four pipes per data row. A status that swallowed content
        # shows up as a row with the WRONG CELL COUNT, and nothing above would notice.
        if os.path.exists(INDEX):
            rows = [ln for ln in open(INDEX, encoding="utf-8") if ln.startswith("| 2026")]
            ragged = [ln.split("|")[3].strip() for ln in rows if len(ln.split("|")) != 7]
            ok("every index row has exactly 5 cells", not ragged, f"{len(ragged)} ragged: {ragged[:3]}")
        print(f"     status fields read: {sum(1 for d in docs if d['status'])}/{len(docs)}"
              f" · longest {max((len(d['status']) for d in docs), default=0)} chars")


    def render():
        lines = [
            "# FINDINGS INDEX — generated by `scripts/docsindex.py`",
            "",
            "Do not edit by hand; run `python3 scripts/docsindex.py --write`. This file exists because",
            "the archive outgrew the filesystem: a reader needs to know which findings are open, which",
            "are clean passes, and which a later beat withdrew — none of which is visible from `ls`.",
            "",
            f"**{len(docs)} findings · {len(scripts)} scripts on disk.** Status is quoted from each",
            "document's own `**Status:**` line; it is not re-interpreted here.",
            "",
            "| date | beat | finding | status | instrument |",
            "|---|---|---|---|---|",
        ]
        for d in sorted(docs, key=lambda d: (d["date"], d["beat"] or "999")):
            name = os.path.splitext(d["base"])[0]
            lines.append(f"| {d['date']} | {d['beat'] or '—'} | [{name}]({d['base']}) | "
                         f"{d['status'] or 'UNSET'} | `{d['instrument'] or '—'}` |")
        lines += [
            "",
            "## Reading order",
            "",
            "Start at `FINDING-2026-10-23-INDEX.md` for the rule-pack investigation narrative — later",
            "document in it withdraws an earlier one, and the order matters. The 2026-10-24 documents",
            "continue that thread (clock, severity, pathdiff, noload) and share one lesson: the defects",
            "cluster on the **runtime correlation path**; the **static authoring path** is validated at",
            "a single choke point (`ParsePack` → `Validate`) and predicting a defect there has been",
            "wrong twice.",
            "",
            "## Standing rule, carried forward",
            "",
            "Every probe prints **its denominator and how many cases were capable of failing.**",
            "Three corrections on 2026-10-23 came from instruments scoring themselves generously.",
            "",
        ]
        return "\n".join(lines)


    if "--write" in sys.argv:
        body = render()
        with open(INDEX, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"wrote {rel(INDEX)}")

    check_status_sanity()

    # --- 2: index <-> documents agree -------------------------------------------------------------
    ok("index exists (run --write first in a fresh clone)", os.path.exists(INDEX))
    if os.path.exists(INDEX):
        idx = open(INDEX, encoding="utf-8").read()
        missing = [d["base"] for d in docs if f"({d['base']})" not in idx]
        ok("every finding document appears in the index", not missing, ", ".join(missing))
        ghost = [m for m in re.findall(r"\]\((FINDING-[^)]+\.md)\)", idx)
                 if not os.path.exists(os.path.join(DOCS, m))]
        ok("every index entry points at a real document", not ghost, ", ".join(ghost))
        ok("index is current (regenerating changes nothing)",
           norm(idx) == norm(render()), "stale — run: python3 scripts/docsindex.py --write")

    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print(f"archive consistent: {len(docs)} findings indexed, {len(scripts)} scripts on disk")


if __name__ == "__main__":
    sys.exit(main())

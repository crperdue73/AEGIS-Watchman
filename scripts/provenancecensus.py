#!/usr/bin/env python3
"""provenancecensus — does a record's rule provenance survive the trip, or is it re-derived?

BEAT 732. Beat 731 asked: if LastSeen can't prove liveness, what else silently stands in
for a different measurement? Candidate named was `Technique`/`RulePack` labels. Followed it.

THE GOOD NEWS, MEASURED FIRST
-----------------------------
Provenance DOES ride on the event. `internal/rules/engine.go:73` constructs
`schema.Rule{ID, Pack, Version, Technique}` at match time — the values come from the rule
object that fired, not from a lookup afterwards. `eventstore.go:351-355` copies them onto
the Record verbatim. SQLite persists all four as columns (`eventstore_sqlite.go:149,223`).
So a rule-pack edit after the fact does NOT retroactively relabel old records. That is the
right design and I am not going to manufacture a problem with it.

WHAT IS ACTUALLY THIN
---------------------
Three things, each narrower than "provenance is broken":

1. **No rule-pack identity on the record.** `Rule.Pack`/`Rule.Version` are fields that
   exist, and I need to check whether the shipped pack sets them — if they default empty,
   the record knows *which rule* but not *which pack build*, so "same rule id, different
   logic" is unrecoverable from the store.

2. **The technique vocabulary is hand-declared and unversioned.** Measured from
   `rules/carbonato.yaml`: 20 rules, 20 `technique:` values, **11 distinct**. The
   vocabulary is `phase3/<slug>` — 11 strings coined for this pack, not ATT&CK ids. The
   schema comment calls it *"the rule's ATT&CK-style tag"*, which invites a consumer to
   read it as an ATT&CK technique id. It isn't one. Nothing validates the vocabulary, so a
   typo produces a new technique and the fleet rollup (`fleet.go:115`) silently grows a
   category.

3. **The `action.detail.technique` fallback** (`eventstore.go:367`) lets an event carry a
   technique the rule did not declare — recorded verbatim with no marker distinguishing
   "this is what the rule said" from "this is what the producer typed." The comment calls
   it a legacy fallback and that is fair; the point is the record cannot tell you which
   branch filled it.

WHY THIS ONE IS SMALLER THAN 724–731, AND I SHOULD SAY SO
----------------------------------------------------------
The previous seven beats each found the tool asserting something untrue. This beat found
the opposite: the mechanism is sound, and what is thin is **the meaning of the label** —
a vocabulary with no validator and a name ("ATT&CK-style") that overpromises. That is a
documentation-and-validation finding, not a false-alarm finding, and I will not inflate it
into one. Recording it honestly as the smallest thing found in this run.

WHAT THIS SCRIPT CHECKS
-----------------------
A. provenance is stamped at match time from the rule object (not looked up later)
B. the record copies it verbatim and SQLite persists it as columns
C. Pack/Version are set by the pack loader (or measured as empty — either answer recorded)
D. the technique vocabulary is counted and pinned, and NOT claimed to be ATT&CK
E. the action.detail fallback exists and leaves no branch marker
F. doc scope: carries the not-applied marker and the explicit "smaller than the rest" line
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0

# Pinned vocabulary, measured this beat (beat 732). If the pack gains/loses a technique
# this goes red and a human decides whether the new label is intentional.
EXPECTED_DISTINCT_TECHNIQUES = 11
EXPECTED_RULES_WITH_TECHNIQUE = 20


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def check_a_stamped_at_match():
    src = read("internal/rules/engine.go")
    # Quote the line that must hold, not a word near it (beat 731 standing rule).
    if re.search(r"ev\.Rule = &schema\.Rule\{ID: r\.ID, Pack: r\.Pack, Version: r\.Version, Technique: r\.Technique\}", src):
        ok("A: provenance is stamped at match time from the firing rule object — NOT "
           "re-derived at read time, so a later pack edit cannot relabel old records")
    else:
        fail("A: the match-time stamping line changed — provenance may now be looked up "
             "later; re-derive before believing the doc")


def check_b_copied_and_persisted():
    src = read("internal/central/eventstore.go")
    for line, label in [
        (r"rec\.RuleID = ev\.Rule\.ID", "rule id"),
        (r"rec\.Technique = ev\.Rule\.Technique", "technique"),
        (r"rec\.RulePack = ev\.Rule\.Pack", "pack"),
        (r"rec\.RuleVersion = ev\.Rule\.Version", "pack version"),
    ]:
        if not re.search(line, src):
            fail("B: %s is no longer copied verbatim onto the record — re-derive" % label)
            return
    ok("B: all four provenance fields are copied verbatim onto the Record")
    sq = read("internal/central/eventstore_sqlite.go")
    if "rec.Technique," in sq and "&r.Technique" in sq and "&r.RulePack" in sq and "&r.RuleVersion" in sq:
        ok("B: SQLite persists technique/pack/version as columns — the durable backend "
           "keeps provenance, so it is not a memory-only fact")
    else:
        fail("B: the SQLite column set changed — provenance durability needs re-deriving")


def check_c_pack_identity():
    src = read("internal/rules/rule.go")
    m = re.search(r"type Rule struct \{(.*?)\n\}", src, re.S)
    if not m:
        fail("C: Rule type not found")
        return
    body = m.group(1)
    has_pack = "Pack " in body
    has_ver = "Version " in body
    if has_pack and has_ver:
        ok("C: the rule type carries Pack and Version — pack identity has a home on the record")
    else:
        fail("C: Pack/Version absent from the rule type — re-derive")
    # Are they SET by the loader? Beat 732 first version searched for the string
    # `.Pack = ` ANYWHERE in internal/rules/*.go — and M4 (deleting the whole
    # `if r.Pack == "" { r.Pack = p.Name }` block) stayed GREEN, because the marker
    # survived as the declaration in rule.go. That is the fourth word-near-the-code check
    # in four beats. Quote the block that must hold, both branches and both fields.
    loader = ROOT / "internal/rules/rule.go"
    lt = loader.read_text(errors="replace")
    for field, src in (("Pack", "p.Name"), ("Version", "p.Version")):
        pat = (r'if r\.%s == "" \{\s*\n\s*r\.%s = %s\s*\n\s*\}' % (field, field, re.escape(src)))
        if re.search(pat, lt):
            ok("C: the loader defaults r.%s from the pack when the rule omits it — pack "
               "identity is stamped at load, so records carry pack-build identity" % field)
        else:
            fail("C: the loader no longer defaults r.%s from the pack (rule.go) — records "
                 "lose pack identity; re-derive" % field)


def check_d_vocabulary():
    y = read("rules/carbonato.yaml")
    ids = re.findall(r"^\s*- id: (\S+)", y, re.M)
    techs = re.findall(r"^\s*technique: (\S+)", y, re.M)
    if len(techs) != EXPECTED_RULES_WITH_TECHNIQUE:
        fail("D: %d rules declare a technique, expected %d — the vocabulary moved; "
             "re-measure and decide whether that is intentional" % (len(techs), EXPECTED_RULES_WITH_TECHNIQUE))
        return
    distinct = sorted(set(techs))
    if len(distinct) != EXPECTED_DISTINCT_TECHNIQUES:
        fail("D: %d distinct techniques, expected %d: %r" % (len(distinct), EXPECTED_DISTINCT_TECHNIQUES, distinct))
        return
    ok("D: %d rules, %d distinct techniques (pinned) — the vocabulary is small and shared" % (len(ids), len(distinct)))
    non_attack = [t for t in distinct if not re.match(r"^T\d{4}", t)]
    if non_attack:
        ok("D: none of the %d techniques is an ATT&CK id (all are %r-style slugs) — while "
           "schema.go:76 calls the field an \"ATT&CK-style tag\", which invites exactly that "
           "misreading" % (len(non_attack), "phase3/…"))
    else:
        ok("D: the vocabulary now uses ATT&CK ids — the schema comment is now accurate; "
           "re-derive the finding")


def check_e_fallback_unmarked():
    src = read("internal/central/eventstore.go")
    if re.search(r'if t, ok := ev\.Action\.Detail\["technique"\]\.\(string\); ok \{\s*rec\.Technique = t', src):
        ok("E gap: action.detail.technique is a second source for the same field, written "
           "verbatim — a record cannot say whether the rule declared it or a producer typed it")
    else:
        fail("E: the action.detail.technique fallback changed — re-derive")
    if "TechniqueSource" in src or "tech_source" in src:
        fail("E: a branch marker now exists (TechniqueSource) — the gap may be closing; re-derive")
    else:
        ok("E gap: no TechniqueSource-equivalent marker exists — confirmed by search, not assumed")


def check_f_doc_scope():
    doc = ROOT / "docs/FINDING-2026-11-01m-technique-vocabulary-is-hand-declared.md"
    if not doc.exists():
        fail("F: the finding doc is missing")
        return
    t = re.sub(r"\s+", " ", doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("smaller than", "the refusal to inflate this into a false-alarm finding"),
        ("not ATT&CK", "the vocabulary's actual identity"),
    ]:
        if phrase in t:
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_stamped_at_match()
    check_b_copied_and_persisted()
    check_c_pack_identity()
    check_d_vocabulary()
    check_e_fallback_unmarked()
    check_f_doc_scope()
    print()
    if FAILS:
        print("provenancecensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("provenancecensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

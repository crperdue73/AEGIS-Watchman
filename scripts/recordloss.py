#!/usr/bin/env python3
"""recordloss — beats 659-665 followed the alert path and the pack. This checks the
third leg: what the RECORD STORE keeps. "Retained" is not the same as "recoverable."

FOUND
-----
`RecordFromEvent` (eventstore.go:339-372) builds a Record from a schema.Event. The
Record struct (eventstore.go:37-72) has 14 fields, and they answer: which host,
which agent root, which event class, which severity, which rule id/pack/version,
which ATT&CK technique, which action type, what the producer claimed about the
source, which path, and a one-line summary.

Two things it does NOT keep:

  1. THE EVENT'S OWN TIMESTAMP. `Record.When` is documented as "when the record was
     retained (server time at ingest)" and is stamped from `s.nowTime()` in
     retain() (service.go:86). **`ev.TS` -- the time the event actually happened on
     the host -- is stored nowhere in the record.** grep for `ev.TS` in
     eventstore.go: zero hits.

  2. THE ARGV. `rec.ActionType` keeps "process.exec"; the command line that ran does
     not appear in any Record field, and `summarizeEvent` renders
     `"<rule_id> (<action_type>) on <host>"`. grep for `argv` in eventstore.go:
     zero hits.

WHY THESE TWO TOGETHER ARE THE FINDING
--------------------------------------
Taken alone, (1) is a metadata gap and (2) is a detail gap. Together they mean the
retained record cannot answer the two questions a person asks when an alert fires:

    WHEN did this happen?     -> only "when we received it"
    WHAT ran?                 -> only "process.exec"

Beat 662 measured that the alert PAYLOAD carries all this -- the full event with
argv and ts goes to the webhook. So the store, the one component whose entire job is
durability, keeps strictly LESS than the transient message. Once the alarm is
delivered and the window scrolls, the evidence for "cat .ssh/id_rsa" is a record
that says a rule_match happened, on some host, at roughly ingest time.

AND A THIRD THING, MEASURED, THAT MAKES IT WORSE
-------------------------------------------------
`retain` (service.go:82-93) appends each record and on error does exactly this:

    if err := s.records.Append(...); err != nil && s.logger != nil {
        s.logger.Printf("central: record store append failed: %v", err)
    }

**The error is logged and discarded.** retain returns nothing, IngestActions returns
nil error on every path (beat 660), so the collector is told "accepted" for events
that were never stored. Retention failure is invisible to the sender and indistinguishable
to the operator from a quiet host.

EGRESS-ORDER COROLLARY (a reading, not a measurement)
-----------------------------------------------------
`when` is computed ONCE outside the loop, so every event in a batch shares one
timestamp -- and batches are per-Poll, so the arrival stamp has tick granularity.
Beat 655 found the correlation window runs on this same poll clock. The record store
therefore also loses intra-batch order. Flagged as a reading; I did not measure a
consumer that depends on it.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: Record has no field carrying ev.TS or argv (14-field struct + parser both
    read); When is an ingest stamp by its own doc comment; retain logs and drops
    append errors; IngestActions still returns success.
NOT CLAIMED: that this is deliberate, or that a specific investigation was hurt by
    it, or that adding argv is free (it is unbounded -- beat 661 measured 426 B per
    event mostly from other fields; argv can be arbitrarily long and a Record store
    that keeps every argv needs its own size policy). No store was written to.
"""

import sys
import pathlib


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        es = (root / "internal/central/eventstore.go").read_text()
        svc = (root / "internal/central/service.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    print("-- the Record's fields, parsed from the struct BODY --")
    body = es.split("type Record struct {")[1].split("\n}")[0]
    fields = []
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("//") or not s:
            continue
        name = s.split()[0]
        fields.append(name)
    print(f"     {len(fields)} fields: {fields}")
    check("Record keeps which host / agent root / class / severity",
          all(f in fields for f in ("HostID", "AgentRoot", "Event", "Severity")))
    check("Record keeps the rule id + pack + version + technique",
          all(f in fields for f in ("RuleID", "RulePack", "RuleVersion", "Technique")))
    check("Record has NO field for the event's own timestamp (ev.TS)",
          not any(f.lower() in ("ts", "timestamp", "eventtime", "occurredat")
                  for f in fields))
    check("Record has NO field for argv",
          not any("argv" in f.lower() or "command" in f.lower() for f in fields))

    print("-- and the parser never puts them there --")
    rfe = es.split("func RecordFromEvent(")[1].split("\nfunc ")[0]
    check("RecordFromEvent never reads ev.TS", "ev.TS" not in rfe)
    check("RecordFromEvent never reads ev.Action.Argv",
          "Argv" not in rfe and "argv" not in rfe.lower())
    check("the summary names the rule and host, never the command line",
          "ev.Rule.ID" in es.split("func summarizeEvent(")[1].split("\nfunc ")[0])

    print("-- When is an ingest stamp, by its own documentation --")
    check("Record.When is documented as server time at ingest",
          "when the record was retained (server time at ingest)" in es)
    rt = svc.split("func (s *Service) retain(")[1].split("\nfunc ")[0]
    check("retain stamps `when` once from s.nowTime(), outside the loop",
          "when := s.nowTime()" in rt and rt.index("when := s.nowTime()") < rt.index("for _, ev"))

    print("-- retention failure is logged and swallowed --")
    check("append error is logged and NOT propagated",
          "record store append failed" in rt and "return err" not in rt)
    ia = svc.split("func (s *Service) IngestActions(")[1].split("\nfunc ")[0]
    # Test the PROPERTY, not the shape: take the statement that calls retain and
    # require it to be a bare call -- no assignment of a return value, because
    # retain has none to give.
    retain_lines = [ln.strip() for ln in ia.splitlines() if "s.retain(" in ln]
    check(f"IngestActions calls retain as a statement, discarding any failure ({retain_lines})",
          len(retain_lines) == 1 and retain_lines[0].startswith("s.retain("))
    check("retain() has no return value to propagate",
          "func (s *Service) retain(host schema.Host, agent schema.Agent, events []schema.Event) {"
          in svc)

    if fails:
        print(f"\nRECORD LOSS RED: {fails}")
        return 1

    print("\n=> The store is the durable half of the product, and it keeps strictly")
    print("   LESS than the webhook payload beat 662 measured: no ev.TS, no argv.")
    print("   After the alert scrolls, 'cat .ssh/id_rsa' is a record that says a")
    print("   rule matched, on a host, at ingest time -- and if the append failed,")
    print("   not even that, because the error is logged and dropped while the")
    print("   collector is told 'accepted'.")
    print("   Fixes, Dad's call: (a) add EventTS (ev.TS) and a bounded argv digest to")
    print("   Record -- bounded because argv is unbounded input; (b) return the")
    print("   retain error to the collector so a failed store is not 'accepted'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

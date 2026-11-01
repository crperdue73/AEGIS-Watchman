#!/usr/bin/env python3
"""alertfanout — beat 661 found the batch unbounded. This asks what the alert
layer does with it. The answer: three sinks, three different failures, and the
two quiet ones are the ones an operator relies on.

THE CHAIN (measured end to end from source)
-------------------------------------------
  collector -> POST /actions (1 MiB cap, 413)     [engine unbounded, beat 661]
  -> IngestActions accepts the whole slice
  -> s.alert(ctx, host, agent, accepted)  builds ONE Alert holding ALL events
  -> alerter.Alert(alert) fans out to every sink, unchanged

So the alert payload is as unbounded as the batch. Measured: 2520 events at
426 B each inside the Alert envelope = **1.0 MiB of JSON per alert.**

WHAT EACH SINK DOES WITH THAT
----------------------------
  WebhookAlerter (alert.go:120-141)  json.Marshal(whole alert) -> one POST.
      No size handling, no splitting, no truncation. An endpoint with a 1 MiB
      body limit (Slack, a default nginx `client_max_body_size`, API gateways)
      answers 413, and Alert returns an error -> DeliveryError.

  EmailAlerter (email.go)  renders EVERY event into a plain-text body plus a
      JSON attachment. 2520 events of readable report. Many MTAs cap message
      size well under this; an oversized message is refused at SMTP.

  LogAlerter (alert.go:149-155)  prints ONLY `a.Summary()`. Every event is
      discarded. You learn "2520 rule matches on host:root (max=high)" and
      nothing about WHICH rule, which argv, which path.

THE ASYMMETRY THAT MATTERS
--------------------------
MultiAlerter is documented as "every sink is attempted even if an earlier one
fails, and the failures are joined so one unreachable webhook cannot silently
suppress a working sink." That is true and it is the right instinct. But it
guards against a sink being DOWN, not against a sink being UNABLE:

    webhook 413  -> returns error -> logged (central: alert delivery failed)
    email 552    -> returns error -> logged
    log sink     -> returns NIL, has "succeeded", and dropped the evidence

So on an oversized alert the two rich sinks fail loudly and the one sink that
always "works" is the one that threw the content away. The operator's fallback
is a count. Every detail needed to act on a runaway agent -- the rule id, the
argv -- exists only in the payloads that just failed.

And this is NOT an edge case layered on an edge case: the same condition that
produces the oversized batch (a runaway agent, beat 661) is the condition under
which you most need the detail, and beating the 1 MiB batch cap requires the
volume that also produces an oversized alert.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: the alert carries the full event slice to every sink; the payload
    measures 1.0 MiB at the beat-661 threshold; Webhook and Email do nothing
    about size; LogAlerter's Summary() discards all events by construction;
    MultiAlerter treats LogAlerter as a success.
NOT CLAIMED: that any specific endpoint rejects at 1 MiB (that is the receiver's
    policy, not our code); that delivery is RETRIED (it is not -- one attempt,
    then a log line); that a human missed an alert. Still no request sent.
"""

import sys
import json
import pathlib

MB = 1 << 20


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    fails = 0

    def check(name, ok):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            fails += 1

    try:
        alert = (root / "internal/central/alert.go").read_text()
        svc = (root / "internal/central/service.go").read_text()
        email = (root / "internal/central/email.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    # ---- 1. one alert holds every event --------------------------------
    print("-- the alert carries the whole slice --")
    check("service.alert builds an Alert from the full `events` slice",
          "a := Alert{When: time.Now().UTC(), Host: host, Agent: agent, Events: events}" in svc)
    check("IngestActions passes `accepted` (all of it) to alert",
          "s.alert(ctx, host, agent, accepted)" in svc)

    # ---- 2. the measured payload ---------------------------------------
    ev = {
        "schema": "aegis.event/v1", "event": "behavior.rule_match",
        "ts": "2026-10-29T12:00:00.123456789Z",
        "host": {"id": "dept-crperdue-tech", "name": "dept-crperdue-tech"},
        "agent": {"id": "vivi", "root": "/home/u"},
        "action": {"type": "process.exec", "argv": ["cat", ".ssh/id_rsa"],
                   "exe": "/usr/bin/cat", "cwd": "/home/u"},
        "rule": {"id": "carbonato.secret.read_after_shell", "severity": "high"},
        "actor": {"kind": "agent", "id": "vivi"},
        "severity": "high",
    }
    n = (MB // len(json.dumps(ev, separators=(",", ":")))) + 1  # the 413 threshold
    a = {"when": "2026-10-29T16:00:00Z",
         "host": {"id": "dept-crperdue-tech"},
         "agent": {"id": "vivi", "root": "/home/u"},
         "events": [ev] * n}
    payload = len(json.dumps(a, separators=(",", ":")))
    print(f"ok   {n} events (the count that trips the batch 413) -> alert payload "
          f"{payload} B = {payload / MB:.2f} MiB")

    # ---- 3. what each sink does --------------------------------------
    print("-- sinks --")
    web = alert.split("func (w *WebhookAlerter) Alert")[1].split("\nfunc ")[0]
    check("WebhookAlerter marshals the WHOLE alert in one POST",
          "json.Marshal(a)" in web and "http.MethodPost" in web)
    check("WebhookAlerter does no size handling (no split/truncate/limit)",
          not any(k in web for k in ("MaxBytes", "split", "truncate", "LimitReader")))

    loga = alert.split("func (l *LogAlerter) Alert")[1].split("\nfunc ")[0]
    check("LogAlerter prints ONLY the summary -- every event discarded",
          "a.Summary()" in loga and "a.Events" not in loga)

    # Summary() counts classes but does not name rules/paths/argv
    summ = alert.split("func (a Alert) Summary()")[1].split("\nfunc ")[0]
    check("Summary() names classes + count, never rule ids or argv",
          "classes[e.Event]++" in summ and "e.Rule" not in summ)

    check("EmailAlerter renders every event into the body",
          "range a.Events" in email or "for _, ev := range" in email)

    # ---- 4. the asymmetry ---------------------------------------------
    print("-- MultiAlerter: attempts every sink, but 'no error' != 'delivered' --")
    multi = alert.split("func (m *MultiAlerter) Alert")[1].split("\nfunc ")[0]
    check("MultiAlerter attempts every sink and joins failures",
          "errs = append(errs, err.Error())" in multi)
    # SEVENTH prose-as-data bite this week: this check first tested
    # `"err" not in loga.split("logger.Printf")[1]`, and LogAlerter's own comment
    # ("The message is ...") -- no, precisely: the trailing `// Alert implements
    # Alerter.` block above the function contains no err, but the split landed on
    # a region containing the word in a comment, so the check FAILED on correct
    # code. Test the CONTROL FLOW, not the text: strip comments, then require the
    # body's only return to be a bare `return nil`.
    body = [ln for ln in loga.splitlines() if not ln.strip().startswith("//")]
    code = "\n".join(body)
    returns = [ln.strip() for ln in code.splitlines() if ln.strip().startswith("return")]
    check("LogAlerter's only return is nil -> counted as a success",
          returns == ["return nil"])

    # ---- 5. is delivery retried? ---------------------------------------
    al = svc.split("func (s *Service) alert(")[1].split("\nfunc ")[0]
    check("alert() makes ONE attempt, then logs (no retry, no queue)",
          "s.alerter.Alert(ctx, a)" in al and "for " not in al)

    if fails:
        print(f"\nALERT FANOUT RED: {fails}")
        return 1

    print("\n=> Same condition, two layers, one consequence: the volume that")
    print("   produces the oversized batch also produces an oversized alert. The")
    print("   webhook and email sinks fail LOUDLY at that size; the log sink")
    print("   'succeeds' having thrown away every rule id and argv. The fallback")
    print("   that always works is the one that keeps no evidence.")
    print("   Fixes, Dad's call: (a) cap events per alert and emit one summary")
    print("   alert naming distinct (rule, path) pairs -- bounded payload, more")
    print("   signal; (b) split oversized webhook payloads; (c) make LogAlerter")
    print("   print the distinct rule ids, not just a count.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

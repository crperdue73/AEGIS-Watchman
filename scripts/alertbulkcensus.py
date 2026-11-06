#!/usr/bin/env python3
"""alertbulkcensus — the alert has no size cap, and a redeploy posts 374 KB.

BEAT 745. MaxDiffBytes caps the diff attached to EACH event (config.go:33, 4096).
Nothing caps how many events ride in one Alert, and nothing caps the serialised
alert. Service.alert wraps every drift event from one cycle into one Alert;
WebhookAlerter json.Marshals the whole thing and POSTs it with no size guard.

MEASURED (beat 745, real Drift + the real pre-order, unchanged MaxDiffBytes=4096):
  files=   1 events=   1 alert_json=    479
  files=  50 events=  50 alert_json=  18801
  files=1000 events=1000 alert_json= 373988
Strictly linear, ~374 B/event — and worse when diffs are small, because the envelope
dominates. Delivery failure is a log line with no retry, so an oversized body means the
whole cycle's alert is LOST exactly when the signal is strongest.

Checks:
  A. the per-event cap exists and is right (the sound part)
  B. nothing caps the event COUNT in an Alert
  C. nothing caps the serialised bytes on the webhook path
  D. delivery failure is logged and dropped — no retry, no queue, no counter
  E. MultiAlerter pays the cost per sink; EmailAlerter re-renders every event
  F. the doc keeps the measured table, the real finding (loss, not bytes), and the
     not-claimed section
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILS, OKS = [], 0


def ok(m):
    global OKS
    OKS += 1
    print("ok   " + m)


def fail(m):
    global FAILS
    FAILS.append(m)
    print("FAIL " + m)


def read(rel):
    return (ROOT / rel).read_text(errors="replace")


def func_body(src, needle):
    i = src.find(needle)
    if i < 0:
        return ""
    j = src.find("\nfunc ", i + 1)
    return src[i:] if j < 0 else src[i:j]


def check_a_per_event_cap():
    cfg = read("internal/central/config.go")
    if re.search(r"MaxDiffBytes int `yaml:\"max_diff_bytes\"`", cfg) and re.search(r"MaxDiffBytes: 4096", cfg):
        ok("A sound: MaxDiffBytes defaults to 4096 and is documented as capping the diff "
           "attached to each drift event. Bounding the diff is the right call.")
    else:
        fail("A: the per-event diff cap changed — re-derive")
    if re.search(r"if c\.MaxDiffBytes < 0", cfg):
        ok("A sound: a negative value is rejected at load rather than silently treated as 0")
    else:
        fail("A: the negative-value guard is gone")


def check_b_no_event_cap():
    src = read("internal/central/alert.go")
    b = func_body(src, "func (s *Service) alert(") or func_body(read("internal/central/service.go"), "func (s *Service) alert(")
    sb = func_body(read("internal/central/service.go"), "func (s *Service) alert(")
    if not sb:
        fail("B: Service.alert not found")
        return
    # The whole body, quoted: one Alert, every event, no truncation.
    if re.search(r"a := Alert\{When: [^}]*Events: events\}", sb):
        ok("B gap: `Alert{... Events: events}` — every event from the cycle rides in one "
           "Alert. There is no count cap anywhere on this path. Measured: 1000 events -> "
           "373,988 bytes of JSON.")
    else:
        fail("B: Service.alert changed shape — re-derive")
    # Pin the ABSENCE of a cap: no slice-truncation, no len() comparison against a limit.
    if re.search(r"events\[:", sb) or re.search(r"if len\(events\) >", sb):
        fail("B: a cap now appears in Service.alert — the finding may be fixed; re-derive")
    else:
        ok("B gap: no `events[:n]` and no `if len(events) >` in Service.alert — the absence "
           "of a cap is pinned, so adding one goes red.")


def check_c_no_byte_cap():
    wb = func_body(read("internal/central/alert.go"), "func (w *WebhookAlerter) Alert(")
    if not wb:
        fail("C: WebhookAlerter.Alert not found")
        return
    if re.search(r"body, err := json\.Marshal\(a\)", wb) and re.search(r"http\.NewRequestWithContext\(", wb):
        ok("C gap: json.Marshal of the entire Alert, straight into the request body. No "
           "length check, no truncation, no ceiling.")
    else:
        fail("C: the webhook payload construction changed — re-derive")
    if re.search(r"len\(body\)\s*>", wb):
        fail("C: a body-size guard now appears in WebhookAlerter — the gap may be closed")
    else:
        ok("C gap: no `len(body) >` in WebhookAlerter.Alert — pinned as absent.")
    if re.search(r"Timeout: 10 \* time\.Second", read("internal/central/alert.go")):
        ok("C sound: the HTTP client has a 10s timeout — a hung receiver cannot block "
           "ingest forever. That timeout is the ONLY guard on this path. Note it bounds "
           "TIME, not SIZE.")


def check_d_loss_is_a_log_line():
    sb = func_body(read("internal/central/service.go"), "func (s *Service) alert(")
    if re.search(r'if err := s\.alerter\.Alert\(ctx, a\); err != nil && s\.logger != nil \{\s*\n\s*s\.logger\.Printf\("central: alert delivery failed: %v", err\)', sb):
        ok("D gap: delivery failure -> one log line, then return. No retry, no queue, no "
           "counter, no state. An oversized body rejected by the receiver means this "
           "cycle's ENTIRE alert is lost, and the only trace is a log line. The alert "
           "degrades exactly when the signal is strongest — that is the finding.")
    else:
        fail("D: delivery-failure handling changed — if a retry/queue appeared, this "
             "finding's severity changes; re-derive")
    if re.search(r"retry|backoff|requeue", read("internal/central/alert.go"), re.I):
        fail("D: retry logic now exists in alert.go — re-derive")
    else:
        ok("D gap: no retry/backoff/requeue anywhere in alert.go — pinned as absent")


def check_e_multi_sink():
    cfg = read("internal/central/config.go")
    ab = func_body(cfg, "func (c Config) Alerter(")
    if "MultiAlerter{Alerters: sinks}" in ab:
        ok("E gap: two configured sinks => MultiAlerter, which calls every sink with the "
           "SAME Alert — the webhook marshals it, EmailAlerter re-renders every event. "
           "The amplification (and the work) is paid once per sink.")
    else:
        fail("E: the sink composition changed — re-derive")
    mb = func_body(read("internal/central/alert.go"), "func (m *MultiAlerter) Alert(")
    if re.search(r"for _, al := range m\.Alerters", mb):
        ok("E sound: MultiAlerter attempts every sink even if an earlier one fails and "
           "joins the errors — a dead webhook cannot suppress a working sink. Careful "
           "code; it simply has no notion of size.")
    else:
        fail("E: MultiAlerter's fan-out changed — re-derive")
    em = func_body(read("internal/central/email.go"), "func (e *EmailAlerter) render(")
    if not em:
        em = read("internal/central/email.go")
    if re.search(r"for _, ev := range a\.Events \{\s*\n\s*e\.writeEvent\(&b, ev\)", em):
        ok("E gap: EmailAlerter renders one section per event with no truncation — the "
           "same unbounded shape on a second, slower sink (measured n=1000 => 1000 sections).")
    else:
        fail("E: the email event loop changed — re-derive")


def check_f_doc():
    doc = ROOT / "docs/FINDING-2026-11-02c-the-alert-has-no-size-cap.md"
    if not doc.exists():
        fail("F: doc missing")
        return
    # Beat 745 own-mistake: the measured rows are column-aligned (`alert_json=  373988`,
    # two spaces). Collapsing whitespace on the DOC side was not enough because I compared
    # against a single-space phrase. Collapse on both sides instead of writing the phrase
    # to match the alignment — a checker that has to spell out the padding is a checker
    # that breaks the next time a column moves.
    collapse = lambda x: re.sub(r"\s+", " ", re.sub(r"[*_`]", "", x))
    t = collapse(doc.read_text())
    for phrase, label in [
        ("NOT APPLIED", "the not-fixed marker"),
        ("alert_json= 373988", "the measured 1000-file row"),
        ("~374 bytes per event", "the linear rate"),
        ("the alert degrades exactly when the signal is strongest", "the real finding"),
        ("the same refrain as beat 740", "the cross-beat de-dup"),
        ("What I am NOT claiming", "the not-claimed section"),
        ("no retry and no queue", "the loss mechanism"),
    ]:
        if collapse(phrase).lower() in t.lower():
            ok("F: doc carries %s" % label)
        else:
            fail("F: doc lost %s (%r)" % (label, phrase))


def main():
    check_a_per_event_cap()
    check_b_no_event_cap()
    check_c_no_byte_cap()
    check_d_loss_is_a_log_line()
    check_e_multi_sink()
    check_f_doc()
    print()
    if FAILS:
        print("alertbulkcensus: %d FAIL / %d ok" % (len(FAILS), OKS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("alertbulkcensus: %d ok, 0 FAIL" % OKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())

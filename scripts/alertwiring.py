#!/usr/bin/env python3
"""alertwiring — beats 661/662 asked how big the alert is and what each sink does
with it. Both assumed the alert is delivered SOMEWHERE. This asks a prior
question: is an alerting sink installed at all, and what happens when none is?

MEASURED FROM SOURCE
--------------------
config.go:241 Alerter(logger) composes sinks from:
    c.Alerts.Webhook != ""      -> WebhookAlerter
    c.Alerts.Email.Host != ""   -> EmailAlerter
    c.Alerts.Log && logger!=nil -> LogAlerter
    len(sinks)==0 -> returns nil       <-- NO SINK AT ALL

cmd/central/main.go:88-95:
    if a := cfg.Alerter(log.Default()); a != nil { svc.SetAlerter(a) } else { log... }

and Service.alerter stays nil (`SetAlerter` never called). Then
service.go IngestActions:
    if s.alerter != nil { s.alert(...) }    <-- the whole alert step is SKIPPED

THE SAVING GRACE
----------------
DefaultConfig() sets `Alerts: Alerts{Log: true}` (config.go:98), so an empty or
missing config file gets a LogAlerter. Out of the box, alerts are not lost.

THE HOLE
--------
`Log: true` is only a DEFAULT. A config file containing

    alerts:
      log: false

(plain YAML, nothing exotic -- and the obvious thing to write while setting up a
webhook you have not finished configuring yet) returns nil from Alerter(). There is
no Validate() on Config anywhere in the package (checked: no `func (c Config)
Validate` / `func (c *Config) Validate`), no error, no refusal to start. The service
boots, ingests every event, retains every event, and silently drops 100% of the
alerts after printing exactly one line at startup:

    watchman-central: alerting disabled (no webhook, log off)

That line is accurate, it is printed once, and it scrolls away. Nothing at runtime
says an alert was suppressed, because `if s.alerter != nil` is a silent skip --
not a log, not a counter, not an error.

SECOND PATH TO THE SAME PLACE
-----------------------------
`cfg.Alerter(log.Default())` -- the logger argument is the only thing that makes
the log sink possible, and it is passed as `log.Default()`, so that is fine. But
if someone calls Alerter(nil) (the signature invites it, since the Log sink's whole
existence depends on it), the log sink silently does not compose. One nil argument
turns a configured sink off without saying so.

CLAIM / NOT CLAIMED
-------------------
CLAIMED: zero sinks -> nil alerter -> the alert step is skipped with no signal at
    runtime; the default config avoids this; a config with `log: false` and no
    webhook/email reaches it; Config has no validation, so nothing refuses or warns
    beyond one startup line.
NOT CLAIMED: that an operator has done this. It is a configuration footgun whose
    blast radius is total (100% of alerts), and I am labelling it as a footgun, not
    as an observed incident.
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
        cfg = (root / "internal/central/config.go").read_text()
        svc = (root / "internal/central/service.go").read_text()
        main_go = (root / "cmd/central/main.go").read_text()
    except Exception as e:  # noqa: BLE001
        print(f"FAIL could not read source ({e}); a missing input is not a pass")
        return 1

    print("-- how sinks are composed --")
    al_fn = cfg.split("func (c Config) Alerter(")[1].split("\nfunc ")[0]
    check("webhook sink composed only when non-empty",
          'if c.Alerts.Webhook != ""' in al_fn)
    check("email sink composed only when Host non-empty",
          'if e := c.Alerts.Email; e.Host != ""' in al_fn)
    check("log sink requires BOTH the flag and a non-nil logger",
          "if c.Alerts.Log && logger != nil" in al_fn)
    check("zero sinks returns nil (not an error, not a no-op sink)",
          "case 0:" in al_fn and "return nil" in al_fn)

    print("-- what happens when the alerter is nil --")
    ia = svc.split("func (s *Service) IngestActions")[1].split("\nfunc ")[0]
    check("alert step is SKIPPED when alerter is nil",
          "if s.alerter != nil {" in ia)
    # the skip must produce NO runtime signal: no log, no counter, no error
    skipped_block = ia.split("if s.alerter != nil {")[1].split("\t}")[0]
    check("the skip produces no log line, counter, or error at runtime",
          "log" not in skipped_block and "++" not in skipped_block
          and "err" not in skipped_block)

    print("-- is the default safe? --")
    check("DefaultConfig enables the log sink (Log: true)",
          "Alerts:       Alerts{Log: true}" in cfg)
    check("a missing config file still yields those defaults",
          "if os.IsNotExist(err) {" in cfg)

    print("-- is there any validation that could catch log:false + no sink? --")
    # absence of MECHANISM: a Validate method, or an error returned for zero sinks
    has_validate = any(sig in cfg for sig in
                       ("func (c Config) Validate(", "func (c *Config) Validate("))
    check(f"Config has no Validate() at all (found={has_validate})", not has_validate)
    check("Alerter() returns no error for the zero-sink case",
          ") (Alerter, error)" not in cfg)

    print("-- the one startup line --")
    check("main logs 'alerting disabled' once at boot and nothing after",
          "alerting disabled" in main_go)
    check("no runtime 'alert suppressed' path exists anywhere",
          "suppress" not in svc.lower())

    if fails:
        print(f"\nALERT WIRING RED: {fails}")
        return 1

    print("\n=> Out of the box this is fine: the default config turns the log sink on.")
    print("   The footgun is that `alerts: {log: false}` -- the natural thing to")
    print("   write while setting up a webhook you have not finished -- composes NO")
    print("   sink, returns nil, and the service then ingests and retains every")
    print("   event while dropping 100% of alerts after ONE startup line. Nothing")
    print("   at runtime says a detection was suppressed.")
    print("   Fix, Dad's call: refuse to start (or warn loudly at every N alerts)")
    print("   when zero sinks are configured but ingest is enabled. A detector with")
    print("   no output is worse than no detector, because it reports healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

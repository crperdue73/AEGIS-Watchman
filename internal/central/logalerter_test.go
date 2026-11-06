package central

// LogAlerter is the DEFAULT sink: config.go:254 appends it whenever no external system is
// configured, so in a bare deployment this is the only thing that ever runs when something is
// detected. It had no test at all (beat 788) -- the alert path that requires no configuration was
// the alert path nothing checked.
//
// The defect it *would* hide is quiet, which is why it is worth asserting: an alerter that swallows
// an alert returns nil, the service treats delivery as successful, and the detection disappears with
// no error anywhere. So the tests below assert the line is actually WRITTEN, not that Alert returns
// nil.

import (
	"bytes"
	"context"
	"log"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func logAlertFixture() Alert {
	return Alert{
		Host:  schema.Host{ID: "h-1"},
		Agent: schema.Agent{Root: "/root"},
		Events: []schema.Event{
			{Event: schema.EventIdentityDrift, Severity: schema.SeverityHigh},
			{Event: schema.EventIdentityDrift, Severity: schema.SeverityLow},
		},
	}
}

func TestLogAlerterWritesTheSummary(t *testing.T) {
	var buf bytes.Buffer
	l := &LogAlerter{Logger: log.New(&buf, "", 0)}

	if err := l.Alert(context.Background(), logAlertFixture()); err != nil {
		t.Fatalf("Alert returned %v, want nil", err)
	}

	got := buf.String()
	if !strings.Contains(got, "ALERT: ") {
		t.Fatalf("log output = %q, want it to contain %q -- an alerter that writes nothing "+
			"returns nil and the detection vanishes", got, "ALERT: ")
	}
	// The summary itself must survive into the log: the whole value of this sink is that the
	// operator can read the alert without a JSON viewer.
	want := logAlertFixture().Summary()
	if !strings.Contains(got, want) {
		t.Errorf("log output = %q, want it to contain the Summary %q", got, want)
	}
	// And per the Summary contract, drift is named as drift.
	if !strings.Contains(got, "drift") {
		t.Errorf("log output = %q, want it to name the event class (%q)", got, "drift")
	}
}

// A zero-value LogAlerter must not panic: the struct is exported and a caller constructing
// &LogAlerter{} by hand is legitimate. It falls back to log.Default().
func TestLogAlerterNilLoggerFallsBack(t *testing.T) {
	var buf bytes.Buffer
	prev := log.Default().Writer()
	log.SetOutput(&buf)
	defer log.SetOutput(prev)

	l := &LogAlerter{} // no Logger
	if err := l.Alert(context.Background(), logAlertFixture()); err != nil {
		t.Fatalf("Alert with a nil Logger returned %v, want nil", err)
	}
	if !strings.Contains(buf.String(), "ALERT: ") {
		t.Errorf("with a nil Logger nothing reached log.Default(): got %q", buf.String())
	}
}

// An empty alert is still an alert. Summary renders "0 event(s)", and dropping it silently would be
// the same failure mode as dropping a real one.
func TestLogAlerterWritesEvenAnEmptyAlert(t *testing.T) {
	var buf bytes.Buffer
	l := &LogAlerter{Logger: log.New(&buf, "", 0)}

	if err := l.Alert(context.Background(), Alert{Host: schema.Host{ID: "h-1"}}); err != nil {
		t.Fatalf("Alert returned %v, want nil", err)
	}
	if !strings.Contains(buf.String(), "ALERT: ") {
		t.Errorf("an empty alert produced no line: %q", buf.String())
	}
}

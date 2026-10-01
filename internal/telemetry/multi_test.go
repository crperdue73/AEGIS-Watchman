package telemetry

import (
	"errors"
	"strings"
	"testing"

	"github.com/CRPerdue/watchman/internal/schema"
)

// stubSource yields a fixed batch, or an error, for composition tests.
type stubSource struct {
	events []Event
	err    error
	calls  int
}

func (s *stubSource) Actions() ([]Event, error) {
	s.calls++
	return s.events, s.err
}

func ev(actionType string) Event {
	return Event{Action: schema.Action{Type: actionType}}
}

func TestMultiSourceConcatenates(t *testing.T) {
	a := &stubSource{events: []Event{ev("process.exec")}}
	b := &stubSource{events: []Event{ev("net.egress"), ev("net.listen")}}
	m := NewMultiSource(a, b)

	got, err := m.Actions()
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("got %d events, want 3", len(got))
	}
	if got[0].Action.Type != "process.exec" || got[1].Action.Type != "net.egress" || got[2].Action.Type != "net.listen" {
		t.Errorf("order not preserved: %+v", got)
	}
}

// One broken backend must not blind the healthy ones — the whole point of D6.
func TestMultiSourceSkipsFailingChild(t *testing.T) {
	good := &stubSource{events: []Event{ev("file.access")}}
	bad := &stubSource{err: errors.New("kernel said no")}
	m := NewMultiSource(bad, good)

	got, err := m.Actions()
	if err != nil {
		t.Fatalf("one failing child should not fail the batch: %v", err)
	}
	if len(got) != 1 || got[0].Action.Type != "file.access" {
		t.Errorf("healthy child's events lost: %+v", got)
	}
}

func TestMultiSourceAllFailIsError(t *testing.T) {
	m := NewMultiSource(
		&stubSource{err: errors.New("a")},
		&stubSource{err: errors.New("b")},
	)
	_, err := m.Actions()
	if err == nil {
		t.Fatal("all children failing should error")
	}
	if !strings.Contains(err.Error(), "all 2 sources failed") {
		t.Errorf("error should summarise: %v", err)
	}
}

// A nil source passed unconditionally must be dropped, not panic.
func TestMultiSourceIgnoresNil(t *testing.T) {
	good := &stubSource{events: []Event{ev("process.exec")}}
	m := NewMultiSource(good, nil)
	if m.Len() != 1 {
		t.Fatalf("nil child kept: Len=%d", m.Len())
	}
	got, err := m.Actions()
	if err != nil || len(got) != 1 {
		t.Errorf("got %+v, err %v", got, err)
	}
}

func TestMultiSourceAddChains(t *testing.T) {
	m := NewMultiSource().Add(&stubSource{events: []Event{ev("a")}}).Add(nil).Add(&stubSource{events: []Event{ev("b")}})
	if m.Len() != 2 {
		t.Fatalf("Len = %d, want 2", m.Len())
	}
}

// Empty composition is a no-op, not an error — a host with no telemetry source
// configured must still run.
func TestMultiSourceEmptyIsNoOp(t *testing.T) {
	m := NewMultiSource()
	got, err := m.Actions()
	if err != nil || got != nil {
		t.Errorf("empty MultiSource = %+v, %v", got, err)
	}
}

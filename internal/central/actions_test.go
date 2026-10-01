package central

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/CRPerdue/watchman/internal/schema"
)

// captureAlerter records the Alerts it receives.
type captureAlerter struct {
	alerts []Alert
}

func (c *captureAlerter) Alert(_ context.Context, a Alert) error {
	c.alerts = append(c.alerts, a)
	return nil
}

func ruleMatchEvent(host, ruleID string) schema.Event {
	ev := schema.NewEnvelope(schema.EventBehaviorRuleMatch, schema.Host{ID: host})
	ev.Action = &schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/usr/bin/bash"}}
	ev.Rule = &schema.Rule{ID: ruleID, Pack: "carbonato", Version: "1"}
	ev.Severity = schema.SeverityHigh
	return *ev
}

func TestIngestActionsAlerts(t *testing.T) {
	svc := NewService(NewMemoryStore())
	cap := &captureAlerter{}
	svc.SetAlerter(cap)

	n, err := svc.IngestActions(context.Background(), host, agent,
		[]schema.Event{ruleMatchEvent("h-1", "carbonato.exec.shell_spawn")})
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("accepted = %d, want 1", n)
	}
	if len(cap.alerts) != 1 {
		t.Fatalf("alerts = %d, want 1", len(cap.alerts))
	}
	a := cap.alerts[0]
	if len(a.Events) != 1 || a.Events[0].Event != schema.EventBehaviorRuleMatch {
		t.Errorf("alert events = %#v", a.Events)
	}
	// host/agent are stamped even though the event carried only a host id.
	if a.Agent.ID != agent.ID {
		t.Errorf("alert agent = %q, want %q", a.Agent.ID, agent.ID)
	}
}

func TestIngestActionsEmptyIsNoOp(t *testing.T) {
	svc := NewService(NewMemoryStore())
	cap := &captureAlerter{}
	svc.SetAlerter(cap)
	n, err := svc.IngestActions(context.Background(), host, agent, nil)
	if err != nil || n != 0 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if len(cap.alerts) != 0 {
		t.Error("no events should produce no alert")
	}
}

func TestIngestActionsDropsEmptyEventNames(t *testing.T) {
	svc := NewService(NewMemoryStore())
	cap := &captureAlerter{}
	svc.SetAlerter(cap)
	n, err := svc.IngestActions(context.Background(), host, agent,
		[]schema.Event{{Severity: "high"}, ruleMatchEvent("h-1", "r")})
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("accepted = %d, want 1 (garbage dropped)", n)
	}
}

func TestHTTPActionsRoundTrip(t *testing.T) {
	svc := NewService(NewMemoryStore())
	cap := &captureAlerter{}
	svc.SetAlerter(cap)
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	body, _ := json.Marshal(ActionsRequest{
		Host:   host,
		Agent:  agent,
		Events: []schema.Event{ruleMatchEvent("h-1", "carbonato.exec.ssh_key_heredoc")},
	})
	resp, err := http.Post(srv.URL+"/api/v1/actions", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("status = %d, want 202", resp.StatusCode)
	}
	var out map[string]int
	_ = json.NewDecoder(resp.Body).Decode(&out)
	if out["accepted"] != 1 {
		t.Errorf("accepted = %d, want 1", out["accepted"])
	}
	if len(cap.alerts) != 1 {
		t.Errorf("alerts = %d, want 1", len(cap.alerts))
	}
}

func TestHTTPActionsRejectsBadInput(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	// GET not allowed.
	resp, err := http.Get(srv.URL + "/api/v1/actions")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET status = %d, want 405", resp.StatusCode)
	}

	// Missing host.id.
	b, _ := json.Marshal(ActionsRequest{Events: []schema.Event{ruleMatchEvent("h", "r")}})
	resp, err = http.Post(srv.URL+"/api/v1/actions", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("missing host status = %d, want 400", resp.StatusCode)
	}

	// No events.
	b, _ = json.Marshal(ActionsRequest{Host: host})
	resp, err = http.Post(srv.URL+"/api/v1/actions", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("no-events status = %d, want 400", resp.StatusCode)
	}
}

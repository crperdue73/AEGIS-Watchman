package central

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

// TestFleetEndpoint proves the P2.1 read side end to end: an ingested action
// surfaces on GET /api/v1/fleet, rolled up by host.
func TestFleetEndpoint(t *testing.T) {
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)

	host := schema.Host{ID: "builder-01", Name: "builder"}
	agent := schema.Agent{Root: "/home/agent"}
	_, err := svc.IngestActions(context.Background(), host, agent, []schema.Event{{
		Event: schema.EventBehaviorRuleMatch, Severity: schema.SeverityCritical, Host: host,
		Rule:   &schema.Rule{ID: "carbonato.container.privileged_start", Pack: "carbonato"},
		Action: &schema.Action{Type: "container.start"},
	}})
	if err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/v1/fleet")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	var view fleetView
	if err := json.NewDecoder(resp.Body).Decode(&view); err != nil {
		t.Fatal(err)
	}
	if view.TotalEvents != 1 || len(view.Hosts) != 1 {
		t.Fatalf("view = %+v", view)
	}
	if view.Hosts[0].HostID != "builder-01" || view.Hosts[0].MaxSeverity != "critical" {
		t.Errorf("host rollup = %+v", view.Hosts[0])
	}
	if len(view.Hosts[0].Recent) != 1 || view.Hosts[0].Recent[0].RuleID != "carbonato.container.privileged_start" {
		t.Errorf("recent = %+v", view.Hosts[0].Recent)
	}
}

// TestFleetEndpointEmptyWithoutStore: no record store configured returns an
// empty (valid) view rather than a 500 — the honest answer.
func TestFleetEndpointEmptyWithoutStore(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/v1/fleet")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var view fleetView
	if err := json.NewDecoder(resp.Body).Decode(&view); err != nil {
		t.Fatal(err)
	}
	if len(view.Hosts) != 0 || view.TotalEvents != 0 {
		t.Errorf("expected empty view, got %+v", view)
	}
}

// TestFleetEndpointHostFilter checks the ?host= filter.
func TestFleetEndpointHostFilter(t *testing.T) {
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)
	ctx := context.Background()
	now := time.Now().UTC()
	_ = rs.Append(ctx, Record{HostID: "h1", Severity: "high", When: now})
	_ = rs.Append(ctx, Record{HostID: "h2", Severity: "low", When: now})

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/v1/fleet?host=h1")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var view fleetView
	if err := json.NewDecoder(resp.Body).Decode(&view); err != nil {
		t.Fatal(err)
	}
	if len(view.Hosts) != 1 || view.Hosts[0].HostID != "h1" {
		t.Errorf("host filter failed: %+v", view.Hosts)
	}
}

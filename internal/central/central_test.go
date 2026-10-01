package central

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/CRPerdue/watchman/internal/collector"
	"github.com/CRPerdue/watchman/internal/schema"
)

func inv(files ...collector.FileState) *collector.Inventory {
	return &collector.Inventory{Root: "/home/x/.openclaw/workspace-x", Files: files}
}

func fs(path, sum string, size int64) collector.FileState {
	return collector.FileState{Path: path, SHA256: sum, Size: size}
}

var (
	host  = schema.Host{ID: "h-1", Name: "builder-01"}
	agent = schema.Agent{ID: "vivi", Root: "/home/x/.openclaw/workspace-x"}
)

func TestIngestFirstSeenIsSilent(t *testing.T) {
	svc := NewService(NewMemoryStore())

	res, err := svc.Ingest(context.Background(), host, agent, inv(fs("SOUL.md", "aaa", 10)))
	if err != nil {
		t.Fatal(err)
	}
	if !res.FirstSeen {
		t.Error("expected FirstSeen=true on first ingest")
	}
	if len(res.Events) != 0 {
		t.Errorf("expected no events on enrollment, got %d", len(res.Events))
	}
}

func TestIngestDetectsDriftAndRebaselines(t *testing.T) {
	svc := NewService(NewMemoryStore())
	ctx := context.Background()

	// Enroll.
	if _, err := svc.Ingest(ctx, host, agent, inv(fs("SOUL.md", "aaa", 10))); err != nil {
		t.Fatal(err)
	}

	// The CARBONATO TTP: persona file edited.
	res, err := svc.Ingest(ctx, host, agent, inv(fs("SOUL.md", "bbb", 25)))
	if err != nil {
		t.Fatal(err)
	}
	if res.FirstSeen {
		t.Error("second ingest should not be FirstSeen")
	}
	if len(res.Events) != 1 {
		t.Fatalf("want 1 drift event, got %d", len(res.Events))
	}
	if res.Events[0].Severity != schema.SeverityHigh {
		t.Errorf("SOUL.md drift should be high severity, got %q", res.Events[0].Severity)
	}

	// A third identical ingest must be silent — baseline was re-recorded.
	res3, err := svc.Ingest(ctx, host, agent, inv(fs("SOUL.md", "bbb", 25)))
	if err != nil {
		t.Fatal(err)
	}
	if len(res3.Events) != 0 {
		t.Errorf("expected no events after rebaseline, got %d", len(res3.Events))
	}
}

func TestIngestEventsAreOrderedByPath(t *testing.T) {
	svc := NewService(NewMemoryStore())
	ctx := context.Background()

	if _, err := svc.Ingest(ctx, host, agent, inv(
		fs("SOUL.md", "1", 1),
		fs("skills/a.md", "2", 2),
		fs("skills/z.md", "3", 3),
	)); err != nil {
		t.Fatal(err)
	}

	res, err := svc.Ingest(ctx, host, agent, inv(
		fs("skills/z.md", "3", 3), // unchanged
		fs("SOUL.md", "9", 1),     // changed
		fs("skills/a.md", "8", 2), // changed
	))
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Events) != 2 {
		t.Fatalf("want 2 events, got %d", len(res.Events))
	}
	if res.Events[0].File.Path != "SOUL.md" || res.Events[1].File.Path != "skills/a.md" {
		t.Errorf("events not path-sorted: %s, %s",
			res.Events[0].File.Path, res.Events[1].File.Path)
	}
}

func TestMemoryStoreReturnsCopies(t *testing.T) {
	st := NewMemoryStore()
	key := AgentKey{HostID: "h-1", Root: "/x"}
	orig := inv(fs("SOUL.md", "aaa", 10))
	if err := st.PutBaseline(context.Background(), key, orig); err != nil {
		t.Fatal(err)
	}
	got, ok, err := st.GetBaseline(context.Background(), key)
	if err != nil || !ok {
		t.Fatalf("get: ok=%v err=%v", ok, err)
	}
	got.Files[0].SHA256 = "MUTATED"
	again, _, _ := st.GetBaseline(context.Background(), key)
	if again.Files[0].SHA256 != "aaa" {
		t.Error("store returned a mutable reference; copy leaked")
	}
}

func TestHTTPIngestRoundTrip(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	post := func(body InventoryRequest) *http.Response {
		b, _ := json.Marshal(body)
		resp, err := http.Post(srv.URL+"/api/v1/events", "application/json", bytes.NewReader(b))
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}

	// 1) enroll
	resp := post(InventoryRequest{Host: host, Agent: agent, Inv: inv(fs("SOUL.md", "aaa", 10))})
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("enroll status = %d", resp.StatusCode)
	}
	var r1 IngestResult
	_ = json.NewDecoder(resp.Body).Decode(&r1)
	resp.Body.Close()
	if !r1.FirstSeen || len(r1.Events) != 0 {
		t.Errorf("enroll result = %+v", r1)
	}

	// 2) drift
	resp = post(InventoryRequest{Host: host, Agent: agent, Inv: inv(fs("SOUL.md", "bbb", 30))})
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("drift status = %d", resp.StatusCode)
	}
	var r2 IngestResult
	_ = json.NewDecoder(resp.Body).Decode(&r2)
	resp.Body.Close()
	if len(r2.Events) != 1 {
		t.Fatalf("want 1 event, got %d", len(r2.Events))
	}
	if r2.Events[0].Schema != schema.Version {
		t.Errorf("event schema = %q", r2.Events[0].Schema)
	}
}

func TestHTTPIngestRejectsBadInput(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	// GET not allowed.
	resp, err := http.Get(srv.URL + "/api/v1/events")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET status = %d, want 405", resp.StatusCode)
	}

	// Missing host.id.
	b, _ := json.Marshal(InventoryRequest{Agent: agent, Inv: inv()})
	resp, err = http.Post(srv.URL+"/api/v1/events", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("missing host status = %d, want 400", resp.StatusCode)
	}

	// Health.
	resp, err = http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("health status = %d", resp.StatusCode)
	}
}

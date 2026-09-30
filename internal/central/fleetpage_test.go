package central

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func readAll(t *testing.T, r io.Reader) string {
	t.Helper()
	b, err := io.ReadAll(r)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestFleetPageRendersHosts(t *testing.T) {
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)
	now := time.Now().UTC()
	_ = rs.Append(context.Background(), Record{
		HostID: "builder-01", HostName: "builder", Severity: "critical",
		Technique: "phase3/scope-expansion", RuleID: "carbonato.container.privileged_start",
		RulePack: "carbonato", RuleVersion: "7",
		When: now,
	})

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.Contains(ct, "text/html") {
		t.Errorf("content-type = %q, want text/html", ct)
	}
	body := readAll(t, resp.Body)
	for _, want := range []string{"builder-01", "critical", "carbonato.container.privileged_start", "sev-critical", "By pack", "carbonato@7"} {
		if !strings.Contains(body, want) {
			t.Errorf("page missing %q", want)
		}
	}
}

// TestFleetPageEscapesHostInput is the security test: a host name is attacker-
// influenced data (it comes from the collector's submission). html/template
// must neutralise it, or a fleet view becomes an XSS vector.
// TestFleetPageMarksDerivedSignals: a record whose signal was derived
// (action_source set) must be visibly marked on the page, so an operator does
// not read a heuristic scope.change as ground truth.
func TestFleetPageMarksDerivedSignals(t *testing.T) {
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)
	_ = rs.Append(context.Background(), Record{
		HostID: "h1", Severity: "high",
		RuleID: "carbonato.tool.scope_new", ActionSource: "heuristic",
		When: time.Now().UTC(),
	})

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body := readAll(t, resp.Body)
	if !strings.Contains(body, "By source") {
		t.Errorf("page missing the By source column; got:\n%s", body)
	}
	if !strings.Contains(body, `class="derived"`) || !strings.Contains(body, "heuristic") {
		t.Errorf("derived signal not marked on the page; got:\n%s", body)
	}
}

func TestFleetPageEscapesHostInput(t *testing.T) {
	svc := NewService(NewMemoryStore())
	rs := NewMemoryRecordStore(DefaultRecordRetention)
	svc.SetRecordStore(rs)
	_ = rs.Append(context.Background(), Record{
		HostID:   "h1",
		HostName: `<script>alert(1)</script>`,
		Severity: "high",
		When:     time.Now().UTC(),
	})

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body := readAll(t, resp.Body)
	if strings.Contains(body, "<script>alert(1)</script>") {
		t.Fatal("host name was NOT escaped — XSS in the fleet view")
	}
	if !strings.Contains(body, "&lt;script&gt;") {
		t.Errorf("expected escaped script tag in output; got:\n%s", body)
	}
}

func TestFleetPageEmptyState(t *testing.T) {
	svc := NewService(NewMemoryStore())
	// No record store: the page must still render, saying so honestly.
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	body := readAll(t, resp.Body)
	if !strings.Contains(body, "No events retained") {
		t.Errorf("empty state not shown; got:\n%s", body)
	}
}

func TestFleetPageOnlyRoot(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/nope")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("status for /nope = %d, want 404", resp.StatusCode)
	}
}

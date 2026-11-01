package central

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// TestAgentsPageRendersRoster is the point of the page: the identity roster a
// human can read — every agent, its baseline, its Layer 2 trust verdict — as a
// second view of GET /api/v1/agents, not a second implementation.
func TestAgentsPageRendersRoster(t *testing.T) {
	svc := NewService(NewMemoryStore())
	reg, err := NewManifestRegistry("", nil)
	if err != nil {
		t.Fatal(err)
	}
	reg.Set("/r/approved", &provenance.Manifest{
		Version: provenance.ManifestVersion,
		AgentID: "a",
		Root:    "/r/approved",
		Files:   map[string]string{"SOUL.md": "goodhash0000", "config.json": "want"},
	})
	svc.SetManifests(reg)
	ctx := context.Background()

	if _, err := svc.Ingest(ctx, schema.Host{ID: "builder-01"}, schema.Agent{ID: "a"}, rosterInv("/r/approved",
		fs("SOUL.md", "goodhash0000", 1024), // listed, matches -> ok
		fs("config.json", "other", 2048),    // listed, differs -> modified
		fs("skills/new.md", "z", 3),         // on host, unlisted -> unsigned
	)); err != nil {
		t.Fatal(err)
	}
	// A second root with no manifest -> unverified + manifest "no".
	if _, err := svc.Ingest(ctx, schema.Host{ID: "builder-02"}, schema.Agent{ID: "b"}, rosterInv("/r/plain",
		fs("SOUL.md", "x", 5))); err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/agents")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.Contains(ct, "text/html") {
		t.Errorf("content-type = %q, want text/html", ct)
	}
	body := readAll(t, resp.Body)
	for _, want := range []string{
		"identity roster",
		"builder-01", "builder-02",
		"/r/approved", "/r/plain",
		"SOUL.md", "config.json",
		`class="trust trust-ok"`,
		`class="trust trust-modified"`,
		`class="trust trust-unsigned"`,
		`class="trust trust-unverified"`,
		"goodhash0000", // hash is shown (its own column), shortened
		"manifest-no",  // the "no manifest" marker for /r/plain
	} {
		if !strings.Contains(body, want) {
			t.Errorf("roster page missing %q", want)
		}
	}
	// The page must point back to the machine-readable endpoint and the fleet
	// view, so the two views are discoverable from each other.
	if !strings.Contains(body, `href="/api/v1/agents"`) || !strings.Contains(body, `href="/"`) {
		t.Errorf("roster page missing navigation links")
	}
}

// TestAgentsPageNeverShipsContent mirrors the endpoint's guard: the page is
// metadata, not a way to read every agent's SOUL.md in a browser.
func TestAgentsPageNeverShipsContent(t *testing.T) {
	svc := NewService(NewMemoryStore())
	body := collector.FileState{Path: "SOUL.md", SHA256: "x", Size: 1, Content: "SECRET-PERSONA-BODY"}
	if _, err := svc.Ingest(context.Background(), schema.Host{ID: "h"}, schema.Agent{ID: "a"},
		rosterInv("/r/a", body)); err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/agents")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if got := readAll(t, resp.Body); strings.Contains(got, "SECRET-PERSONA-BODY") {
		t.Errorf("roster page leaked file content: %s", got)
	}
}

// TestAgentsPageEscapesHostInput is the security test: host ids and file paths
// come from a collector's submission. html/template must neutralise them, or the
// roster becomes an XSS vector.
func TestAgentsPageEscapesHostInput(t *testing.T) {
	svc := NewService(NewMemoryStore())
	if _, err := svc.Ingest(context.Background(),
		schema.Host{ID: `<script>alert(1)</script>`}, schema.Agent{ID: "a"},
		rosterInv("/r/a", fs(`<img src=x onerror=alert(2)>.md`, "s", 1))); err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/agents")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	got := readAll(t, resp.Body)
	if strings.Contains(got, "<script>alert(1)</script>") || strings.Contains(got, "<img src=x") {
		t.Fatal("host/path input was NOT escaped — XSS in the roster view")
	}
	if !strings.Contains(got, "&lt;script&gt;") {
		t.Errorf("expected escaped script tag in output; got:\n%s", got)
	}
}

// TestAgentsPageEmptyState: with no baselines the page still renders and says so
// honestly, exactly like the fleet page's empty state.
func TestAgentsPageEmptyState(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/agents")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if body := readAll(t, resp.Body); !strings.Contains(body, "No baselines on record") {
		t.Errorf("empty state not shown; got:\n%s", body)
	}
}

// TestAgentsPageFilters: the page honours the same host=/root= query params as
// the JSON endpoint, because both share buildAgentsView.
func TestAgentsPageFilters(t *testing.T) {
	svc := NewService(NewMemoryStore())
	ctx := context.Background()
	for _, h := range []string{"h-1", "h-2"} {
		if _, err := svc.Ingest(ctx, schema.Host{ID: h}, schema.Agent{ID: "a"},
			rosterInv("/r/"+h, fs("SOUL.md", "s", 1))); err != nil {
			t.Fatal(err)
		}
	}
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/agents?host=h-2")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body := readAll(t, resp.Body)
	if !strings.Contains(body, "h-2") || strings.Contains(body, "h-1") {
		t.Errorf("host filter not applied to the page; got:\n%s", body)
	}
}

// TestAgentsPageMethodNotAllowed: the page is a read view; a POST is refused the
// same way the JSON endpoint refuses it.
func TestAgentsPageMethodNotAllowed(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/agents", "text/plain", strings.NewReader("x"))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("POST status = %d, want 405", resp.StatusCode)
	}
}

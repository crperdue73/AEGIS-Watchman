package central

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

func rosterInv(root string, files ...collector.FileState) *collector.Inventory {
	return &collector.Inventory{Root: root, Files: files}
}

// decodeAgents GETs /api/v1/agents (with the given query) and decodes the view.
func decodeAgents(t *testing.T, srv *httptest.Server, query string) agentsView {
	t.Helper()
	resp, err := http.Get(srv.URL + "/api/v1/agents" + query)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var view agentsView
	if err := json.NewDecoder(resp.Body).Decode(&view); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return view
}

// TestAgentsRosterListsEveryBaseline is the whole point of the endpoint: the
// fleet's identity roster is queryable — every agent, its baseline — not just
// the event rollup that /api/v1/fleet returns.
func TestAgentsRosterListsEveryBaseline(t *testing.T) {
	svc := NewService(NewMemoryStore())
	ctx := context.Background()

	// Two agents on two hosts, three files total.
	if _, err := svc.Ingest(ctx, schema.Host{ID: "h-2"}, schema.Agent{ID: "b"},
		rosterInv("/r/b", fs("SOUL.md", "s2", 2), fs("skills/a.md", "a2", 3))); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Ingest(ctx, schema.Host{ID: "h-1"}, schema.Agent{ID: "a"},
		rosterInv("/r/a", fs("SOUL.md", "s1", 1))); err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	view := decodeAgents(t, srv, "")
	if view.TotalAgents != 2 {
		t.Fatalf("total_agents = %d, want 2", view.TotalAgents)
	}
	if view.TotalFiles != 3 {
		t.Errorf("total_files = %d, want 3", view.TotalFiles)
	}
	// Sorted by host id, so the roster is stable run to run.
	if view.Agents[0].HostID != "h-1" || view.Agents[1].HostID != "h-2" {
		t.Errorf("agents not host-sorted: %s, %s", view.Agents[0].HostID, view.Agents[1].HostID)
	}
	// Files within an entry are path-sorted.
	if got := view.Agents[1].Files; got[0].Path != "SOUL.md" || got[1].Path != "skills/a.md" {
		t.Errorf("files not path-sorted: %+v", got)
	}
	if view.Agents[1].Files[0].SHA256 != "s2" {
		t.Errorf("hash not carried through: %+v", view.Agents[1].Files[0])
	}
}

// TestAgentsRosterNeverShipsContent guards the deliberate omission: the roster is
// metadata, not a way to read every agent's SOUL.md over the network.
func TestAgentsRosterNeverShipsContent(t *testing.T) {
	svc := NewService(NewMemoryStore())
	body := collector.FileState{Path: "SOUL.md", SHA256: "x", Size: 1, Content: "SECRET-PERSONA-BODY"}
	if _, err := svc.Ingest(context.Background(), schema.Host{ID: "h"}, schema.Agent{ID: "a"},
		rosterInv("/r/a", body)); err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/v1/agents")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "SECRET-PERSONA-BODY") || strings.Contains(string(raw), "content") {
		t.Errorf("roster leaked file content: %s", raw)
	}
}

func TestAgentsRosterFilters(t *testing.T) {
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

	if v := decodeAgents(t, srv, "?host=h-2"); v.TotalAgents != 1 || v.Agents[0].HostID != "h-2" {
		t.Errorf("host filter: %+v", v)
	}
	if v := decodeAgents(t, srv, "?root=/r/h-1"); v.TotalAgents != 1 || v.Agents[0].HostID != "h-1" {
		t.Errorf("root filter: %+v", v)
	}
	if v := decodeAgents(t, srv, "?host=nope"); v.TotalAgents != 0 {
		t.Errorf("no-match filter should be empty, got %+v", v)
	}
}

func TestAgentsRosterMethodNotAllowed(t *testing.T) {
	svc := NewService(NewMemoryStore())
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/api/v1/agents", "application/json", strings.NewReader("{}"))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("POST status = %d, want 405", resp.StatusCode)
	}
}

// TestFileStoreListsBaselines proves the roster is not a memory-only feature:
// a durable baseline is listable after reopen, key and all.
func TestFileStoreListsBaselines(t *testing.T) {
	path := filepath.Join(t.TempDir(), "baselines.json")
	ctx := context.Background()

	st, err := NewFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	key := AgentKey{HostID: "h-1", Root: "/r/a"}
	if err := st.PutBaseline(ctx, key, rosterInv("/r/a", fs("SOUL.md", "s", 1))); err != nil {
		t.Fatal(err)
	}

	// Reopen: the list must survive a restart.
	st2, err := NewFileStore(path)
	if err != nil {
		t.Fatal(err)
	}
	list, err := st2.ListBaselines(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 || list[0].Key != key {
		t.Fatalf("list after reopen = %+v, want the one key %v", list, key)
	}
	if len(list[0].Files) != 1 || list[0].Files[0].Path != "SOUL.md" {
		t.Errorf("baseline files not round-tripped: %+v", list[0].Files)
	}
}

// TestAgentsRosterJoinsLayer2Trust proves the roster answers "is every agent's
// identity approved", not just "what is it": a file in the approved manifest
// reads ok, a mismatch reads modified, an unlisted file reads unsigned, and a
// root with no manifest reads unverified.
func TestAgentsRosterJoinsLayer2Trust(t *testing.T) {
	svc := NewService(NewMemoryStore())
	reg, err := NewManifestRegistry("", nil)
	if err != nil {
		t.Fatal(err)
	}
	reg.Set("/r/approved", &provenance.Manifest{
		Version: provenance.ManifestVersion,
		AgentID: "a",
		Root:    "/r/approved",
		Files:   map[string]string{"SOUL.md": "good", "config.json": "want"},
	})
	svc.SetManifests(reg)
	ctx := context.Background()

	if _, err := svc.Ingest(ctx, schema.Host{ID: "h"}, schema.Agent{ID: "a"}, rosterInv("/r/approved",
		fs("SOUL.md", "good", 1),      // listed, matches -> ok
		fs("config.json", "other", 2), // listed, differs -> modified
		fs("skills/new.md", "z", 3),   // unlisted         -> unsigned
	)); err != nil {
		t.Fatal(err)
	}
	// A second agent root with no manifest -> unverified.
	if _, err := svc.Ingest(ctx, schema.Host{ID: "h"}, schema.Agent{ID: "b"}, rosterInv("/r/plain",
		fs("SOUL.md", "x", 4))); err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()
	view := decodeAgents(t, srv, "")

	byRoot := map[string]agentRosterEntry{}
	for _, a := range view.Agents {
		byRoot[a.Root] = a
	}
	approved := byRoot["/r/approved"]
	if !approved.Manifest {
		t.Error("approved root should report manifest=true")
	}
	got := map[string]string{}
	for _, f := range approved.Files {
		got[f.Path] = f.Trust
	}
	want := map[string]string{"SOUL.md": "ok", "config.json": "modified", "skills/new.md": "unsigned"}
	for p, w := range want {
		if got[p] != w {
			t.Errorf("trust[%s] = %q, want %q", p, got[p], w)
		}
	}

	plain := byRoot["/r/plain"]
	if plain.Manifest {
		t.Error("root with no manifest should report manifest=false")
	}
	if plain.Files[0].Trust != "unverified" {
		t.Errorf("unapproved root trust = %q, want unverified", plain.Files[0].Trust)
	}
}

// TestParseAgentKey pins the key round-trip, including a colon-bearing host id
// (IPv6) where a naive "split on first colon" would corrupt the root.
func TestParseAgentKey(t *testing.T) {
	for _, tc := range []struct{ in, host, root string }{
		{"h-1:/r/a", "h-1", "/r/a"},
		{"fe80::1:/r/a", "fe80::1", "/r/a"},
		{"host:/nested/root/x", "host", "/nested/root/x"},
	} {
		got, ok := parseAgentKey(tc.in)
		if !ok || got.HostID != tc.host || got.Root != tc.root {
			t.Errorf("parseAgentKey(%q) = %+v,%v; want %s,%s", tc.in, got, ok, tc.host, tc.root)
		}
	}
}

package central

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// The HTTP surface, declared once. The test below proves this list against two
// independent sources: the mux that actually serves it, and the endpoint table
// in docs/deploy-central.md. That table is hand-maintained, and a hand-kept list
// of routes rots the moment a route is added — as it did when `/agents` landed
// and the table had to be edited by memory. This guard is the same species as
// the collector-config and rule-table guards: hold a document to its claim.
type endpoint struct{ method, path string }

var centralEndpoints = []endpoint{
	{"GET", "/healthz"},
	{"POST", "/api/v1/events"},
	{"POST", "/api/v1/actions"},
	{"GET", "/api/v1/fleet"},
	{"GET", "/api/v1/agents"},
	{"GET", "/agents"},
	{"GET", "/"},
}

func TestCentralEndpointsMatchDoc(t *testing.T) {
	// (1) Every declared endpoint is actually served by the mux, at its own
	// pattern — not merely swept up by the "/" catch-all. (The catch-all means a
	// bare empty-pattern check would never fire; the exact registration must win.)
	mux := NewService(NewMemoryStore()).Handler()
	for _, ep := range centralEndpoints {
		req := httptest.NewRequest(ep.method, ep.path, nil)
		_, pattern := mux.Handler(req)
		if pattern != ep.path {
			t.Errorf("endpoint %s %s resolves to mux pattern %q, want %q — is it registered?",
				ep.method, ep.path, pattern, ep.path)
		}
	}

	// (2) The doc table lists exactly these endpoints — no more, no less.
	raw, err := os.ReadFile(filepath.Join("..", "..", "docs", "deploy-central.md"))
	if err != nil {
		t.Fatalf("read docs/deploy-central.md: %v", err)
	}
	rows := parseEndpointTable(string(raw))
	if len(rows) < 5 {
		t.Fatalf("parsed only %d doc row(s) — the parser is broken, fix it before trusting a pass", len(rows))
	}

	declared := map[string]bool{}
	for _, ep := range centralEndpoints {
		declared[ep.method+" "+ep.path] = true
	}
	documented := map[string]bool{}
	for _, ep := range rows {
		documented[ep.method+" "+ep.path] = true
	}

	var missing, extra []string
	for k := range declared {
		if !documented[k] {
			missing = append(missing, k)
		}
	}
	for k := range documented {
		if !declared[k] {
			extra = append(extra, k)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)
	if len(missing) > 0 {
		t.Errorf("docs/deploy-central.md endpoint table omits served endpoints: %v", missing)
	}
	if len(extra) > 0 {
		t.Errorf("docs/deploy-central.md endpoint table documents routes central does not serve: %v", extra)
	}
}

// endpointRowRE matches a Markdown table row whose first two cells are a
// back-ticked method and path, e.g. "| `GET` | `/agents` | … |".
var endpointRowRE = regexp.MustCompile("^\\|\\s*`([A-Z]+)`\\s*\\|\\s*`([^`]+)`\\s*\\|")

func parseEndpointTable(doc string) []endpoint {
	var out []endpoint
	for _, line := range strings.Split(doc, "\n") {
		if m := endpointRowRE.FindStringSubmatch(line); m != nil {
			out = append(out, endpoint{method: m[1], path: m[2]})
		}
	}
	return out
}

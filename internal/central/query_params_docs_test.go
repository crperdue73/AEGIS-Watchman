package central

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// The query-parameter contract, declared once — the same species as the
// endpoint guard in endpoints_docs_test.go, one level deeper. A route can be
// documented and still have a filter that rots: a param parsed but never
// written down, or a filter the docs promise that the handler never reads.
//
// The declared set here is the contract; two tests hold it against the two
// things that can drift from it — the Go source that parses the params, and the
// operator doc that promises them.
var endpointQueryParams = map[string][]string{
	"/api/v1/fleet":  {"host", "since", "min_severity", "limit", "recent"},
	"/api/v1/agents": {"host", "root"},
	"/agents":        {"host", "root"},
	"/":              {}, // the fleet page reads no query params
}

func declaredParamSet() map[string]bool {
	set := map[string]bool{}
	for _, params := range endpointQueryParams {
		for _, p := range params {
			set[p] = true
		}
	}
	return set
}

// TestQueryParamsMatchSource proves the parameters the docs declare are exactly
// the ones the handlers read off r.URL.Query(). It scans the package's non-test
// Go source for Query().Get("…") calls (the only way this codebase reads a
// query param), so adding or removing a filter in code without touching the
// declared set — and therefore the docs — fails here.
//
// Limit, stated so a pass is trusted: this compares the *union* across the
// package, not per handler. A param read in the wrong handler is not caught;
// a param added to any handler without being declared is.
func TestQueryParamsMatchSource(t *testing.T) {
	parsed := parsedQueryParams(t)

	declared := declaredParamSet()
	var undeclared, unparsed []string
	for k := range parsed {
		if !declared[k] {
			undeclared = append(undeclared, k)
		}
	}
	for k := range declared {
		if !parsed[k] {
			unparsed = append(unparsed, k)
		}
	}
	sort.Strings(undeclared)
	sort.Strings(unparsed)

	if len(parsed) == 0 {
		t.Fatalf("scanned source for Query().Get(\"…\") and found none — the scanner is broken, fix it before trusting a pass")
	}
	if len(undeclared) > 0 {
		t.Errorf("handlers parse query param(s) not in the declared contract (and so undocumented): %v", undeclared)
	}
	if len(unparsed) > 0 {
		t.Errorf("declared contract promises query param(s) no handler reads: %v", unparsed)
	}
}

// TestQueryParamsMatchDoc holds the contract to docs/fleet-view.md: every
// declared param is named in the operator doc, and the fleet endpoint's
// "Query parameters" table lists exactly the fleet params — no more, no less.
func TestQueryParamsMatchDoc(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "docs", "fleet-view.md"))
	if err != nil {
		t.Fatalf("read docs/fleet-view.md: %v", err)
	}
	doc := string(raw)

	for _, params := range endpointQueryParams {
		for _, p := range params {
			if !strings.Contains(doc, "`"+p+"`") {
				t.Errorf("query param %q is declared but not documented in docs/fleet-view.md", p)
			}
		}
	}

	// The fleet table must match exactly — a documented filter the handler does
	// not read is the failure this catches.
	table := parseQueryParamTable(doc)
	if len(table) < 3 {
		t.Fatalf("parsed only %d row(s) from the query-parameter table — parser is broken, fix it before trusting a pass", len(table))
	}
	want := endpointQueryParams["/api/v1/fleet"]
	sort.Strings(want)
	got := append([]string(nil), table...)
	sort.Strings(got)
	if strings.Join(want, ",") != strings.Join(got, ",") {
		t.Errorf("docs/fleet-view.md query-parameter table = %v, want %v", got, want)
	}
}

var queryGetRE = regexp.MustCompile(`Query\(\)\s*\.\s*Get\("([A-Za-z_][A-Za-z0-9_]*)"\)`)

func parsedQueryParams(t *testing.T) map[string]bool {
	t.Helper()
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatalf("read package dir: %v", err)
	}
	set := map[string]bool{}
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		src, err := os.ReadFile(name)
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		for _, m := range queryGetRE.FindAllStringSubmatch(string(src), -1) {
			set[m[1]] = true
		}
	}
	return set
}

// queryParamRowRE matches a Markdown table row whose first cell is a back-ticked
// lowercase param name, e.g. "| `min_severity` | *(none)* | … |".
var queryParamRowRE = regexp.MustCompile("^\\|\\s*`([a-z_]+)`\\s*\\|")

// parseQueryParamTable returns the param names from the "Query parameters"
// table — the one introduced by a line beginning "Query parameters" — and stops
// at the first non-table line after it. Anchoring on the heading keeps this from
// picking up the config-key or backend tables elsewhere in the same file.
func parseQueryParamTable(doc string) []string {
	lines := strings.Split(doc, "\n")
	var out []string
	in := false
	for _, line := range lines {
		if !in {
			if strings.HasPrefix(strings.TrimSpace(line), "Query parameters") {
				in = true
			}
			continue
		}
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, "|") {
			if len(out) > 0 {
				break
			}
			continue // skip the heading → header → separator gap
		}
		if m := queryParamRowRE.FindStringSubmatch(line); m != nil {
			out = append(out, m[1])
		}
	}
	return out
}

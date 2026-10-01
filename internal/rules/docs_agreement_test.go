package rules

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// The shipped pack and its public documentation must agree.
//
// docs/rules.md carries the rule table an operator reads to decide whether the
// tool covers them: one row per rule, its id and its severity. That table is a
// claim about the product, and it is hand-maintained — so it can silently drift
// from the pack it describes. A rule added to the pack but not the table is
// invisible coverage; a table entry with no rule behind it, or a severity that
// no longer matches, is a lie about how loudly an alert should page. Neither is
// caught by any test today, which is exactly the class this guards.
//
// The check is deliberately structural, not prose: it reads the pipe-table rows
// (the columns are the contract), so a reflow of the surrounding text does not
// break it and a new rule must be added to both places to pass.

// tableRow matches a rule-table row: `| `id` <markers> | severity | ...`.
var tableRow = regexp.MustCompile("^\\|\\s*`(carbonato\\.[^`]+)`[^|]*\\|\\s*(info|low|medium|high|critical)\\s*\\|")

// Prose claims docs/rules.md makes about the shipped pack; each is checked
// against the pack below. Kept as regexes so a reflow does not break them, and
// a missing match is a loud failure, not a silent pass.
var (
	docsVersionRE  = regexp.MustCompile(`pack \*\*carbonato\*\*, version \*\*(\d+)\*\*`)
	docsCountRE    = regexp.MustCompile(`\*\*(\d+) rules\*\*`)
	docsCoverageRE = regexp.MustCompile(`(\d+)\s+of\s+the\s+(\d+)\s+rules can fire, and only\s+(\d+)\s+of\s+the\s+(\d+)\s+critical rules`)
)

// TestShippedPackDocsSummaryMatchesPack checks the prose a reader acts on: the
// pack version and rule count stated in docs/rules.md, and — the claim that bit
// heartbeat 86 — the coverage sentence, which must equal what the code computes
// for a stock build rather than a hand-typed number that drifts.
func TestShippedPackDocsSummaryMatchesPack(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatalf("load shipped pack: %v", err)
	}
	raw, err := os.ReadFile(filepath.Join("..", "..", "docs", "rules.md"))
	if err != nil {
		t.Fatalf("read docs/rules.md: %v", err)
	}
	doc := string(raw)

	check := func(name string, re *regexp.Regexp, want string) {
		m := re.FindStringSubmatch(doc)
		if m == nil {
			t.Errorf("docs/rules.md: %s not found — the wording changed and this guard is blind; update the regex", name)
			return
		}
		if got := m[len(m)-1]; got != want {
			t.Errorf("docs/rules.md %s = %q, pack says %q", name, got, want)
		}
	}
	check("version", docsVersionRE, p.Version)
	check("rule count", docsCountRE, strconv.Itoa(len(p.Rules)))

	// The coverage sentence must match the computation for a stock build.
	inert := map[string]bool{}
	for _, r := range InertRules([]*Pack{p}, stockAvailable()) {
		inert[r.ID] = true
	}
	live := len(p.Rules) - len(inert)
	critTotal, critLive := 0, 0
	for _, r := range p.Rules {
		if r.Severity == "critical" {
			critTotal++
			if !inert[r.ID] {
				critLive++
			}
		}
	}
	m := docsCoverageRE.FindStringSubmatch(doc)
	if m == nil {
		t.Fatal("docs/rules.md: coverage summary sentence not found — update this guard to match the new wording")
	}
	want := []string{strconv.Itoa(live), strconv.Itoa(len(p.Rules)), strconv.Itoa(critLive), strconv.Itoa(critTotal)}
	for i, name := range []string{"live rules", "total rules", "live critical", "total critical"} {
		if m[i+1] != want[i] {
			t.Errorf("docs/rules.md coverage summary %s = %s, code computes %s", name, m[i+1], want[i])
		}
	}
}

func TestShippedPackMatchesDocsRuleTable(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatalf("load shipped pack: %v", err)
	}
	doc, err := os.ReadFile(filepath.Join("..", "..", "docs", "rules.md"))
	if err != nil {
		t.Fatalf("read docs/rules.md: %v", err)
	}

	docSev := map[string]string{}
	for _, line := range strings.Split(string(doc), "\n") {
		if m := tableRow.FindStringSubmatch(line); m != nil {
			id, sev := m[1], m[2]
			if prev, dup := docSev[id]; dup {
				t.Errorf("docs/rules.md lists %s twice (severities %q and %q)", id, prev, sev)
			}
			docSev[id] = sev
		}
	}
	if len(docSev) == 0 {
		t.Fatal("no rule rows found in docs/rules.md — the table format changed and this guard is now blind")
	}

	packSev := map[string]string{}
	for _, r := range p.Rules {
		packSev[r.ID] = r.Severity
	}

	// Every rule in the pack has a documented row, and no row lacks a rule.
	var missing, extra, badSev []string
	for id, sev := range packSev {
		ds, ok := docSev[id]
		switch {
		case !ok:
			missing = append(missing, id)
		case ds != sev:
			badSev = append(badSev, id+": docs="+ds+" pack="+sev)
		}
	}
	for id := range docSev {
		if _, ok := packSev[id]; !ok {
			extra = append(extra, id)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)
	sort.Strings(badSev)

	if len(missing) > 0 {
		t.Errorf("shipped in the pack but absent from the docs/rules.md table (invisible coverage): %v", missing)
	}
	if len(extra) > 0 {
		t.Errorf("documented in docs/rules.md but not in the shipped pack: %v", extra)
	}
	if len(badSev) > 0 {
		t.Errorf("severity disagrees between docs/rules.md and the pack (drives paging): %v", badSev)
	}
}

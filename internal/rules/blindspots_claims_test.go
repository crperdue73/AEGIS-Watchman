package rules

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// The README's "Known blind spots" section is a claim, not a courtesy: it says
// what the tool cannot see, keyed by the coverage matrix's gap ids, and it says
// it is "kept honest against PHASE0-COVERAGE.md". Nothing held that promise to
// the matrix — the ids are typed by hand in both places, so the README can cite
// a gap the matrix no longer has, and the matrix can open a new gap the README
// never discloses. Both are the same failure: a blind spot the reader is not
// told about, in the one section a reader trusts to tell them.
//
// So the promise gets a guard, in the same class as readme_claims_test.go (which
// holds the README's pack figures to the pack). It is structural — it reads the
// `Gn` token out of each place and does not parse the prose around it — so a
// reflow of the surrounding text does not break it, and a missing id is a loud
// failure rather than a silent pass.
//
// Scope, stated so a pass is trusted: it checks *which gaps are named*, not what
// the prose says about them. A gap can still be described inaccurately in the
// README and pass; that is a prose review, not a structural one.

var (
	// A gap id anywhere in the README section, e.g. "G3" or "(G5)".
	readmeGapID = regexp.MustCompile(`\bG(\d+)\b`)
	// A coverage-matrix gap section: "### G3 — ...".
	matrixGapHead = regexp.MustCompile(`^### (G\d+)\b(.*)$`)
)

// TestReadmeBlindSpotsMatchCoverageMatrix holds the README's blind-spots list
// and the coverage matrix's gap sections to each other, both directions.
func TestReadmeBlindSpotsMatchCoverageMatrix(t *testing.T) {
	readme := readRepoFile(t, "README.md")
	matrix := readRepoFile(t, "PHASE0-COVERAGE.md")

	section := markdownSection(readme, "## Known blind spots")
	if section == "" {
		t.Fatal("README.md: '## Known blind spots' section not found — this guard is blind; fix the heading")
	}
	cited := map[string]bool{}
	for _, m := range readmeGapID.FindAllStringSubmatch(section, -1) {
		cited["G"+m[1]] = true
	}
	if len(cited) == 0 {
		t.Fatal("README.md 'Known blind spots': no gap id (Gn) found — this guard is blind; fix the wording")
	}

	have := map[string]bool{} // every gap section in the matrix
	open := map[string]bool{} // those not marked CLOSED
	for _, line := range strings.Split(matrix, "\n") {
		m := matrixGapHead.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		have[m[1]] = true
		if !strings.Contains(m[2], "CLOSED") {
			open[m[1]] = true
		}
	}
	if len(have) == 0 {
		t.Fatal("PHASE0-COVERAGE.md: no '### Gn' gap sections found — this guard is blind; fix the heading format")
	}

	// 1. Every id the README cites must have a section in the matrix.
	var stale []string
	for id := range cited {
		if !have[id] {
			stale = append(stale, id)
		}
	}
	if len(stale) > 0 {
		sort.Strings(stale)
		t.Errorf("README.md 'Known blind spots' cites gap id(s) with no coverage-matrix section: %v", stale)
	}

	// 2. Every open gap in the matrix must be disclosed in the README section.
	var undisclosed []string
	for id := range open {
		if !cited[id] {
			undisclosed = append(undisclosed, id)
		}
	}
	if len(undisclosed) > 0 {
		sort.Strings(undisclosed)
		t.Errorf("PHASE0-COVERAGE.md has open gap(s) the README 'Known blind spots' does not disclose: %v — add them to the README or close them in the matrix", undisclosed)
	}
}

// readRepoFile reads a repo-root file, relative to this package's directory.
func readRepoFile(t *testing.T, name string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	return string(b)
}

// markdownSection returns the body of the section that begins at heading, up to
// the next top-level ("## ") heading. Empty if the heading is absent.
func markdownSection(doc, heading string) string {
	i := strings.Index(doc, heading)
	if i < 0 {
		return ""
	}
	rest := doc[i+len(heading):]
	if j := strings.Index(rest, "\n## "); j >= 0 {
		return rest[:j]
	}
	return rest
}

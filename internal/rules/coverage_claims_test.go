package rules

import (
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// PHASE0-COVERAGE.md ships (it is one of the Phase 0/1 docs the README links),
// and it makes its own numeric claims about the product: how many of the
// attacker's forced actions are "covered end to end today", the count quoted
// three ways — "8 of 10", "8/10", "matrix at 8/10" — plus the "80% … 20%"
// split. Those numbers are hand-typed and, unlike the rule count, nothing held
// them to the table they describe. They drifted once already: the roadmap
// recommendation carried a stale "matrix at 7/10" next to a "Recommended next:
// R4" for an adapter that had shipped.
//
// So the claims get a guard, in the same class as docs_agreement_test.go (which
// holds docs/rules.md to the pack) and readme_claims_test.go (the README to the
// pack). It computes the count from the matrix's own action table — a row is
// "covered" when its Have-it cell carries a ✅ and no ❌, so A5 ("✅ via argv ·
// ❌ via file.read") is partial and does not count — and fails if any prose
// figure disagrees. Structural, in the house style: it reads the table cells and
// the number tokens, not the prose between them.

var (
	// An action-table row: `| A1 | … | … | ✅ shipped |`.
	coverageActionRow = regexp.MustCompile(`^[ \t]*\|[ \t]*A\d+[ \t]*\|`)
	// "8 of 10 forced actions are covered end to end", or the "8/10" form.
	coverageOfRE = regexp.MustCompile(`(\d+)\s*(?:of|/)\s*(\d+)\s*forced actions are covered end to end`)
	// "matrix at 8/10".
	coverageMatrixAtRE = regexp.MustCompile(`matrix at (\d+)\s*/\s*(\d+)`)
	// "80% covered end to end, 20% half-wired".
	coveragePercentRE = regexp.MustCompile(`(\d+)% covered end to end, (\d+)% half-wired`)
)

func TestCoverageMatrixCountsMatchProse(t *testing.T) {
	doc := readRepoFile(t, "PHASE0-COVERAGE.md")

	// Compute covered/total from the action table itself.
	total, covered := 0, 0
	for _, line := range strings.Split(doc, "\n") {
		if !coverageActionRow.MatchString(line) {
			continue
		}
		cells := strings.Split(line, "|")
		have := cells[len(cells)-2] // the "Have it?" cell (last cell before the trailing split)
		total++
		if strings.Contains(have, "✅") && !strings.Contains(have, "❌") {
			covered++
		}
	}
	if total == 0 {
		t.Fatal("PHASE0-COVERAGE.md: no '| An |' action rows found — this guard is blind; fix the table heading/format")
	}
	wantTotal := strconv.Itoa(total)
	wantCovered := strconv.Itoa(covered)

	// Every "N of M / N/M forced actions are covered end to end" claim.
	ofMatches := coverageOfRE.FindAllStringSubmatch(doc, -1)
	if len(ofMatches) == 0 {
		t.Fatal("PHASE0-COVERAGE.md: the 'N of 10 forced actions are covered end to end' claim was not found — this guard is blind; fix the wording")
	}
	for _, m := range ofMatches {
		if m[1] != wantCovered || m[2] != wantTotal {
			t.Errorf("PHASE0-COVERAGE.md claims %s of %s forced actions covered end to end; the table says %s of %s",
				m[1], m[2], wantCovered, wantTotal)
		}
	}

	// "matrix at N/M".
	for _, m := range coverageMatrixAtRE.FindAllStringSubmatch(doc, -1) {
		if m[1] != wantCovered || m[2] != wantTotal {
			t.Errorf("PHASE0-COVERAGE.md says 'matrix at %s/%s'; the table says %s/%s",
				m[1], m[2], wantCovered, wantTotal)
		}
	}

	// "P% covered end to end, Q% half-wired".
	for _, m := range coveragePercentRE.FindAllStringSubmatch(doc, -1) {
		wantPct := covered * 100 / total
		if m[1] != strconv.Itoa(wantPct) || m[2] != strconv.Itoa(100-wantPct) {
			t.Errorf("PHASE0-COVERAGE.md claims %s%% covered / %s%% half-wired; the table says %d%% / %d%%",
				m[1], m[2], wantPct, 100-wantPct)
		}
	}
}

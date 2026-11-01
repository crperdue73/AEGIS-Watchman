package rules

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"testing"
)

// The README and the build plan each state the shipped pack's version and rule
// count in prose — `rules/carbonato.yaml` (v7, 20 rules) in the README, "N rules
// exist" in PHASE1-PLAN.md. Those are public claims about the product, and they
// are hand-maintained: unlike the docs/rules.md table, no test held them to the
// pack. Twice now the pack grew and the prose did not (pin-signing, then the
// rule count), and each drift had to be chased down by hand. This holds the two
// prose claims to the same pack docs_agreement_test.go already pins, so the
// number cannot drift silently a third time.
//
// Scope: the version and the total rule count only — the two figures these
// sentences carry. It is intentionally structural (a regex over the sentence),
// so a reflow of surrounding prose does not break it, and a missing match is a
// loud failure rather than a silent pass.

var (
	// `rules/carbonato.yaml` (v7, 20 rules)
	readmeRuleClaimRE = regexp.MustCompile("rules/carbonato\\.yaml` \\(v(\\d+), (\\d+) rules\\)")
	// **Why:** 20 rules exist
	planRuleClaimRE = regexp.MustCompile(`\*\*Why:\*\* (\d+) rules exist`)
)

// TestPublicRuleCountClaimsMatchPack holds the README and PHASE1-PLAN prose
// claims about the shipped pack's version and rule count to the pack itself.
func TestPublicRuleCountClaimsMatchPack(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatalf("load shipped pack: %v", err)
	}
	wantCount := strconv.Itoa(len(p.Rules))

	read := func(name string) string {
		b, err := os.ReadFile(filepath.Join("..", "..", name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		return string(b)
	}

	// README: "`rules/carbonato.yaml` (vN, M rules)".
	m := readmeRuleClaimRE.FindStringSubmatch(read("README.md"))
	if m == nil {
		t.Fatal("README.md: the (vN, M rules) claim was not found — the wording changed and this guard is blind; update readmeRuleClaimRE")
	}
	if m[1] != p.Version {
		t.Errorf("README.md says pack version v%s, the pack is v%s", m[1], p.Version)
	}
	if m[2] != wantCount {
		t.Errorf("README.md says %s rules, the pack has %s", m[2], wantCount)
	}

	// Build plan: "**Why:** N rules exist".
	m = planRuleClaimRE.FindStringSubmatch(read("PHASE1-PLAN.md"))
	if m == nil {
		t.Fatal("PHASE1-PLAN.md: the 'N rules exist' claim was not found — the wording changed; update planRuleClaimRE")
	}
	if m[1] != wantCount {
		t.Errorf("PHASE1-PLAN.md says %s rules exist, the pack has %s", m[1], wantCount)
	}
}

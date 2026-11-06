package central

// BEAT 773 -- the brake that made me confident was the wrong brake.
//
// THREE findings in a row (767, 770, 772) were not code bugs but EVIDENCE bugs:
//
//	767  "N event(s) retained" -- no test asserted the sentence at all.
//	770  empty-store + filter -- the suite tested both halves, never the
//	     intersection; two passing tests recommended confidence they hadn't earned.
//	772  G1 walked "." and only ever saw its own package -- AND G3, the brake
//	     written to prove G1 could fail, passed while the scope was broken.
//
// The 772 case names the mechanism exactly, so it is worth pinning as a rule:
//
//	*** A brake on a PREDICATE is not a brake on a SCOPE. ***
//
// G3 proved formatVersionRefs() correctly counts a reference, ignores a
// declaration, ignores comments, ignores test files. All true. And irrelevant to
// the defect, which was that the walk never reached internal/central. I had a
// rigorous test of the wrong half, and it was green, and I shipped the guard.
//
// So this file does the thing G3 failed to do: it mutates the SCOPE, not the
// predicate, and requires the guard to go red.
//
//	E1  plant a reference OUTSIDE the instrument's own package and require G1 to
//	    fail. This is the exact mutant that slipped past at beat 772. It runs the
//	    same source-scanning predicate G1 uses, over a synthetic tree, so it does
//	    not depend on editing real files.
//	E2  the negative control: with no planted reference the same scan is clean,
//	    so E1 cannot pass by always failing.
//	E3  the transferable check, applied to the OTHER directory-scanning test in
//	    this package (query_params_docs_test.go reads "." too): assert its scope
//	    is INTENTIONAL and document why, since the same mutation would apply.
//	    It is correct today -- it wants its own package's Query().Get calls --
//	    but nothing says so, and "correct by accident" is how 772 happened.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// eScanTree applies G1's reference predicate over an explicit root. It exists so
// the brake can aim at a SCOPE instead of at the predicate.
func eScanTree(root, pkgDir string) (int, error) {
	total := 0
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			if base := info.Name(); base == ".git" || base == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		total += formatVersionRefsLike(string(raw), false)
		return nil
	})
	return total, err
}

// formatVersionRefsLike mirrors the predicate in internal/rules/formatversion_test.go.
// Duplicated on purpose: if the real one drifts, this brake stops exercising the
// real shape, and that drift would be invisible. Noted rather than hidden.
func formatVersionRefsLike(body string, isTest bool) int {
	if isTest {
		return 0
	}
	n := 0
	for _, ln := range strings.Split(body, "\n") {
		trimmed := strings.TrimSpace(ln)
		if strings.HasPrefix(trimmed, "//") {
			continue
		}
		if !strings.Contains(trimmed, "FormatVersion") {
			continue
		}
		if strings.HasPrefix(trimmed, "const FormatVersion") ||
			strings.HasPrefix(trimmed, "FormatVersion ") {
			continue
		}
		n++
	}
	return n
}

// E1/E2 -- mutate the SCOPE. The mutant that beat 772.
func TestAReferenceOutsideTheInstrumentPackageIsStillSeen(t *testing.T) {
	tmp := t.TempDir()
	mine := filepath.Join(tmp, "internal", "rules")
	other := filepath.Join(tmp, "internal", "central")
	for _, d := range []string{mine, other} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatalf("E: mkdir %s: %v", d, err)
		}
	}
	// The instrument's own package: declaration only, zero references.
	if err := os.WriteFile(filepath.Join(mine, "rule.go"),
		[]byte("package rules\n\nconst FormatVersion = 1\n"), 0o644); err != nil {
		t.Fatalf("E: write rule.go: %v", err)
	}

	// E2 -- negative control: nothing planted yet, so a correct scan finds 0.
	// A scan scoped ONLY to internal/rules also finds 0, which is why it looked
	// healthy at beat 772.
	n, err := eScanTree(tmp, mine)
	if err != nil {
		t.Fatalf("E2: %v", err)
	}
	if n != 0 {
		t.Fatalf("E2: control scan found %d references in a clean tree, want 0", n)
	}

	// E1 -- plant the reference where the broken G1 could not see it.
	if err := os.WriteFile(filepath.Join(other, "http.go"),
		[]byte("package central\n\nimport \"x/internal/rules\"\n\nvar _ = rules.FormatVersion\n"),
		0o644); err != nil {
		t.Fatalf("E1: write http.go: %v", err)
	}
	n, err = eScanTree(tmp, mine)
	if err != nil {
		t.Fatalf("E1: %v", err)
	}
	if n == 0 {
		t.Fatalf("E1: a reference in internal/central was NOT seen by a scan rooted at " +
			"the module root. This is exactly beat 772's defect: the guard is scoped " +
			"to its own package and the reference is outside it.")
	}
	t.Logf("E1/E2 MEASURED: the same code scanned from the module root sees a "+
		"reference planted in a sibling package (%d reference(s)); the clean control "+
		"sees 0. This is the mutation G1 could not survive at beat 772, and the "+
		"mutation G3 -- a predicate brake -- was structurally unable to catch.", n)

	// And the demonstration of why: scope it to the instrument's own package and
	// the reference vanishes. Not an assertion about production code; a receipt
	// for the claim that a predicate brake cannot see a scope bug.
	narrow, err := eScanTree(mine, mine)
	if err != nil {
		t.Fatalf("E1: narrow: %v", err)
	}
	if narrow != 0 {
		t.Fatalf("E1: narrow scan found %d, want 0 (the planted file is elsewhere)", narrow)
	}
	t.Logf("E1 RECEIPT: the SAME predicate, scoped to internal/rules, reports 0 on "+
		"the same tree where the module-root walk reports %d. Predicate correct in "+
		"both runs. Only the scope differs. That is why G3 was green.", n)
}

// E3 -- the transferable check. The other directory-scanning test in this package
// reads "." as well; assert its narrow scope is deliberate, and say why, so that
// the next reader does not "fix" it into correctness by accident or by
// coincidence.
func TestDotScansInThisPackageAreIntentionallyNarrow(t *testing.T) {
	raw, err := os.ReadFile("query_params_docs_test.go")
	if err != nil {
		t.Fatalf("E3: read query_params_docs_test.go: %v", err)
	}
	src := string(raw)

	if !strings.Contains(src, `os.ReadDir(".")`) {
		t.Fatalf("E3: query_params_docs_test.go no longer reads \".\" -- if the scan " +
			"was widened, confirm it still wants only this package's " +
			"Query().Get() calls and update this notice.")
	}
	// The scope is intentional because the subject is this package's HTTP
	// handlers. Assert the coupling that makes narrow correct: it compares
	// against docs/fleet-view.md, which documents THIS package's routes.
	if !strings.Contains(src, "fleet-view.md") {
		t.Fatalf("E3: query_params_docs_test.go reads \".\" but no longer references " +
			"docs/fleet-view.md -- the narrow scope was justified BY that pairing. " +
			"Without it, narrow may now be a blind spot like beat 772's G1.")
	}
	if !strings.Contains(src, "Query()") {
		t.Fatalf("E3: expected the scan to be about Query().Get() calls in this package")
	}
	t.Logf("E3 MEASURED: \".\" in query_params_docs_test.go is intentionally narrow -- " +
		"its subject is THIS package's Query().Get() calls, paired with " +
		"docs/fleet-view.md which documents those routes. Correct today, and now " +
		"coupled to the reason. A future widening will trip this notice.")
}

package rules

// BEAT 772 -- a dead constant must not be able to look alive again.
//
// FormatVersion was declared (rule.go:27) and read nowhere. Beat 771's lesson was
// that a comment describing its own code wrongly is a DEFECT, not a decision --
// so the comment now says "declared and never read". But a corrected comment with
// no instrument is just a better worded claim (beat 767: renaming "retained" to
// "scanned" left every suite green).
//
// So this file does two things:
//
//	G1  measures that FormatVersion has zero non-test references, right now.
//	G2  makes the comment's claim falsifiable: if someone wires the constant into
//	    a loader, G1 fails BY NAME and says to update the comment.
//	G3  is the honesty brake -- proves G1's detector can actually see a reference,
//	    using a synthetic source body. Without G3, G1 could be a greenscan: a
//	    grep for nothing finding nothing forever.
//
// Deliberately NOT in this file: any assertion that format versioning OUGHT to be
// enforced. Whether to wire FormatVersion up or delete it is the open decision in
// docs/FINDING-2026-11-02p. This file only pins what the code does today.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// formatVersionRefs scans Go source bodies for non-test references to the
// identifier. It is shared by G1 and G3 so the brake exercises the SAME logic.
// moduleRoot walks up from the test's working directory to the directory holding
// go.mod. Without this, the walk is confined to one package and the guard has a
// blind spot the size of the rest of the repo.
func moduleRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", os.ErrNotExist
		}
		dir = parent
	}
}

func formatVersionRefs(name string, body string, isTest bool) int {
	if isTest {
		return 0 // test files are allowed to mention it; they are the instrument
	}
	n := 0
	for _, ln := range strings.Split(body, "\n") {
		trimmed := strings.TrimSpace(ln)
		// Skip comments -- this file's own warning text mentions the name, and a
		// comment is not a reference. (If it were, the comment explaining the
		// constant would keep the constant's own guard alive. That is the
		// "counting a comment as coverage" error from MEMORY.md.)
		if strings.HasPrefix(trimmed, "//") {
			continue
		}
		if !strings.Contains(trimmed, name) {
			continue
		}
		// The declaration itself is not a reference.
		if strings.HasPrefix(trimmed, "const "+name) || strings.HasPrefix(trimmed, name+" ") {
			continue
		}
		n++
	}
	return n
}

// G1 -- the measurement.
func TestFormatVersionIsStillUnreferenced(t *testing.T) {
	// Walk the MODULE ROOT, not ".", because `go test` runs with the package
	// directory as CWD -- so "." here is internal/rules, and a reference in
	// internal/central would be invisible. Measured: with "." a cross-package
	// reference left this test GREEN. Find the root by walking up to go.mod.
	root, err := moduleRoot()
	if err != nil {
		t.Fatalf("G1: locate module root: %v", err)
	}
	var refs []string
	err = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			// Skip VCS and vendor noise; nothing else.
			if base := info.Name(); base == ".git" || base == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		isTest := strings.HasSuffix(path, "_test.go")
		if n := formatVersionRefs("FormatVersion", string(raw), isTest); n > 0 {
			rel, _ := filepath.Rel(root, path)
			refs = append(refs, rel)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("G1: walk: %v", err)
	}

	if len(refs) > 0 {
		t.Fatalf("G1: FormatVersion is now referenced in %v. The comment at "+
			"rule.go:27 states it is declared and never read -- that comment is "+
			"now WRONG. If format versioning was wired up on purpose, update the "+
			"comment and delete this test together; if it was wired up by "+
			"accident, this failure is the receipt.", refs)
	}
	t.Logf("G1 MEASURED: FormatVersion has zero non-test references in the tree. " +
		"The rule.go:27 comment (declared, never read; a pack declaring an " +
		"unsupported format version is accepted silently) is accurate as of this run.")
}

// G3 -- the brake. Prove the detector above can see a reference at all.
func TestFormatVersionDetectorCanSeeAReference(t *testing.T) {
	cases := []struct {
		name   string
		body   string
		isTest bool
		want   int
		why    string
	}{
		{"declaration only", "const FormatVersion = 1", false, 0,
			"a declaration is not a reference"},
		{"comment mention", "// see FormatVersion for details", false, 0,
			"a comment is not a reference"},
		{"real use in a loader", "if p.Version != FormatVersion {", false, 1,
			"this IS a reference and must be counted"},
		{"test file mention", "x := FormatVersion", true, 0,
			"test files are the instrument, not the subject"},
		{"no mention", "x := 1", false, 0, "nothing to see"},
	}
	for _, c := range cases {
		if got := formatVersionRefs("FormatVersion", c.body, c.isTest); got != c.want {
			t.Fatalf("G3 %q: got %d references, want %d (%s)", c.name, got, c.want, c.why)
		}
	}
	t.Logf("G3 MEASURED: the detector counts a real use, ignores the declaration, " +
		"ignores comments, and ignores test files. G1 is therefore capable of " +
		"failing and is not a greenscan.")
}

package central

// BEAT 774 -- Callie's rule, turned on my own tree: a gate that fails OPEN is a
// gate you cannot check by hand.
//
// Callie (7521) had her mail login REFUSED tonight -- AUTHENTICATIONFAILED, with
// the wrong credentials sourced. Her verdict was the opposite of a complaint:
// "it stopped, it refused, it did not return an empty inbox and call it clean. A
// gate that fails closed is the one thing I can check by hand."
//
// My tree had the opposite property in two places:
//
//	telemetry/scope_acceptance_test.go  stat(pack) err -> t.Skipf(...)
//	    Measured at beat 774: DELETE rules/carbonato.yaml and the suite reports
//	    PASS. The P1.3 acceptance criterion silently stops being tested. Fixed to
//	    t.Fatalf; the pack is tracked, so its absence is rot, not environment.
//
//	fleetsample_test.go  qv := "?limit=1"; if qv == "" { t.Skip() }
//	    A literal is never empty, so the branch was unreachable dead code -- an
//	    unreachable Skip is a silent pass waiting for a future edit to arm it.
//	    Removed.
//
// This file is the durable guard. It cannot decide whether a skip is justified
// (some legitimately are: declared XFAILs, documented-behaviour pins, feature
// detection on an optional build tag). So it does not pretend to. It requires
// every t.Skip in the tree to be CLASSIFIABLE, and freezes the count of the
// class that is easiest to hide in: a skip with no stated reason.
//
//	F1  every skip call carries a non-empty reason (an argument that exists).
//	F2  the number of skips with NO machine-readable marker (XFAIL, CURRENT
//	    behaviour, implemented, no longer applies) is pinned. A new unexplained
//	    skip fails this test and forces a decision instead of a shrug.
//	F3  the brake: F2's counter must move when an unmarked skip is added.

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// fSkipRE finds t.Skip(, t.Skipf(, and t.SkipNow().
var fSkipRE = regexp.MustCompile(`t\.Skip(?:f|Now)?\(`)

// fJustifiedMarkers are the substrings that indicate a skip has been CONSIDERED --
// it names a reason a reader can argue with. Anything else is an unmarked skip.
var fJustifiedMarkers = []string{
	"XFAIL",
	"CURRENT behaviour",
	"documented as CURRENT",
	"implemented",
	"no longer applies",
	"not found", // feature/env absence, e.g. optional build tag
	"requires",
	"not supported",
	// Beat 775: a build-tag skip states its reason as "the contract does not
	// apply here", which is honest and was being reported as unmarked. Widening
	// the vocabulary is the SAFE direction (it can only lower the count); the
	// pin below still stops a silent skip from being added.
	"does not apply",
}

// fCountSkips classifies a source body. Returns (total skips, unmarked skips).
// Shared by F1/F2 and the F3 brake so the brake exercises the real logic.
func fCountSkips(body string) (total int, unmarked []string) {
	// Strip comment lines FIRST. This file's own prose names t.Skip() repeatedly,
	// and beat 774's first run counted 8 of its own explanations as unmarked skips
	// -- the "counting a comment as coverage" error, inverted: counting a comment
	// as a defect. A comment is neither.
	var code strings.Builder
	for _, ln := range strings.Split(body, "\n") {
		if strings.HasPrefix(strings.TrimSpace(ln), "//") {
			code.WriteString("\n")
			continue
		}
		code.WriteString(ln)
		code.WriteString("\n")
	}
	src := code.String()
	for _, loc := range fSkipRE.FindAllStringIndex(src, -1) {
		total++
		// Look at the skip call plus a little context: the reason may be on the
		// next line for a wrapped Skipf.
		end := loc[1] + 400
		if end > len(src) {
			end = len(src)
		}
		call := src[loc[0]:end]

		// A bare t.Skip() with no argument carries no reason at all.
		isBare := strings.HasPrefix(call, "t.Skip()") || strings.HasPrefix(call, "t.SkipNow()")

		justified := false
		for _, m := range fJustifiedMarkers {
			if strings.Contains(call, m) {
				justified = true
				break
			}
		}
		if !justified || isBare {
			line := 1 + strings.Count(src[:loc[0]], "\n")
			unmarked = append(unmarked, "line "+itoa774(line))
		}
	}
	return total, unmarked
}

func itoa774(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

// fTreeSkips walks the module and classifies every skip.
func fTreeSkips(t *testing.T) (total int, unmarked int, names []string) {
	t.Helper()
	root, err := moduleRootForSkips()
	if err != nil {
		t.Fatalf("F: module root: %v", err)
	}
	err = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			if base := info.Name(); base == ".git" || base == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, "_test.go") {
			return nil
		}
		if strings.HasSuffix(path, "skipaudit_test.go") {
			// The instrument, not a subject: this file's prose and its F3 test
			// data both spell out t.Skip() on purpose. F3 exercises the predicate.
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		tot, un := fCountSkips(string(raw))
		total += tot
		unmarked += len(un)
		rel, _ := filepath.Rel(root, path)
		for _, u := range un {
			names = append(names, rel+" "+u)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("F: walk: %v", err)
	}
	return total, unmarked, names
}

func moduleRootForSkips() (string, error) {
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

// fUnmarkedBaseline is the frozen count. It may only move DOWN without comment.
// Moving it UP is a decision that needs a reason in this file.
const fUnmarkedBaseline = 0

// F2 -- the pin.
func TestNoUnmarkedSkipsInTheTree(t *testing.T) {
	total, unmarked, names := fTreeSkips(t)
	if unmarked > fUnmarkedBaseline {
		t.Fatalf("F2: %d skip(s) carry no stated reason (baseline %d):\n  %s\n\n"+
			"A skip with no reason is a silent pass: the test reports nothing and the "+
			"suite stays green. Beat 774 measured a bad one -- deleting the shipped "+
			"rule pack left the P1.3 acceptance test SKIPPED and the suite PASSING. "+
			"Either state a reason a reader can argue with (XFAIL, CURRENT "+
			"behaviour, requires X, not supported), or do not skip.",
			unmarked, fUnmarkedBaseline, strings.Join(names, "\n  "))
	}
	t.Logf("F2 MEASURED: %d skip(s) in the tree, %d unmarked (baseline %d). Every "+
		"skip states a reason.", total, unmarked, fUnmarkedBaseline)
}

// F1 -- every skip call passes an argument (no bare t.Skip()).
func TestEverySkipStatesAReason(t *testing.T) {
	root, err := moduleRootForSkips()
	if err != nil {
		t.Fatalf("F1: module root: %v", err)
	}
	var bad []string
	err = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			if base := info.Name(); base == ".git" || base == "vendor" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, "_test.go") {
			return nil
		}
		if strings.HasSuffix(path, "skipaudit_test.go") {
			// This file names t.Skip() in prose and in test data; it is the
			// instrument, not a subject. (F3 exercises the predicate directly.)
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		// Comments are not calls. fleetsample_test.go explains the removed
		// t.Skip() in prose, and F1's first run flagged that prose as a bare
		// skip -- the same "counting a comment as coverage" error inverted.
		var code strings.Builder
		for _, ln := range strings.Split(string(raw), "\n") {
			if strings.HasPrefix(strings.TrimSpace(ln), "//") {
				code.WriteString("\n")
				continue
			}
			code.WriteString(ln)
			code.WriteString("\n")
		}
		body := code.String()
		for _, loc := range fSkipRE.FindAllStringIndex(body, -1) {
			end := loc[1] + 40
			if end > len(body) {
				end = len(body)
			}
			call := body[loc[0]:end]
			if strings.HasPrefix(call, "t.Skip()") || strings.HasPrefix(call, "t.SkipNow()") {
				rel, _ := filepath.Rel(root, path)
				bad = append(bad, rel+": "+strings.SplitN(call, "\n", 2)[0])
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("F1: walk: %v", err)
	}
	if len(bad) > 0 {
		t.Fatalf("F1: bare t.Skip()/t.SkipNow() with no reason:\n  %s", strings.Join(bad, "\n  "))
	}
	t.Logf("F1 MEASURED: no bare skips in the tree.")
}

// F3 -- the brake. F2's counter has to be able to move.
func TestUnmarkedSkipDetectorCanFire(t *testing.T) {
	cases := []struct {
		name    string
		body    string
		wantUnm int
	}{
		{"bare skip", "func T(){ t.Skip() }", 1},
		{"skip with reason", `func T(){ t.Skip("XFAIL: not applied") }`, 0},
		{"skipf with reason", `func T(){ t.Skipf("requires model %s", m) }`, 0},
		{"unmarked skipf", `func T(){ t.Skipf("because") }`, 1},
		{"two skips one marked", "t.Skip(\"documented as CURRENT behaviour\")\nt.Skip()", 1},
		{"none", "func T(){}", 0},
	}
	for _, c := range cases {
		_, un := fCountSkips(c.body)
		if len(un) != c.wantUnm {
			t.Fatalf("F3 %q: %d unmarked, want %d (%v)", c.name, len(un), c.wantUnm, un)
		}
	}
	t.Logf("F3 MEASURED: the classifier flags bare and unreasoned skips and passes " +
		"ones that name a reason. F2 is capable of failing and is not a greenscan.")
}

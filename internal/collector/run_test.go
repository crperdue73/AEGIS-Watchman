package collector

import (
	"os"
	"path/filepath"
	"sort"
	"testing"
)

// CollectAll is already covered in config_test.go (TestCollectAllGlobsRoots, and
// TestCollectAllNoMatchIsError) -- I wrote "CollectAll had NO test" in this header at first, from
// a `grep "func Test" run_test.go` that hit a file that did not exist yet. Grepping the file I
// expected instead of the package is how a covered function gets called uncovered.
//
// What those two do NOT cover is the DIRECTIONS that make run.go:38 interesting: order with more
// than two roots, order merged ACROSS configured roots, order stable across repeated runs, and a
// malformed glob. Two elements sorted correctly is the shape a comparator bug can still pass.
//
// This file is deliberately about the CONTRACT, not the walk. WalkRoot has its own tests;
// repeating them here would just be slower.

// mkAgentRoot builds a directory with the given relative files, returning its absolute path.
func mkAgentRoot(t *testing.T, base string, files ...string) string {
	t.Helper()
	dir := filepath.Join(base, "agent")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, f := range files {
		p := filepath.Join(dir, f)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("x\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// TestCollectAllOneRootOneEntry is the baseline the rest of this file stands on: a single literal
// root yields exactly one Collected whose Root is the resolved path and whose inventory is real.
func TestCollectAllOneRootOneEntry(t *testing.T) {
	dir := mkAgentRoot(t, t.TempDir(), "a.txt")

	got, err := CollectAll(&Config{AgentRoots: []AgentRoot{{Path: dir}}})
	if err != nil {
		t.Fatalf("CollectAll: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d entries for one root, want 1", len(got))
	}
	if got[0].Root != dir {
		t.Errorf("Root = %q, want %q", got[0].Root, dir)
	}
	if got[0].Inventory == nil {
		t.Fatal("Inventory is nil for a readable root")
	}
	if len(got[0].Inventory.Files) == 0 {
		t.Error("inventory is empty for a root containing a file")
	}
}

// TestCollectAllGlobExpandsToOneEntryPerMatch pins the documented behaviour that a root pattern
// with glob metacharacters is not one agent but one agent PER MATCH. Getting this wrong is a
// silent undercount: three workspaces reported as one inventory, and the other two agents simply
// never appear in the fleet.
func TestCollectAllGlobExpandsToOneEntryPerMatch(t *testing.T) {
	base := t.TempDir()
	for _, name := range []string{"ws-a", "ws-b", "ws-c"} {
		if err := os.MkdirAll(filepath.Join(base, name), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(base, name, "f.txt"), []byte("x\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	got, err := CollectAll(&Config{AgentRoots: []AgentRoot{{Path: filepath.Join(base, "ws-*")}}})
	if err != nil {
		t.Fatalf("CollectAll: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("glob matched 3 directories but CollectAll returned %d entries", len(got))
	}
	for _, c := range got {
		if c.Inventory == nil {
			t.Errorf("root %q has a nil inventory", c.Root)
		}
	}
}

// TestCollectAllOrderIsDeterministic is the test that would have covered run.go:38.
//
// The comparator is `out[i].Root < out[j].Root`. A `<=` mutant makes the less-function reflexive,
// which violates the sort.Slice contract even though Go's pdqsort still produced sorted output on
// every shape I measured (200 randomised trials, including duplicates -- see WORKLOG beat 825).
// So this is not a kill attempt: it asserts the PROPERTY the comparator exists to provide. If a
// future Go release gets stricter about the contract, or someone rewrites the sort, this is what
// says the order must be strictly ascending and repeatable across runs.
func TestCollectAllOrderIsDeterministic(t *testing.T) {
	base := t.TempDir()
	// Created out of order on purpose, so a no-op "sort" cannot pass by accident.
	for _, name := range []string{"n3", "n1", "n4", "n2"} {
		if err := os.MkdirAll(filepath.Join(base, name), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(base, name, "f.txt"), []byte("x\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	cfg := &Config{AgentRoots: []AgentRoot{{Path: filepath.Join(base, "n*")}}}

	first, err := CollectAll(cfg)
	if err != nil {
		t.Fatalf("CollectAll: %v", err)
	}
	if len(first) != 4 {
		t.Fatalf("got %d entries, want 4", len(first))
	}

	var roots []string
	for _, c := range first {
		roots = append(roots, c.Root)
	}
	if !sort.StringsAreSorted(roots) {
		t.Errorf("roots are not in ascending order: %v", roots)
	}

	// Strictly ascending, not merely sorted-equal: duplicate entries would mean a match was
	// collected twice, which double-counts one agent's inventory.
	for i := 1; i < len(roots); i++ {
		if roots[i] == roots[i-1] {
			t.Errorf("duplicate root %q at positions %d and %d", roots[i], i-1, i)
		}
	}

	// Deterministic across runs: glob order comes from the filesystem, so the sort is the only
	// thing making the output stable. If it were dropped, this is the assertion that notices.
	second, err := CollectAll(cfg)
	if err != nil {
		t.Fatalf("CollectAll (second run): %v", err)
	}
	for i := range first {
		if first[i].Root != second[i].Root {
			t.Fatalf("order changed between runs at %d: %q then %q", i, first[i].Root, second[i].Root)
		}
	}
}

// TestCollectAllRefusesRootMatchingNothing pins run.go:26. A literal path that matches nothing is
// far more likely a typo or a missing mount than an agent with no files, so it is refused. The
// alternative -- returning zero entries and no error -- is the quiet version: the run "succeeds",
// the fleet report names no agents, and nobody notices until an incident review.
func TestCollectAllRefusesRootMatchingNothing(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "does-not-exist")
	if _, err := CollectAll(&Config{AgentRoots: []AgentRoot{{Path: missing}}}); err == nil {
		t.Fatal("a root matching no directories was accepted; the run would report an empty fleet " +
			"as a success")
	}
}

// TestCollectAllRefusesBadGlob pins run.go:22: a malformed pattern is an error, not a skip. A
// silently ignored agent root is an agent that stops being watched with no signal anywhere.
func TestCollectAllRefusesBadGlob(t *testing.T) {
	if _, err := CollectAll(&Config{AgentRoots: []AgentRoot{{Path: "/tmp/["}}}); err == nil {
		t.Fatal("a malformed agent-root glob was accepted")
	}
}

// TestCollectAllMultipleRootsAreMergedAndSorted pins the multi-root path: each configured root
// contributes its matches, and the combined result is sorted across roots, not grouped by the
// order they were configured in. Grouping by config order looks equivalent but means a caller can
// never binary-search or diff two runs by root without re-deriving the grouping.
func TestCollectAllMultipleRootsAreMergedAndSorted(t *testing.T) {
	base := t.TempDir()
	for _, name := range []string{"z-root", "a-root"} {
		if err := os.MkdirAll(filepath.Join(base, name), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(base, name, "f.txt"), []byte("x\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Configured in descending order; output must be ascending across both roots.
	got, err := CollectAll(&Config{AgentRoots: []AgentRoot{
		{Path: filepath.Join(base, "z-root")},
		{Path: filepath.Join(base, "a-root")},
	}})
	if err != nil {
		t.Fatalf("CollectAll: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d entries, want 2", len(got))
	}
	if !(got[0].Root < got[1].Root) {
		t.Errorf("roots are not sorted across configured roots: %q then %q", got[0].Root, got[1].Root)
	}
}

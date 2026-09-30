package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const oneRule = "name: p\nrules: [{id: p.rule, severity: critical, technique: t/x, title: a rule, match: {type: process.exec, exe: sh}}]\n"

func writeFile(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestLoadSetSingleFile(t *testing.T) {
	p := writeFile(t, t.TempDir(), "p.yaml", oneRule)
	packs, err := loadSet([]string{p})
	if err != nil {
		t.Fatalf("loadSet: %v", err)
	}
	if len(packs) != 1 || len(packs[0].Rules) != 1 {
		t.Fatalf("expected 1 pack / 1 rule, got %+v", packs)
	}
}

func TestLoadSetDir(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "a.yaml", oneRule)
	writeFile(t, dir, "b.yaml", "name: q\nrules: [{id: q.rule, severity: low, match: {type: process.exec, exe: ls}}]\n")
	packs, err := loadSet([]string{dir})
	if err != nil {
		t.Fatalf("loadSet: %v", err)
	}
	if len(packs) != 2 {
		t.Fatalf("expected 2 packs, got %d", len(packs))
	}
}

// The whole point of taking several paths: ids must be unique across the SET,
// not just within each file/dir. Two packs given as separate arguments that
// share an id must be a load error.
func TestLoadSetRejectsDuplicateIDAcrossPaths(t *testing.T) {
	d1, d2 := t.TempDir(), t.TempDir()
	a := writeFile(t, d1, "a.yaml", oneRule)
	b := writeFile(t, d2, "b.yaml", "name: q\nrules: [{id: p.rule, severity: low, match: {type: process.exec, exe: bash}}]\n")
	_, err := loadSet([]string{a, b})
	if err == nil {
		t.Fatal("expected a duplicate-id error across the given paths")
	}
	if !strings.Contains(err.Error(), "p.rule") {
		t.Errorf("error should name the duplicate id, got: %v", err)
	}
}

func TestSummarize(t *testing.T) {
	packs, err := loadSet([]string{writeFile(t, t.TempDir(), "p.yaml", oneRule)})
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	summarize(&buf, packs)
	got := buf.String()
	for _, want := range []string{"ok: 1 pack(s), 1 rule(s)", "critical=1", "techniques: 1"} {
		if !strings.Contains(got, want) {
			t.Errorf("summary missing %q:\n%s", want, got)
		}
	}
}

func TestListRules(t *testing.T) {
	packs, err := loadSet([]string{writeFile(t, t.TempDir(), "p.yaml", oneRule)})
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	listRules(&buf, packs)
	got := buf.String()
	if !strings.Contains(got, "p.rule") || !strings.Contains(got, "critical") || !strings.Contains(got, "t/x") {
		t.Errorf("list output missing fields:\n%s", got)
	}
}

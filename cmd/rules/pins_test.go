package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/CRPerdue/agentids/internal/rules"
)

func TestSplitPinsFlag(t *testing.T) {
	cases := []struct {
		in       []string
		wantPins string
		wantRest []string
		wantErr  bool
	}{
		{[]string{"rules/"}, "", []string{"rules/"}, false},
		{[]string{"--pins", "p.pins", "rules/"}, "p.pins", []string{"rules/"}, false},
		{[]string{"--pins=p.pins", "a.yaml"}, "p.pins", []string{"a.yaml"}, false},
		{[]string{"a.yaml", "--pins", "p.pins"}, "p.pins", []string{"a.yaml"}, false},
		{[]string{"--pins"}, "", nil, true},
		{[]string{"--pins="}, "", nil, true},
	}
	for _, c := range cases {
		pins, rest, err := splitPinsFlag(c.in)
		if c.wantErr {
			if err == nil {
				t.Errorf("%v: expected an error", c.in)
			}
			continue
		}
		if err != nil {
			t.Errorf("%v: unexpected error: %v", c.in, err)
			continue
		}
		if pins != c.wantPins || strings.Join(rest, ",") != strings.Join(c.wantRest, ",") {
			t.Errorf("%v: got pins=%q rest=%v", c.in, pins, rest)
		}
	}
}

// TestCheckPinsOKAndDrift is the acceptance path for a pin: a set that matches
// reports ok, and every kind of drift (changed bytes, a new pack, a removed
// pack) is a hard failure naming the file.
func TestCheckPinsOKAndDrift(t *testing.T) {
	dir := t.TempDir()
	pk := writeFile(t, dir, "a.yaml", oneRule)

	pins, err := rules.PinSet([]string{dir})
	if err != nil {
		t.Fatal(err)
	}
	pinsFile := filepath.Join(t.TempDir(), "packs.pins")
	if err := os.WriteFile(pinsFile, rules.FormatPins(pins), 0o600); err != nil {
		t.Fatal(err)
	}

	// Matching set: ok, no error.
	var buf bytes.Buffer
	if err := checkPins(&buf, pinsFile, []string{dir}); err != nil {
		t.Fatalf("matching set should pass: %v\n%s", err, buf.String())
	}
	if !strings.Contains(buf.String(), "pins: ok") {
		t.Errorf("expected an ok line, got:\n%s", buf.String())
	}

	// Changed bytes (a re-pin is required): must fail and name the file.
	if err := os.WriteFile(pk, []byte(strings.Replace(oneRule, "exe: sh", "exe: bash", 1)), 0o600); err != nil {
		t.Fatal(err)
	}
	buf.Reset()
	if err := checkPins(&buf, pinsFile, []string{dir}); err == nil {
		t.Fatalf("changed pack should fail the pin check")
	}
	if !strings.Contains(buf.String(), "changed") || !strings.Contains(buf.String(), "a.yaml") {
		t.Errorf("drift report should name the changed file:\n%s", buf.String())
	}

	// A new, unpinned pack: must fail.
	writeFile(t, dir, "b.yaml", "name: q\nrules: [{id: q.rule, severity: low, match: {type: process.exec, exe: ls}}]\n")
	buf.Reset()
	if err := checkPins(&buf, pinsFile, []string{dir}); err == nil {
		t.Fatal("an unpinned pack should fail the pin check")
	}
	if !strings.Contains(buf.String(), "unpinned") {
		t.Errorf("drift report should flag the unpinned pack:\n%s", buf.String())
	}
}

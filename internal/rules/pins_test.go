package rules

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const validPack = `name: p
version: "2"
rules:
  - id: p.rule
    severity: critical
    title: a rule
    match:
      type: process.exec
      exe: sh
`

func writePack(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestPinFileAndRoundTrip(t *testing.T) {
	p := writePack(t, t.TempDir(), "p.yaml", validPack)
	pin, err := PinFile(p)
	if err != nil {
		t.Fatalf("PinFile: %v", err)
	}
	if pin.File != "p.yaml" || pin.Name != "p" || pin.Version != "2" {
		t.Fatalf("pin identity = %+v", pin)
	}
	if len(pin.SHA256) != 64 {
		t.Fatalf("pin digest = %q, want 64 hex", pin.SHA256)
	}
	if got := pin.NameVersion(); got != "p@2" {
		t.Errorf("NameVersion = %q", got)
	}

	// Format then parse must reproduce the same set — the pin file is a stable
	// round-trip, not a lossy rendering.
	text := FormatPins([]Pin{pin})
	if !strings.Contains(string(text), "# format:") {
		t.Errorf("pin file missing the format header:\n%s", text)
	}
	parsed, err := ParsePins(text)
	if err != nil {
		t.Fatalf("ParsePins: %v", err)
	}
	if len(parsed) != 1 || parsed[0] != pin {
		t.Fatalf("round-trip mismatch: %+v vs %+v", parsed, pin)
	}
}

func TestPinFileRejectsInvalidPack(t *testing.T) {
	// A file the collector would refuse to load must not be pinnable.
	p := writePack(t, t.TempDir(), "bad.yaml", "name: bad\nrules:\n  - id: nope\n")
	if _, err := PinFile(p); err == nil {
		t.Fatal("expected an error pinning an invalid pack")
	}
}

func TestPinSetSortedAndMatchesDirSelection(t *testing.T) {
	dir := t.TempDir()
	writePack(t, dir, "b.yaml", "name: b\nrules:\n  - id: b.rule\n    severity: low\n    title: b\n    match: {type: process.exec, exe: b}\n")
	writePack(t, dir, "a.yaml", "name: a\nrules:\n  - id: a.rule\n    severity: low\n    title: a\n    match: {type: process.exec, exe: a}\n")
	// A non-YAML file must be ignored, exactly as LoadPacks ignores it.
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("ignore me"), 0o600); err != nil {
		t.Fatal(err)
	}
	pins, err := PinSet([]string{dir})
	if err != nil {
		t.Fatalf("PinSet: %v", err)
	}
	if len(pins) != 2 {
		t.Fatalf("pinned %d packs, want 2: %+v", len(pins), pins)
	}
	if pins[0].File != "a.yaml" || pins[1].File != "b.yaml" {
		t.Errorf("pins not sorted by file: %+v", pins)
	}
}

func TestFormatPinsIsDeterministic(t *testing.T) {
	a := Pin{File: "b.yaml", Name: "b", SHA256: strings.Repeat("b", 64)}
	b := Pin{File: "a.yaml", Name: "a", SHA256: strings.Repeat("a", 64)}
	// Given out of order; the output is sorted regardless.
	if got, want := string(FormatPins([]Pin{a, b})), string(FormatPins([]Pin{b, a})); got != want {
		t.Errorf("FormatPins not order-independent:\n%s\n---\n%s", got, want)
	}
}

func TestParsePinsRejectsMalformed(t *testing.T) {
	cases := map[string]string{
		"too few fields": "abc  p@1\n",
		"non-hex digest": strings.Repeat("z", 64) + "  p@1  p.yaml\n",
		"short digest":   "abcd  p@1  p.yaml\n",
	}
	for name, in := range cases {
		if _, err := ParsePins([]byte(in)); err == nil {
			t.Errorf("%s: expected a parse error", name)
		}
	}
	// Comments and blank lines are fine.
	ok := "# a comment\n\n" + strings.Repeat("a", 64) + "  p@1  p.yaml\n"
	pins, err := ParsePins([]byte(ok))
	if err != nil || len(pins) != 1 {
		t.Fatalf("comments/blank lines should be skipped: pins=%+v err=%v", pins, err)
	}
}

func TestDiffPins(t *testing.T) {
	d := func(s string) Pin { return Pin{File: s, SHA256: strings.Repeat(s[:1], 64)} }
	pinned := []Pin{d("a.yaml"), d("b.yaml"), d("c.yaml")}
	actual := []Pin{d("a.yaml"), {File: "b.yaml", SHA256: strings.Repeat("9", 64)}, d("d.yaml")}

	issues := DiffPins(pinned, actual)
	got := map[string]string{}
	for _, is := range issues {
		got[is.File] = is.Kind
	}
	if got["b.yaml"] != PinChanged {
		t.Errorf("b.yaml should be changed, got %q", got["b.yaml"])
	}
	if got["c.yaml"] != PinMissing {
		t.Errorf("c.yaml should be missing, got %q", got["c.yaml"])
	}
	if got["d.yaml"] != PinUnpinned {
		t.Errorf("d.yaml should be unpinned, got %q", got["d.yaml"])
	}
	if _, ok := got["a.yaml"]; ok {
		t.Errorf("a.yaml matches and must not be reported: %+v", issues)
	}
	// Deterministic order: sorted by file.
	if issues[0].File != "b.yaml" || issues[1].File != "c.yaml" || issues[2].File != "d.yaml" {
		t.Errorf("issues not sorted by file: %+v", issues)
	}

	// An identical set is silent.
	if got := DiffPins(pinned, pinned); len(got) != 0 {
		t.Errorf("identical sets reported drift: %+v", got)
	}
}

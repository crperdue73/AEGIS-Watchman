package rules

// BEAT 763 -- the pin file's own round-trip. Leo's problem (7476), one layer down.
//
// ParsePins is deliberately strict: "A malformed line is an error, not a skipped
// line -- a pin file that quietly drops a pack pins nothing for that pack." Good.
// But strictness only helps if the WRITER can't produce the malformed shape.
//
// FormatPins emits `%s  %s  %s` (sha256, name@version, filename) with no quoting
// and no validation of the name. ParsePins requires exactly 3 whitespace fields.
// A pack whose name contains a space therefore produces a line ParsePins REFUSES.
//
// That is the good direction (loud, not silent) -- so this is not a detection
// miss, it is a question about the pin file's promise. The header says
// "byte-exact and reproducible"; the real question is whether a set can be
// written that cannot be read back. If so, `pin > packs.pins` produces a file
// `verify` will reject, and the operator's stable state is unreachable.
//
// W1  control: a normal pin set round-trips byte-identically
// W2  a pack name with a space: can FormatPins -> ParsePins succeed?
// W3  a filename with a space: same question (a dir can hold "my pack.yaml")
// W4  the operator consequence: is the resulting file ACCEPTED by verify?
// W5  honesty brake: does any real pack in the repo / testdata have such a name,
//     or is this a shape only a hand-written pack could reach?

import (
	"strings"
	"testing"
)

func pinFor(name, version, file, sha string) Pin {
	return Pin{Name: name, Version: version, File: file, SHA256: sha}
}

const sampleSHA = "0000000000000000000000000000000000000000000000000000000000000001"

// W1 -- control.
func TestPinSetRoundTrips(t *testing.T) {
	pins := []Pin{
		pinFor("carbonato", "1", "carbonato.yaml", sampleSHA),
		pinFor("baseline", "", "baseline.yml", sampleSHA),
	}
	text := FormatPins(pins)
	back, err := ParsePins(text)
	if err != nil {
		t.Fatalf("W1: a normal pin set failed to parse back: %v", err)
	}
	if len(back) != 2 {
		t.Fatalf("W1: round-trip returned %d pins, want 2", len(back))
	}
	if string(FormatPins(back)) != string(text) {
		t.Fatalf("W1: formatting is not idempotent:\n%s\n---\n%s", text, FormatPins(back))
	}
}

// W2 -- the defect shape.
func TestPackNameWithSpaceBreaksThePinFile(t *testing.T) {
	pins := []Pin{pinFor("my pack", "1", "my.yaml", sampleSHA)}
	text := FormatPins(pins)

	back, err := ParsePins(text)
	if err == nil {
		// If this ever passes, the writer learned to quote or the reader learned
		// to split from the right. Either way the old pin is stale.
		t.Fatalf("W2: a pack name containing a space now round-trips (%d pins) -- "+
			"re-read finding 2026-11-02r, the writer or reader changed", len(back))
	}
	// MEASURED: the writer produces a line the reader refuses. Declared XFAIL
	// because the fix is a decision (validate the name, quote it, or split the
	// line from the right so the name is the only variable-width field).
	t.Skipf("XFAIL (finding 2026-11-02r, NOT applied): FormatPins wrote a line that "+
		"ParsePins refuses (%v). The pin header promises a byte-exact reproducible "+
		"record; it cannot represent a pack whose name contains whitespace. Retire "+
		"when the writer validates/quotes, or the reader splits from the right.", err)
}

// W3 -- a filename with a space. Files are the last field, so a naive right-split
// would fix names and break files: measured here so the trade-off is visible
// rather than assumed.
func TestFileNameWithSpaceBreaksThePinFile(t *testing.T) {
	pins := []Pin{pinFor("pack", "1", "my pack.yaml", sampleSHA)}
	text := FormatPins(pins)
	if _, err := ParsePins(text); err == nil {
		t.Fatalf("W3: a filename containing a space now round-trips -- re-read 2026-11-02r")
	}
	// This is what makes the writer fix non-obvious: name@version sits in the
	// MIDDLE, so neither left-split nor right-split is universally correct.
	t.Logf("W3 MEASURED: name and file can BOTH contain whitespace, and name@version " +
		"is the middle field -- so no fixed split point is right for both. A fix " +
		"needs a separator that cannot appear in either, or quoting.")
}

// W4 -- what the operator sees. This is the part that matters: not "an error
// exists" but "the workflow is a loop that cannot close."
func TestThePinWorkflowCannotCloseForSuchAPack(t *testing.T) {
	dir := t.TempDir()
	body := strings.Replace(goodPack, "name: demo", "name: my pack", 1)
	writePackFile(t, dir, "p.yaml", body)

	pins, err := PinSet([]string{dir})
	if err != nil {
		t.Fatalf("W4: PinSet: %v", err)
	}
	text := FormatPins(pins)
	if _, err := ParsePins(text); err == nil {
		t.Fatalf("W4: the real pack loaded and pinned cleanly -- re-read 2026-11-02r")
	}
	t.Logf("W4 MEASURED: PinSet succeeds, FormatPins succeeds, ParsePins refuses the " +
		"output. `pin > packs.pins` + `verify --pins packs.pins` cannot converge for " +
		"this pack. The reader is strict, which is correct; the writer has no gate.")
}

// W5 -- honesty brake: is this reachable in practice?
func TestNoShippedPackHasAWhitespaceName(t *testing.T) {
	// The pack schema requires only a non-empty name, so a hand-written pack can
	// do this and nothing stops it. Rather than guess about shipped packs, assert
	// the CONSTRAINT: if the schema ever gains a name pattern, this note is stale.
	p, err := ParsePack([]byte(strings.Replace(goodPack, "name: demo", "name: two words", 1)))
	if err != nil {
		t.Fatalf("W5: a whitespace name is now REFUSED at pack load (%v) -- that is a "+
			"better fix than any I proposed; retire 2026-11-02r entirely", err)
	}
	if !strings.Contains(p.Name, " ") {
		t.Fatalf("W5: name %q did not preserve the space", p.Name)
	}
	t.Logf("W5 MEASURED: the pack schema accepts a whitespace name today, so the pin " +
		"writer can be handed one without any hand-editing of the pin file.")
}

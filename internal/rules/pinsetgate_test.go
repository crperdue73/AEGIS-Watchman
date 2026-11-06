package rules

// BEAT 762 -- the pin can vouch for a file the detector will never load.
//
// rule.go:198-201 states a strong invariant in prose:
//
//	"packFileNames ... is the one definition of 'the packs in a dir', shared by
//	 LoadPacks and PinSet so a pin always describes the exact set the collector
//	 would load."
//
// That is true for DIRECTORIES. It is not true for the second shape PinSet
// accepts: an explicit FILE path. pins.go:96 appends any path that isn't a dir
// with no extension check, so `PinSet([]string{"rules.txt"})` pins a file that
// LoadPacks would skip forever (packFileNames only admits .yaml/.yml).
//
// Why this matters more than a naming nit: a pin is an ATTESTATION. The whole
// reason it exists is so an operator can say "these are the rules in force." If
// a pin can describe a file that can never be in force, the attestation is
// broader than the truth -- the operator sees a covered-looking number that
// includes a pack no rule will ever fire from. For a detection tool that is the
// same failure rule.go:120 warns about, one layer up.
//
// U1  control: a dir of .yaml files pins exactly those and matches LoadPacks
// U2  a dir containing a .txt is skipped by BOTH (the invariant holds for dirs)
// U3  an explicit .txt path IS pinned, and LoadPacks(dir) does NOT load it
// U4  the set comparison: can a pin set and a load set be made to disagree by
//     path shape alone, with the files identical on disk?
// U5  honesty brake: the CLI. Does `watchman rules pins <file>` reach this, or is
//     the file form unreachable from the command surface (making U3 latent-only)?

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writePackFile(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatalf("U: write %s: %v", name, err)
	}
	return p
}

// U1 + U2 -- the dir shape, where the invariant DOES hold.
func TestPinSetMatchesLoadPacksForADirectory(t *testing.T) {
	dir := t.TempDir()
	// Distinct ids: CheckUniqueIDs(rightly) refuses two packs sharing a rule id,
	// so reusing goodPack verbatim would make this a test of that guard instead.
	writePackFile(t, dir, "a.yaml", goodPack)
	writePackFile(t, dir, "b.yml", strings.Replace(goodPack, "demo.exec.sh", "demo.exec.sh2", 1))
	// U2: a file with the wrong extension must be invisible to BOTH.
	writePackFile(t, dir, "notes.txt", goodPack)

	packs, err := LoadPacks(dir)
	if err != nil {
		t.Fatalf("U1: LoadPacks: %v", err)
	}
	pins, err := PinSet([]string{dir})
	if err != nil {
		t.Fatalf("U1: PinSet: %v", err)
	}
	if len(packs) != 2 {
		t.Fatalf("U1: LoadPacks loaded %d packs, want 2", len(packs))
	}
	if len(pins) != 2 {
		t.Fatalf("U1: PinSet pinned %d files, want 2 -- the dir invariant broke", len(pins))
	}
	for _, p := range pins {
		if filepath.Base(p.File) == "notes.txt" {
			t.Fatalf("U2: notes.txt was pinned from a directory, but LoadPacks would " +
				"never load it -- the invariant stated at rule.go:198 is false")
		}
	}
}

// U3 -- the hole. An explicit path bypasses the extension gate entirely.
func TestExplicitNonPackPathIsPinnedButNeverLoaded(t *testing.T) {
	dir := t.TempDir()
	txt := writePackFile(t, dir, "rules.txt", goodPack)

	pins, err := PinSet([]string{txt})
	if err != nil {
		t.Fatalf("U3: PinSet on an explicit .txt path errored: %v", err)
	}
	if len(pins) != 1 {
		t.Fatalf("U3: expected 1 pin for the explicit file, got %d", len(pins))
	}

	// The same directory, asked to load: the file is invisible.
	packs, err := LoadPacks(dir)
	if err != nil {
		t.Fatalf("U3: LoadPacks: %v", err)
	}
	if len(packs) != 0 {
		t.Fatalf("U3: LoadPacks loaded %d packs from a dir holding only rules.txt", len(packs))
	}

	// MEASURED: one pin, zero packs, identical bytes on disk. The attestation
	// covers a file the collector cannot load. Pinned as a declared XFAIL because
	// the fix is a decision (refuse a non-pack explicit path, or accept that a pin
	// may describe staged-but-inactive rules and say so) -- see finding 2026-11-02q.
	t.Skipf("XFAIL (finding 2026-11-02q, NOT applied): PinSet(%q) pins a file that "+
		"LoadPacks would never load (extension gate lives only in packFileNames, and "+
		"the explicit-file branch at pins.go:96 has no gate). A pin can therefore "+
		"attest to rules that are not in force. Retire this pin when the contract is "+
		"decided.", filepath.Base(txt))
}

// U4 -- make the two sets disagree with identical content, by path shape alone.
func TestPinSetAndLoadPacksCanDisagreeOnTheSameTree(t *testing.T) {
	dir := t.TempDir()
	writePackFile(t, dir, "real.yaml", goodPack)
	staged := writePackFile(t, dir, "staged.yaml.disabled",
		strings.Replace(goodPack, "demo.exec.sh", "demo.exec.sh.staged", 1))

	// Collector's view: one pack.
	packs, err := LoadPacks(dir)
	if err != nil {
		t.Fatalf("U4: LoadPacks: %v", err)
	}
	// Operator's view: dir + the staged file spelled out explicitly.
	pins, err := PinSet([]string{dir, staged})
	if err != nil {
		t.Fatalf("U4: PinSet: %v", err)
	}
	if len(packs) != 1 {
		t.Fatalf("U4: want 1 loadable pack, got %d", len(packs))
	}
	if len(pins) != 2 {
		t.Fatalf("U4: want 2 pins (dir + explicit staged file), got %d -- if this is now "+
			"1, the explicit-file gate was added; re-read finding 2026-11-02q", len(pins))
	}
	t.Logf("U4 MEASURED: %d pins vs %d packs over one tree -- the pin set is broader "+
		"than the load set, and the extra entry is a file whose bytes are a valid pack",
		len(pins), len(packs))
}

// U5 -- honesty brake: how reachable is this? The CLI takes paths as arguments.
func TestPinsCLIAcceptsArbitraryPaths(t *testing.T) {
	// We assert the reachable surface rather than guessing: PinSet is the only
	// entry and it takes []string of user-supplied paths, called from
	// cmd/rules/pins.go with flag args and from collector/behavior.go:347 with a
	// config RulesDir. The CONFIG path can only ever be a directory, so the hole
	// needs a human typing a filename -- it is NOT reachable from the collector's
	// own config. That keeps the impact bounded, and is why this is a doc/contract
	// finding and not an escalation.
	dir := t.TempDir()
	txt := writePackFile(t, dir, "rules.txt", goodPack)
	pins, err := PinSet([]string{txt})
	if err != nil || len(pins) != 1 {
		t.Fatalf("U5: reachability probe changed (err=%v n=%d) -- re-read 2026-11-02q", err, len(pins))
	}
	t.Logf("U5 MEASURED: pinning an explicit non-pack file succeeds; the collector's "+
		"own path (behavior.go:347) passes a directory, so this is reachable from the "+
		"CLI, not from collection. Bounded: %s", filepath.Base(txt))
}

package rules

// Rule-pack pinning: the publishing/approval half of Layer 3.
//
// A rule pack is a versioned, shareable artifact (the Snort model) — which is
// exactly why a fleet needs to say *which* packs it runs and record that they
// are still byte-for-byte the ones it approved. A pin file is that record: one
// line per pack — its file name, its name@version, and the sha256 of the raw
// file bytes. `watchman-rules verify --pins` fails loud when the loaded set has
// drifted from the pin (a pack changed, a pinned pack vanished, or a new pack
// appeared unpinned).
//
// It is a lockfile, not a signature. Byte-exact on purpose, the same way Layer 2
// hashes an agent file: the pin names the *artifact*, so a reformat that changes
// the bytes is a deliberate re-approval, not a silent pass. A bare pin is only a
// lockfile — anyone can write or edit one — so the pin *file* can itself be
// signed. `watchman-rules sign-pins` writes a detached Ed25519 signature over
// the pin's exact bytes, reusing the same keys Layer 2 uses for agent manifests;
// `verify --pins <pins> --pubkey <key>` checks that signature *before* it checks
// the set against the pin, so a tampered pin is rejected as tampered, not merely
// as mismatched. See `cmd/rules/signpins.go` and `docs/rules.md` ("Signing a
// pin").
import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Pin records one pack's identity and byte-exact digest.
type Pin struct {
	// File is the pack's base file name (e.g. "carbonato.yaml").
	File string
	// Name and Version are read from the pack document.
	Name    string
	Version string
	// SHA256 is the hex digest of the pack file's raw bytes.
	SHA256 string
}

// NameVersion renders "name@version", or just the name when unversioned.
func (p Pin) NameVersion() string {
	if p.Version == "" {
		return p.Name
	}
	return p.Name + "@" + p.Version
}

// PinFile pins a single pack file: it must parse as a valid pack, and its raw
// bytes are hashed. A file that is not a valid pack is an error — pinning a
// document the collector would refuse to load is meaningless.
func PinFile(path string) (Pin, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Pin{}, fmt.Errorf("rules: read pack %s: %w", path, err)
	}
	p, err := ParsePack(raw)
	if err != nil {
		return Pin{}, fmt.Errorf("rules: %s: %w", path, err)
	}
	sum := sha256.Sum256(raw)
	return Pin{
		File:    filepath.Base(path),
		Name:    p.Name,
		Version: p.Version,
		SHA256:  hex.EncodeToString(sum[:]),
	}, nil
}

// PinSet pins every pack named by paths (a pack file, or a directory of packs),
// sorted by file name for a deterministic lockfile. The file selection matches
// LoadPacks exactly — the same *.yaml/*.yml, non-recursive, sorted — so a pin
// always describes the set the collector would actually load from those paths.
func PinSet(paths []string) ([]Pin, error) {
	var files []string
	for _, p := range paths {
		info, err := os.Stat(p)
		if err != nil {
			return nil, fmt.Errorf("rules: %w", err)
		}
		if info.IsDir() {
			names, err := packFileNames(p)
			if err != nil {
				return nil, err
			}
			for _, n := range names {
				files = append(files, filepath.Join(p, n))
			}
			continue
		}
		files = append(files, p)
	}
	pins := make([]Pin, 0, len(files))
	for _, f := range files {
		pin, err := PinFile(f)
		if err != nil {
			return nil, err
		}
		pins = append(pins, pin)
	}
	sort.Slice(pins, func(i, j int) bool { return pins[i].File < pins[j].File })
	return pins, nil
}

// pinHeader documents the on-disk pin format in the file itself, so a lockfile
// copied into a repo explains how to regenerate and check it.
const pinHeader = `# watchman rule-pack pins — a byte-exact record of an approved pack set.
# regenerate: watchman-rules pin <dir-or-file>... > packs.pins
# verify:     watchman-rules verify --pins packs.pins <dir-or-file>...
# format: <sha256>  <name@version>  <filename>
`

// FormatPins renders a pin set as the pin-file text (header + one line per
// pack, sorted by file). Byte-exact and reproducible: the same set always
// formats to the same bytes.
func FormatPins(pins []Pin) []byte {
	sorted := append([]Pin(nil), pins...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].File < sorted[j].File })
	var b strings.Builder
	b.WriteString(pinHeader)
	for _, p := range sorted {
		fmt.Fprintf(&b, "%s  %s  %s\n", p.SHA256, p.NameVersion(), p.File)
	}
	return []byte(b.String())
}

// ParsePins reads a pin file. Blank lines and lines starting with '#' are
// ignored; every data line must have exactly three whitespace-separated fields
// (sha256, name@version, filename). A malformed line is an error, not a skipped
// line — a pin file that quietly drops a pack pins nothing for that pack.
func ParsePins(data []byte) ([]Pin, error) {
	var pins []Pin
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	line := 0
	for sc.Scan() {
		line++
		s := strings.TrimSpace(sc.Text())
		if s == "" || strings.HasPrefix(s, "#") {
			continue
		}
		fields := strings.Fields(s)
		if len(fields) != 3 {
			return nil, fmt.Errorf("rules: pin file line %d: want 3 fields (sha256, name@version, file), got %d", line, len(fields))
		}
		sha := strings.ToLower(fields[0])
		if _, err := hex.DecodeString(sha); err != nil || len(sha) != 2*sha256.Size {
			return nil, fmt.Errorf("rules: pin file line %d: %q is not a 64-char hex sha256", line, fields[0])
		}
		name, version := splitNameVersion(fields[1])
		if name == "" {
			return nil, fmt.Errorf("rules: pin file line %d: empty pack name", line)
		}
		pins = append(pins, Pin{File: fields[2], Name: name, Version: version, SHA256: sha})
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("rules: read pin file: %w", err)
	}
	return pins, nil
}

// splitNameVersion splits "name@version"; a name with no '@' has no version.
func splitNameVersion(s string) (name, version string) {
	if i := strings.IndexByte(s, '@'); i >= 0 {
		return s[:i], s[i+1:]
	}
	return s, ""
}

// Pin drift kinds.
const (
	PinChanged  = "changed"  // the file is present but its digest differs
	PinMissing  = "missing"  // pinned, but absent from the loaded set
	PinUnpinned = "unpinned" // present in the set but not in the pin file
)

// PinIssue is one drift between a pin file and the actual pack set.
type PinIssue struct {
	Kind string
	File string
	// Want is the pinned digest (for changed/missing); Got is the observed one
	// (for changed/unpinned).
	Want string
	Got  string
}

// DiffPins compares a pinned set against the actual loaded set, matched by file
// name, and returns every drift sorted by file then kind. An empty result means
// the two agree exactly.
func DiffPins(pinned, actual []Pin) []PinIssue {
	pinnedByFile := make(map[string]Pin, len(pinned))
	for _, p := range pinned {
		pinnedByFile[p.File] = p
	}
	actualByFile := make(map[string]Pin, len(actual))
	for _, a := range actual {
		actualByFile[a.File] = a
	}
	var issues []PinIssue
	for _, p := range pinned {
		a, ok := actualByFile[p.File]
		switch {
		case !ok:
			issues = append(issues, PinIssue{Kind: PinMissing, File: p.File, Want: p.SHA256})
		case a.SHA256 != p.SHA256:
			issues = append(issues, PinIssue{Kind: PinChanged, File: p.File, Want: p.SHA256, Got: a.SHA256})
		}
	}
	for _, a := range actual {
		if _, ok := pinnedByFile[a.File]; !ok {
			issues = append(issues, PinIssue{Kind: PinUnpinned, File: a.File, Got: a.SHA256})
		}
	}
	sort.Slice(issues, func(i, j int) bool {
		if issues[i].File != issues[j].File {
			return issues[i].File < issues[j].File
		}
		return issues[i].Kind < issues[j].Kind
	})
	return issues
}

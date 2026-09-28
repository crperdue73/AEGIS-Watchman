// Package collector implements the per-host Layer 1 detection: inventory,
// hash, and watch agent identity/persona/skill/config files.
//
// Phase 1 scope (this package): walk the configured agent roots, hash the
// matched files, and produce HOST-side state (map path -> FileState). The
// drift diff against a stored baseline happens in package central.
package collector

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// FileState is the immutable snapshot of one file at one point in time.
//
// Content is optional: collectors include it only for small text files so the
// central service can produce a human-readable diff. It is omitted from the
// baseline when empty and is never required for change detection (the hash is).
type FileState struct {
	Path    string    `json:"path"`
	SHA256  string    `json:"sha256"`
	Size    int64     `json:"size"`
	ModTime time.Time `json:"mod_time"`
	Content string    `json:"content,omitempty"`
}

// Inventory is the full set of hashed files for one agent root.
type Inventory struct {
	Root  string      `json:"root"`
	Files []FileState `json:"files"`
}

// MaxContentBytes bounds how much file content a collector reads and ships.
// Larger files are still hashed and reported; only the diff body is unavailable.
const MaxContentBytes = 512 * 1024

// isProbablyText applies a cheap heuristic: NUL byte in the first 8 KiB means
// binary, and we do not ship binary blobs. Good enough to avoid diffing PNGs.
func isProbablyText(b []byte) bool {
	n := len(b)
	if n > 8192 {
		n = 8192
	}
	for i := 0; i < n; i++ {
		if b[i] == 0 {
			return false
		}
	}
	return true
}

// readContent reads a file for diffing, returning "" if it is binary or too
// large. A read error is non-fatal — the hash-based detection still stands.
func readContent(path string, size int64) string {
	if size > MaxContentBytes || size < 0 {
		return ""
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	if !isProbablyText(b) {
		return ""
	}
	return string(b)
}
func HashFile(path string) (string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()

	h := sha256.New()
	n, err := io.Copy(h, f)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(h.Sum(nil)), n, nil
}

// matchGlob reports whether name matches any include pattern and no exclude
// pattern. Patterns use filepath.Match semantics against the file's base name
// and also against the root-relative slash path, so both "SOUL.md" and
// "skills/**/*.md"-style intent work for the common case.
func matchGlob(rel string, base string, include, exclude []string) bool {
	excluded := false
	for _, pat := range exclude {
		if globMatch(pat, rel, base) {
			excluded = true
			break
		}
	}
	if excluded {
		return false
	}
	if len(include) == 0 {
		return true
	}
	for _, pat := range include {
		if globMatch(pat, rel, base) {
			return true
		}
	}
	return false
}

// globMatch handles exact basename, filepath.Match, and a "**/" wildcard that
// matches across path separators (e.g. "skills/**/*.md" also matches
// "skills/nested/two.md", and "**/*.tmp" matches at any depth).
func globMatch(pat, rel, base string) bool {
	if pat == base || pat == rel {
		return true
	}
	if ok, _ := filepath.Match(pat, base); ok {
		return true
	}
	if ok, _ := filepath.Match(pat, rel); ok {
		return true
	}

	// Expand "**" into a segment-count-aware match: try matching the pattern's
	// suffix against the file at every suffix of the relative path. This is the
	// standard "globstar" behavior implemented without a dependency.
	if strings.Contains(pat, "**") {
		// "skills/**/*.md" -> prefix "skills/", tail "*.md"
		// "**/*.tmp"        -> prefix "",        tail "*.tmp"
		prefix := ""
		tail := pat
		if i := strings.Index(pat, "**"); i >= 0 {
			prefix = strings.TrimSuffix(pat[:i], "/")
			tail = strings.TrimPrefix(pat[i+2:], "/")
		}

		// The relative path must live under prefix (if any).
		under := rel
		if prefix != "" {
			if rel == prefix {
				under = ""
			} else if strings.HasPrefix(rel, prefix+"/") {
				under = rel[len(prefix)+1:]
			} else {
				return false
			}
		}

		// Match the tail against the remaining path at any depth: try the whole
		// remainder, then each suffix starting at every "/".
		segs := strings.Split(under, "/")
		for i := range segs {
			suffix := strings.Join(segs[i:], "/")
			if ok, _ := filepath.Match(tail, suffix); ok {
				return true
			}
			if ok, _ := filepath.Match(tail, segs[len(segs)-1]); ok {
				return true
			}
		}
	}
	return false
}

// WalkRoot walks root and returns an Inventory of every file matching the
// include/exclude patterns, with its sha256, size, and mtime. Unreadable files
// are skipped rather than failing the whole walk (a locked file should not
// blind the collector to every other file in the agent).
func WalkRoot(root string, include, exclude []string) (*Inventory, error) {
	inv := &Inventory{Root: root, Files: []FileState{}}

	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			// Skip unreadable subtrees; do not abort the inventory.
			if d != nil && d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if d.IsDir() {
			base := d.Name()
			if base == ".git" {
				return filepath.SkipDir
			}
			return nil
		}

		rel, rerr := filepath.Rel(root, path)
		if rerr != nil {
			rel = path
		}
		rel = filepath.ToSlash(rel)

		if !matchGlob(rel, d.Name(), include, exclude) {
			return nil
		}

		sum, size, herr := HashFile(path)
		if herr != nil {
			return nil // unreadable file: skip, keep walking
		}

		info, ierr := d.Info()
		var mt time.Time
		if ierr == nil {
			mt = info.ModTime().UTC()
		}

		inv.Files = append(inv.Files, FileState{
			Path:    rel,
			SHA256:  sum,
			Size:    size,
			ModTime: mt,
			Content: readContent(path, size),
		})
		return nil
	})
	if err != nil {
		return nil, err
	}
	return inv, nil
}

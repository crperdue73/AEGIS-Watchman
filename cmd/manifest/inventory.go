package main

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"

	"github.com/crperdue73/AEGIS-Watchman/internal/provenance"
)

// InventoryRoot hashes the workspace-relative files under root that match the
// given globs (relative to root). It mirrors the collector's include semantics:
// a glob is matched with filepath.Glob against filepath.Join(root, pattern).
//
// Returns path -> sha256 (hex). Paths are workspace-relative so a manifest is
// portable across hosts that mount the same workspace at a different absolute
// path.
func InventoryRoot(root string, globs []string) (map[string]string, error) {
	out := map[string]string{}
	info, err := os.Stat(root)
	if err != nil {
		return nil, fmt.Errorf("stat root: %w", err)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("root %q is not a directory", root)
	}

	for _, g := range globs {
		matches, err := filepath.Glob(filepath.Join(root, g))
		if err != nil {
			return nil, fmt.Errorf("bad glob %q: %w", g, err)
		}
		if len(matches) == 0 {
			// A literal path that does not exist is fine (a workspace may not
			// have every optional file); a glob matching nothing is also fine.
			continue
		}
		for _, m := range matches {
			fi, err := os.Stat(m)
			if err != nil {
				return nil, fmt.Errorf("stat %q: %w", m, err)
			}
			if fi.IsDir() {
				continue
			}
			raw, err := os.ReadFile(m)
			if err != nil {
				// A single unreadable file should not abort an approval run.
				continue
			}
			rel, err := filepath.Rel(root, m)
			if err != nil {
				rel = m
			}
			out[filepath.ToSlash(rel)] = provenance.SHA256Hex(raw)
		}
	}
	return out, nil
}

// DefaultInclude globs mirror the collector's notion of "agent identity surface"
// plus the common skill/config shapes, so an approval manifest and a verify scan
// look at the same files by default.
var DefaultInclude = []string{
	"*.md",
	"*.json",
	"*.yaml",
	"*.yml",
	"*.toml",
	"skills/**/*.md",
	"skills/**/*.json",
	"skills/**/*.yaml",
	"skills/**/*.yml",
}

// DefaultExclude prunes noise and VCS metadata from a default scan.
var DefaultExclude = []string{".git", "node_modules", ".venv", "__pycache__"}

// WalkRoot hashes every file under root matching the include globs, skipping any
// directory whose base name is in the exclude set. Unlike InventoryRoot (which
// only globs, and so cannot see a brand-new directory), this walks the tree, so
// a freshly dropped skills/<name>/SKILL.md IS found. That is the CARBONATO case.
func WalkRoot(root string, includes, excludes []string) (map[string]string, error) {
	out := map[string]string{}
	if len(includes) == 0 {
		includes = DefaultInclude
	}
	excl := map[string]bool{}
	for _, e := range excludes {
		excl[e] = true
	}

	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil // skip unreadable entries; a partial scan beats none
		}
		if d.IsDir() {
			if excl[d.Name()] {
				return filepath.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)
		if !matchesAny(rel, includes) {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		out[rel] = provenance.SHA256Hex(raw)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return out, nil
}

// matchesAny reports whether rel matches any of the globs. Patterns use "**" to
// mean "any number of path segments" and a bare "*.md" to mean "-.md in the root
// or any depth" (the shape operators expect from an include list).
func matchesAny(rel string, globs []string) bool {
	base := path.Base(rel)
	for _, g := range globs {
		if ok := matchGlob(rel, base, g); ok {
			return true
		}
	}
	return false
}

func matchGlob(rel, base, glob string) bool {
	// Direct glob match against the relative path.
	if ok, _ := path.Match(glob, rel); ok {
		return true
	}
	// "**/x" -> match x at any depth.
	if strings.HasPrefix(glob, "**/") {
		tail := strings.TrimPrefix(glob, "**/")
		if ok, _ := path.Match(tail, base); ok {
			return true
		}
		// also try each suffix of the path (for multi-segment tails)
		parts := strings.Split(rel, "/")
		for i := 1; i < len(parts); i++ {
			if ok, _ := path.Match(tail, strings.Join(parts[i:], "/")); ok {
				return true
			}
		}
	}
	// A bare pattern with no slash matches a basename at any depth.
	if !strings.Contains(glob, "/") {
		if ok, _ := path.Match(glob, base); ok {
			return true
		}
	}
	return false
}

// SortedPaths returns the map keys sorted, for stable output.
func SortedPaths(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

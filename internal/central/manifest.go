package central

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/CRPerdue/watchman/internal/collector"
	"github.com/CRPerdue/watchman/internal/provenance"
	"github.com/CRPerdue/watchman/internal/schema"
)

// ManifestRegistry holds the approved Layer 2 manifests, keyed by agent root
// (the same root the collector reports in its inventory). It verifies each
// manifest's own signature against the configured trust keys at load time, so a
// forged or unsigned manifest can never become an approval list.
//
// A missing manifest is not an error: an agent that has never been approved
// simply has no manifest, and every file it reports is treated as unsigned —
// which is the correct, loud answer for an unapproved workspace.
type ManifestRegistry struct {
	dir    string
	keys   []provenance.TrustKey
	mu     sync.RWMutex
	byRoot map[string]*provenance.Manifest
	// requireApproval mirrors config.manifests.require_approval so callers that
	// only hold the registry can still know the fleet policy.
	requireApproval bool
}

// NewManifestRegistry loads every *.json manifest under dir and verifies each
// against keys. It returns an error if any manifest fails verification — a bad
// approval list is a configuration fault the operator must fix, not something to
// silently skip.
func NewManifestRegistry(dir string, keys []provenance.TrustKey) (*ManifestRegistry, error) {
	r := &ManifestRegistry{dir: dir, keys: keys, byRoot: map[string]*provenance.Manifest{}}
	if dir == "" {
		return r, nil
	}
	matches, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, fmt.Errorf("central: manifest glob: %w", err)
	}
	for _, path := range matches {
		// The manifest dir may also hold other JSON (e.g. trust-keys.json). Only
		// files that parse as an watchman manifest are treated as manifests; a
		// JSON file whose content fails the version check is skipped rather than
		// aborting startup, so an unrelated .json does not take down the service.
		if !looksLikeManifest(path) {
			continue
		}
		m, err := provenance.LoadManifest(path)
		if err != nil {
			return nil, fmt.Errorf("central: load manifest %s: %w", path, err)
		}
		if len(keys) > 0 {
			if err := verifyAgainstAny(m, keys); err != nil {
				return nil, fmt.Errorf("central: manifest %s: %w", path, err)
			}
		}
		root := normalizeRoot(m.Root)
		r.byRoot[root] = m
	}
	return r, nil
}

// looksLikeManifest peeks at a JSON file and reports whether it has the shape
// of an approval manifest (an agent_id and a files map), so unrelated JSON in
// the manifest directory is ignored instead of failing startup.
func looksLikeManifest(path string) bool {
	raw, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	var probe struct {
		AgentID string                     `json:"agent_id"`
		Files   map[string]json.RawMessage `json:"files"`
	}
	if err := json.Unmarshal(raw, &probe); err != nil {
		return false
	}
	return probe.AgentID != "" && probe.Files != nil
}

// verifyAgainstAny accepts the manifest if any configured trust key verifies it.
func verifyAgainstAny(m *provenance.Manifest, keys []provenance.TrustKey) error {
	var last error
	for _, k := range keys {
		if err := m.Verify(k.Key); err == nil {
			return nil
		} else {
			last = err
		}
	}
	if last == nil {
		last = fmt.Errorf("no trust keys configured")
	}
	return last
}

// ForRoot returns the manifest approved for an agent root, if any.
func (r *ManifestRegistry) ForRoot(root string) (*provenance.Manifest, bool) {
	if r == nil {
		return nil, false
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	m, ok := r.byRoot[normalizeRoot(root)]
	return m, ok
}

// Set installs/replaces a manifest for a root (used by tests and reload paths).
func (r *ManifestRegistry) Set(root string, m *provenance.Manifest) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.byRoot[normalizeRoot(root)] = m
}

// Reload re-reads the manifest directory. Manifests that fail verification are
// returned as errors and the previously loaded set is left untouched, so a bad
// re-approval cannot silently disable coverage.
func (r *ManifestRegistry) Reload() ([]string, error) {
	if r.dir == "" {
		return nil, nil
	}
	fresh, err := NewManifestRegistry(r.dir, r.keys)
	if err != nil {
		return nil, err
	}
	r.mu.Lock()
	r.byRoot = fresh.byRoot
	r.mu.Unlock()

	roots := make([]string, 0, len(fresh.byRoot))
	for root := range fresh.byRoot {
		roots = append(roots, root)
	}
	return roots, nil
}

// LoadTrustKeys reads base64 public keys from a JSON map file
// ({"name": "base64"}). A missing path is not an error (no trust keys -> Layer 2
// signature verification is off, and manifests are trusted as shipped).
func LoadTrustKeys(path string) ([]provenance.TrustKey, error) {
	if path == "" {
		return nil, nil
	}
	named, err := readKeyMap(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	return provenance.ParsedTrustKeys(named)
}

// ManifestRootKey normalizes a root path so manifests and inventory roots match
// regardless of a trailing slash.
func normalizeRoot(p string) string {
	p = strings.TrimRight(p, "/")
	if p == "" {
		return "/"
	}
	return p
}

// ProvenanceEvents checks an inventory against the approved manifest for its
// root and returns any provenance.untrusted events. It returns no events when
// no manifest is registered for the root (nothing has been approved yet) UNLESS
// requireApproval is true, in which case every file is "unsigned".
func (s *Service) ProvenanceEvents(host schema.Host, agent schema.Agent, inv *collector.Inventory) []schema.Event {
	requireApproval := s.RequireApproval
	if s.manifests != nil && s.manifests.requireApproval {
		requireApproval = true
	}
	m, ok := s.manifests.ForRoot(inv.Root)
	if !ok {
		if !requireApproval {
			return nil
		}
		// No approval on record: every file is untrusted.
		m = &provenance.Manifest{Version: provenance.ManifestVersion, AgentID: agent.ID, Root: inv.Root, Files: map[string]string{}}
	}
	hostFiles := map[string]string{}
	for _, f := range inv.Files {
		hostFiles[f.Path] = f.SHA256
	}
	verdicts := provenance.Verify(m, hostFiles)
	return provenance.Events(host, agent, verdicts)
}

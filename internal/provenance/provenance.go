// Package provenance implements Layer 2 detection: is this agent file one we
// signed and approved, and is it still byte-for-byte what we signed?
//
// Layer 1 asks "did it change?" Layer 2 asks "was it ever ours?" A CARBONATO
// operator does not need to modify an existing file to win — dropping a new
// skill into a workspace is enough, and nothing has "drifted" yet. Layer 2
// catches that: a file the manifest does not account for is untrusted.
//
// The manifest is a signed inventory of (path -> sha256) for one agent root.
// It is deliberately simple and offline-verifiable: an Ed25519 signature over a
// canonical JSON body, so an operator can approve a workspace on a laptop and
// ship the manifest to the fleet without a PKI.
package provenance

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"time"
)

// ManifestVersion is the on-disk manifest format version.
const ManifestVersion = 1

// Manifest is the approved, signed state of one agent root: every file we expect
// to be present, and the hash it must have.
type Manifest struct {
	Version   int       `json:"version"`
	AgentID   string    `json:"agent_id"`
	Root      string    `json:"root,omitempty"`
	CreatedAt time.Time `json:"created_at"`
	// Files maps a workspace-relative path to its approved sha256 (hex).
	Files map[string]string `json:"files"`
	// Signature is a base64 Ed25519 signature over the canonical body.
	Signature string `json:"signature,omitempty"`
}

// canonicalBody returns the deterministic bytes that get signed/verified. It
// excludes the signature field itself and sorts keys, so a manifest round-tripped
// through YAML, JSON, or a reformatter still verifies.
func (m *Manifest) canonicalBody() ([]byte, error) {
	paths := make([]string, 0, len(m.Files))
	for p := range m.Files {
		paths = append(paths, p)
	}
	sort.Strings(paths)

	type kv struct {
		Path string `json:"path"`
		Hash string `json:"sha256"`
	}
	body := struct {
		Version   int    `json:"version"`
		AgentID   string `json:"agent_id"`
		Root      string `json:"root,omitempty"`
		CreatedAt string `json:"created_at"`
		Files     []kv   `json:"files"`
	}{
		Version:   m.Version,
		AgentID:   m.AgentID,
		Root:      m.Root,
		CreatedAt: m.CreatedAt.UTC().Format(time.RFC3339Nano),
		Files:     make([]kv, 0, len(paths)),
	}
	for _, p := range paths {
		body.Files = append(body.Files, kv{Path: p, Hash: m.Files[p]})
	}
	return json.Marshal(body)
}

// Sign sets Signature using an Ed25519 private key.
func (m *Manifest) Sign(priv ed25519.PrivateKey) error {
	if len(priv) != ed25519.PrivateKeySize {
		return fmt.Errorf("provenance: bad private key size %d", len(priv))
	}
	body, err := m.canonicalBody()
	if err != nil {
		return err
	}
	m.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(priv, body))
	return nil
}

// Verify checks the manifest's own signature against a public key. A manifest
// that does not verify must not be trusted as an approval list.
func (m *Manifest) Verify(pub ed25519.PublicKey) error {
	if m.Signature == "" {
		return errors.New("provenance: manifest is unsigned")
	}
	sig, err := base64.StdEncoding.DecodeString(m.Signature)
	if err != nil {
		return fmt.Errorf("provenance: decode signature: %w", err)
	}
	body, err := m.canonicalBody()
	if err != nil {
		return err
	}
	if !ed25519.Verify(pub, body, sig) {
		return errors.New("provenance: manifest signature does not verify")
	}
	return nil
}

// TrustKey is an Ed25519 public key the verifier accepts, with a human label for
// the event payload (so an operator sees "signed by: ops-2026-q3", not base64).
type TrustKey struct {
	Name string
	Key  ed25519.PublicKey
}

// ParsedTrustKeys builds trust keys from base64 public keys.
func ParsedTrustKeys(named map[string]string) ([]TrustKey, error) {
	out := make([]TrustKey, 0, len(named))
	for name, b64 := range named {
		raw, err := base64.StdEncoding.DecodeString(b64)
		if err != nil {
			return nil, fmt.Errorf("provenance: trust key %q: %w", name, err)
		}
		if len(raw) != ed25519.PublicKeySize {
			return nil, fmt.Errorf("provenance: trust key %q: bad size %d", name, len(raw))
		}
		out = append(out, TrustKey{Name: name, Key: ed25519.PublicKey(raw)})
	}
	return out, nil
}

// SHA256Hex is the hash form the manifest stores and the collector produces.
func SHA256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// LoadManifest reads and parses a manifest from path.
func LoadManifest(path string) (*Manifest, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("provenance: read manifest: %w", err)
	}
	var m Manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("provenance: parse manifest %q: %w", path, err)
	}
	if m.Version != ManifestVersion {
		return nil, fmt.Errorf("provenance: unsupported manifest version %d", m.Version)
	}
	if m.Files == nil {
		m.Files = map[string]string{}
	}
	return &m, nil
}

// SaveManifest writes a manifest as indented JSON with 0600 perms.
func (m *Manifest) SaveManifest(path string) error {
	raw, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return fmt.Errorf("provenance: marshal manifest: %w", err)
	}
	if err := os.WriteFile(path, append(raw, '\n'), 0o600); err != nil {
		return fmt.Errorf("provenance: write manifest: %w", err)
	}
	return nil
}

// Trust is the provenance verdict for one path.
type Trust int

const (
	// TrustUnsigned: the path is present on host but absent from the manifest.
	TrustUnsigned Trust = iota
	// TrustModified: the path is in the manifest but the on-host hash differs.
	TrustModified
	// TrustOK: present, listed, and hash-matching (including signer-approved).
	TrustOK
	// TrustMissing: listed in the manifest but absent on host (informational;
	// a deleted skill is not an intrusion, but it is worth surfacing).
	TrustMissing
)

// String renders the verdict for logs and event details.
func (t Trust) String() string {
	switch t {
	case TrustUnsigned:
		return "unsigned"
	case TrustModified:
		return "modified"
	case TrustOK:
		return "ok"
	case TrustMissing:
		return "missing"
	default:
		return "unknown"
	}
}

// Verdict is the per-file Layer 2 result.
type Verdict struct {
	Path         string
	Status       Trust
	HashHost     string // sha256 on host, "" if absent
	HashManifest string // sha256 in manifest, "" if unlisted
}

// Verify compares an on-host file set against a manifest and returns a verdict
// per path that matters (unsigned / modified / missing). Matching files are
// omitted from the result to keep events small — a clean file is not news.
//
// hostFiles maps workspace-relative path -> sha256 (hex).
func Verify(m *Manifest, hostFiles map[string]string) []Verdict {
	var out []Verdict
	seen := make(map[string]bool, len(hostFiles))

	for path, hostHash := range hostFiles {
		seen[path] = true
		want, listed := m.Files[path]
		switch {
		case !listed:
			out = append(out, Verdict{Path: path, Status: TrustUnsigned, HashHost: hostHash})
		case want != hostHash:
			out = append(out, Verdict{Path: path, Status: TrustModified, HashHost: hostHash, HashManifest: want})
		}
	}
	for path, want := range m.Files {
		if !seen[path] {
			out = append(out, Verdict{Path: path, Status: TrustMissing, HashManifest: want})
		}
	}

	// Deterministic order: path, then status, so events are stable.
	sort.Slice(out, func(i, j int) bool {
		if out[i].Path != out[j].Path {
			return out[i].Path < out[j].Path
		}
		return out[i].Status < out[j].Status
	})
	return out
}

// BuildManifest creates an (unsigned) manifest from an on-host file map, for the
// "approve this workspace now" operator workflow.
func BuildManifest(agentID, root string, hostFiles map[string]string) *Manifest {
	files := make(map[string]string, len(hostFiles))
	for p, h := range hostFiles {
		files[p] = h
	}
	return &Manifest{
		Version:   ManifestVersion,
		AgentID:   agentID,
		Root:      root,
		CreatedAt: time.Now().UTC(),
		Files:     files,
	}
}

// SeverityFor maps a verdict to an event severity. An unlisted (unsigned) file
// is the CARBONATO drop-a-skill case and ranks high; a modified-but-signed file
// is a weaker signal (it may be a legitimate edit awaiting re-approval).
func SeverityFor(v Verdict) string {
	switch v.Status {
	case TrustUnsigned:
		return "high"
	case TrustModified:
		return "medium"
	case TrustMissing:
		return "low"
	default:
		return "info"
	}
}

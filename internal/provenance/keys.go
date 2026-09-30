package provenance

// Detached signatures for published artifacts.
//
// The Layer 2 manifest signs a canonical JSON *body* (see Manifest.Sign). A
// published rule-pack pin file is not JSON — it is the lockfile
// rules/packs.pins — so it needs a detached Ed25519 signature over that file's
// exact bytes: the same key material and the same offline-verifiable story,
// applied to a different payload shape. These helpers are what
// `agentids-rules sign-pins` / `verify --pubkey` use, so a fleet can trust a
// *third-party* pack set and not only one it built itself.
import (
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"strings"
)

// LoadPrivateKey reads a base64 Ed25519 private key — the exact format
// `agentids-manifest keygen` writes.
func LoadPrivateKey(path string) (ed25519.PrivateKey, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("provenance: read key: %w", err)
	}
	b, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil {
		return nil, fmt.Errorf("provenance: decode key: %w", err)
	}
	if len(b) != ed25519.PrivateKeySize {
		return nil, fmt.Errorf("provenance: bad private key size %d", len(b))
	}
	return ed25519.PrivateKey(b), nil
}

// LoadPublicKey reads a base64 Ed25519 public key. When path names a private
// key — some operators only keep the one file — its ".pub" companion is tried,
// so a common slip does not look like a corrupt key.
func LoadPublicKey(path string) (ed25519.PublicKey, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		if raw2, err2 := os.ReadFile(path + ".pub"); err2 == nil {
			raw = raw2
		} else {
			return nil, fmt.Errorf("provenance: read public key: %w", err)
		}
	}
	b, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil {
		return nil, fmt.Errorf("provenance: decode public key: %w", err)
	}
	if len(b) == ed25519.PrivateKeySize {
		// A private key file was given where a public one was expected: derive
		// the public half rather than making the operator split files.
		return ed25519.PrivateKey(b).Public().(ed25519.PublicKey), nil
	}
	if len(b) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("provenance: bad public key size %d", len(b))
	}
	return ed25519.PublicKey(b), nil
}

// SignDetached returns the base64 (standard) Ed25519 signature over data. It is
// the detached-signature counterpart to Manifest.Sign.
func SignDetached(priv ed25519.PrivateKey, data []byte) (string, error) {
	if len(priv) != ed25519.PrivateKeySize {
		return "", fmt.Errorf("provenance: bad private key size %d", len(priv))
	}
	return base64.StdEncoding.EncodeToString(ed25519.Sign(priv, data)), nil
}

// VerifyDetached checks a base64 signature over data against pub. A signature
// that does not verify must not be trusted — the caller should abort before
// acting on the signed artifact.
func VerifyDetached(pub ed25519.PublicKey, data []byte, sigB64 string) error {
	if len(pub) != ed25519.PublicKeySize {
		return fmt.Errorf("provenance: bad public key size %d", len(pub))
	}
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(sigB64))
	if err != nil {
		return fmt.Errorf("provenance: decode signature: %w", err)
	}
	if !ed25519.Verify(pub, data, sig) {
		return errors.New("provenance: signature does not verify")
	}
	return nil
}

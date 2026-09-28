package central

import (
	"encoding/json"
	"os"
)

// readKeyMap reads a JSON object of {"name": "base64-ed25519-pubkey"}.
func readKeyMap(path string) (map[string]string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var m map[string]string
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	return m, nil
}

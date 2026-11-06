package central

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// Beat 801. `detail_capped` shipped in beat 799/800 and is documented in docs/fleet-view.md as a
// field an operator's UI can key on. Nothing would have caught it being renamed, retyped, or
// dropped, because every existing test decodes into Go structs -- rename the struct tag and the
// decoder simply stops filling the field, tests keep passing, and a consumer's UI goes quiet.
//
// These tests read the WIRE NAMES, not the Go field names. That is the whole point: the Go name is
// ours to change, the JSON name is a promise to someone else.

// documentedFleetViewKeys lists every top-level field docs/fleet-view.md tells a consumer to expect,
// mapped to the JSON type it must arrive as. Adding a field here is a delivery: it has to land in
// the doc in the same beat, which is why the test also reads the doc.
//
// `hosts` is checked structurally rather than by type, since it is an array.
var documentedFleetViewKeys = map[string]string{
	"generated_at":  "string",
	"total_events":  "number",
	"truncated":     "bool",
	"scan_limit":    "number",
	"detail_capped": "bool",
}

// alwaysPresentFleetViewKeys are the fields that must appear on EVERY response, including an
// empty one. The rest are omitempty and legitimately absent when they have nothing to say --
// listing them here would be asserting that a field is always set when it is designed not to be.
var alwaysPresentFleetViewKeys = []string{"generated_at", "total_events", "hosts"}

// optionalFleetViewKeys are deliberately omitempty: present only when they carry information.
// A consumer must treat absence as "nothing to report", not "old server". Recorded here so the
// distinction is a decision this file makes rather than a detail someone rediscovers later.
var optionalFleetViewKeys = map[string]string{
	"scan_limit":      "number",
	"truncated":       "bool",
	"detail_capped":   "bool",
	"dropped_unnamed": "number",
}

// documentedFleetHostKeys is the same contract one level down: the per-host object a consumer
// iterates. A rollup field renamed here breaks a table column silently.
var documentedFleetHostKeys = map[string]string{
	"host_id":      "string",
	"host_name":    "string",
	"total":        "number",
	"max_severity": "string",
	"last_seen":    "string",
	"by_severity":  "object",
	"by_technique": "object",
	"by_source":    "object",
	"by_pack":      "object",
	"recent":       "array",
}

// jsonTypeOf names a decoded JSON value in the vocabulary of the contract table. It is
// deliberately coarse: the contract is about the shape a consumer can rely on, not about
// int-vs-float, which encoding/json resolves the same way for any consumer.
func jsonTypeOf(t *testing.T, v any) string {
	t.Helper()
	switch v.(type) {
	case string:
		return "string"
	case bool:
		return "bool"
	case float64, int, int64:
		return "number"
	case map[string]any:
		return "object"
	case []any:
		return "array"
	case nil:
		return "null"
	default:
		return reflect.TypeOf(v).String()
	}
}

// A full-detail response must carry every documented key with the documented type. The store here
// holds records so the view is populated -- an empty view would let "absent" masquerade as "fine".
func TestFleetJSONWireContractTopLevel(t *testing.T) {
	_, body := fleetJSON(t, loudQuietStore(t, 12), "?limit=100&recent=3")

	var raw map[string]any
	if err := json.Unmarshal([]byte(body), &raw); err != nil {
		t.Fatalf("decode into generic map failed: %v", err)
	}

	for _, key := range alwaysPresentFleetViewKeys {
		if key == "hosts" {
			continue // checked structurally below
		}
		wantType, ok := documentedFleetViewKeys[key]
		if !ok {
			t.Fatalf("contract lists %q as always-present but gives it no type", key)
		}
		val, present := raw[key]
		if !present {
			t.Errorf("documented field %q is ABSENT. docs/fleet-view.md tells a consumer it "+
				"exists; a missing key with no error is the quietest kind of breaking change. "+
				"Response keys: %v", key, sortedKeys(raw))
			continue
		}
		if got := jsonTypeOf(t, val); got != wantType {
			t.Errorf("field %q arrives as %s, documented as %s", key, got, wantType)
		}
	}

	// Optional fields: type-checked only when present, so a retype is still caught but an
	// honest omission is not punished.
	for key, wantType := range optionalFleetViewKeys {
		val, present := raw[key]
		if !present {
			continue
		}
		if got := jsonTypeOf(t, val); got != wantType {
			t.Errorf("optional field %q arrives as %s, documented as %s", key, got, wantType)
		}
	}

	// BEAT 801, caught by mutation: "skip it when absent" makes an omitempty field UNRENAMEABLE-
	// PROOF -- rename the tag and the key is merely absent, which this test treated as an honest
	// omission. C1 and C2 both survived, in the file written to catch exactly that. An optional
	// field is not exempt from the wire contract; it is only exempt from being always present.
	//
	// So: if a documented optional field is ABSENT, nothing in its place may be present. Any
	// unknown top-level key is a rename or a leak, and either one changes the contract.
	known := map[string]bool{}
	for k := range documentedFleetViewKeys {
		known[k] = true
	}
	for k := range optionalFleetViewKeys {
		known[k] = true
	}
	known["hosts"] = true
	for _, k := range sortedKeys(raw) {
		if !known[k] {
			t.Errorf("undocumented top-level key %q in the response. Either a field was "+
				"renamed (its documented name is now absent and this one appeared) or a new "+
				"field shipped without being documented. Response keys: %v", k, sortedKeys(raw))
		}
	}

	if _, ok := raw["hosts"]; !ok {
		t.Fatalf("no hosts array in the response -- the fleet view without a fleet")
	}
	if got := jsonTypeOf(t, raw["hosts"]); got != "array" {
		t.Fatalf("hosts arrives as %s, want array", got)
	}
	hosts := raw["hosts"].([]any)
	if len(hosts) == 0 {
		t.Fatalf("hosts is empty; the contract test would then prove nothing about host objects")
	}
}

// The per-host object a consumer iterates.
func TestFleetJSONWireContractHostObject(t *testing.T) {
	_, body := fleetJSON(t, loudQuietStore(t, 12), "?limit=100&recent=3")

	var raw struct {
		Hosts []map[string]any `json:"hosts"`
	}
	if err := json.Unmarshal([]byte(body), &raw); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(raw.Hosts) == 0 {
		t.Fatalf("no hosts to contract-check")
	}
	for i, h := range raw.Hosts {
		for key, wantType := range documentedFleetHostKeys {
			// by_* maps are omitempty in the struct; a host with no technique rollup legitimately
			// omits them. Everything else must be present.
			val, present := h[key]
			if !present {
				if len(key) > 3 && key[:3] == "by_" {
					continue
				}
				t.Errorf("host[%d] (%v) omits documented field %q", i, h["host_id"], key)
				continue
			}
			if got := jsonTypeOf(t, val); got != wantType && val != nil {
				t.Errorf("host[%d] field %q arrives as %s, documented as %s",
					i, key, got, wantType)
			}
		}
		// Same rename hole as the top level: a renamed host field shows up as an unknown key.
		for _, k := range sortedKeys(h) {
			if _, ok := documentedFleetHostKeys[k]; !ok {
				t.Errorf("host[%d] carries undocumented key %q (renamed, or shipped "+
					"undocumented?) -- documented host keys: %v", i, k,
					sortedHostKeys())
			}
		}
	}
}

// `detail_capped` must be absent when nothing was trimmed. This is a negative contract: a
// consumer that renders "details trimmed" on any non-empty flag would otherwise cry wolf on
// every complete response.
func TestFleetJSONWireContractDetailCappedOmittedWhenNotTrimmed(t *testing.T) {
	// recent=100 against a host with 12 records: the sample holds everything, so nothing was
	// trimmed and the caveat must not appear.
	_, body := fleetJSON(t, loudQuietStore(t, 12), "?limit=100&recent=100")

	var raw map[string]any
	if err := json.Unmarshal([]byte(body), &raw); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if _, present := raw["detail_capped"]; present {
		t.Errorf("detail_capped is present on a complete view (body=%s). The handler sets it "+
			"whenever a detail sample was requested, not when one was actually trimmed -- "+
			"which makes the flag a report about the REQUEST, not the response. If that is "+
			"the intended contract, docs/fleet-view.md must say so; as written it says the "+
			"field means the detail was trimmed", body)
	}
}

// And the doc and the code must agree on which fields exist. A field added to the struct but
// never documented is invisible to a consumer reading the doc; a field documented but renamed
// in code sends them to a dead key. Both are caught here rather than by a customer.
func TestFleetWireContractMatchesDocumentation(t *testing.T) {
	doc := readDocFile(t, "fleet-view.md")

	for key := range documentedFleetViewKeys {
		if !docMentionsJSONKey(doc, key) {
			t.Errorf("field %q is covered by this contract test but never appears in "+
				"docs/fleet-view.md -- either document it or drop it from the contract", key)
		}
	}
	for key := range documentedFleetHostKeys {
		if !docMentionsJSONKey(doc, key) {
			t.Errorf("host field %q never appears in docs/fleet-view.md", key)
		}
	}
}

// ---- helpers ----

// sortedKeys returns a map's keys in stable order, for error messages that a human can diff.
func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// readDocFile reads a file from docs/ relative to this package, so the check does not depend on
// the working directory a test binary happens to run in.
func readDocFile(t *testing.T, name string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "docs", name))
	if err != nil {
		t.Fatalf("read docs/%s: %v", name, err)
	}
	return string(b)
}

// docMentionsJSONKey reports whether the doc names this wire key as a quoted JSON token.
//
// It matches `"detail_capped"` and `detail_capped` but not `detail_capped_extra`; substring
// matching alone would let a renamed field slide by because its old name is a prefix of the new.
// Bare `detail_capped` is allowed because the doc's prose table names fields unquoted.
func docMentionsJSONKey(doc, key string) bool {
	for _, needle := range []string{`"` + key + `"`, "`" + key + "`", key} {
		idx := 0
		for {
			i := strings.Index(doc[idx:], needle)
			if i < 0 {
				break
			}
			start := idx + i
			end := start + len(needle)
			// Reject a match that is part of a longer identifier.
			beforeOK := start == 0 || !isIdentByte(doc[start-1])
			afterOK := end >= len(doc) || !isIdentByte(doc[end])
			if beforeOK && afterOK {
				return true
			}
			idx = end
		}
	}
	return false
}

func isIdentByte(b byte) bool {
	return b == '_' || b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || b >= '0' && b <= '9'
}

func sortedHostKeys() []string {
	out := make([]string, 0, len(documentedFleetHostKeys))
	for k := range documentedFleetHostKeys {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

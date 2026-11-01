package schema

// The event wire format exists in two places that must not drift: the Go types
// in this package (what central and the collector actually emit) and the shipped
// JSON Schema (watchman.event.schema.json, what the README calls "the public
// contract, versioned from day one" — what third parties validate against).
//
// Every other public contract in this repo is held to its source by a guard:
// routes to docs (central), config keys to docs (collector), the rule table to
// the pack (rules), container claims to the image (container-check). This one had
// none. These tests are that guard: they reflect over the Go structs and hold
// the schema's property names, event enum, severity enum, and schema const to
// what the code declares.
//
// Scope, stated so a pass is trusted: this checks *names* and *enumerations*, not
// nullability or `required`. The Go tags use `omitempty` where the schema uses a
// `["string","null"]` type; the schema is a permissive superset there and that is
// intentional (a consumer must tolerate an absent optional key). A rename, an
// added field, or a dropped one fails; a nullability tweak does not. The latter
// is a smaller change than the former and is reviewed by eye.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestEventSchemaMatchesGoTypes(t *testing.T) {
	doc := loadEventSchema(t)

	// Each Go struct and the schema node whose `properties` must name the same
	// set of JSON keys. The top-level Event maps to the schema root.
	cases := []struct {
		name string
		typ  reflect.Type
		node map[string]any
	}{
		{"Event", reflect.TypeOf(Event{}), doc},
		{"Host", reflect.TypeOf(Host{}), subNode(t, doc, "host")},
		{"Agent", reflect.TypeOf(Agent{}), subNode(t, doc, "agent")},
		{"FileInfo", reflect.TypeOf(FileInfo{}), subNode(t, doc, "file")},
		{"Action", reflect.TypeOf(Action{}), subNode(t, doc, "action")},
		{"Rule", reflect.TypeOf(Rule{}), subNode(t, doc, "rule")},
		{"Actor", reflect.TypeOf(Actor{}), subNode(t, doc, "actor")},
	}
	for _, c := range cases {
		got := structJSONNames(t, c.typ)
		want := propertyNames(t, c.node, c.name)
		if diff := setDiff(want, got); len(diff) > 0 {
			t.Errorf("%s: schema declares propert%s the Go type does not emit: %v",
				c.name, plural(len(diff)), diff)
		}
		if diff := setDiff(got, want); len(diff) > 0 {
			t.Errorf("%s: Go type emits JSON key(s) the schema omits: %v",
				c.name, diff)
		}
	}
}

func TestEventSchemaEnumsMatchConstants(t *testing.T) {
	doc := loadEventSchema(t)

	if got, want := enumOf(t, doc, "event"), setOf(
		EventIdentityDrift, EventProvenanceUntrusted, EventBehaviorRuleMatch,
	); !sameSet(got, want) {
		t.Errorf("schema event enum = %v, want %v", sortedKeys(got), sortedKeys(want))
	}
	if got, want := enumOf(t, doc, "severity"), setOf(
		SeverityInfo, SeverityLow, SeverityMedium, SeverityHigh, SeverityCritical,
	); !sameSet(got, want) {
		t.Errorf("schema severity enum = %v, want %v", sortedKeys(got), sortedKeys(want))
	}

	if got := constOf(t, doc, "schema"); got != Version {
		t.Errorf("schema `const` for the version field = %q, want package Version %q", got, Version)
	}
}

// ---- helpers ----------------------------------------------------------------

func loadEventSchema(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "watchman.event.schema.json"))
	if err != nil {
		t.Fatalf("read watchman.event.schema.json: %v", err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse watchman.event.schema.json: %v", err)
	}
	if _, ok := doc["properties"]; !ok {
		t.Fatalf("schema has no top-level properties object — is it still a schema?")
	}
	return doc
}

// subNode returns the schema node at properties[name], insisting it exists.
func subNode(t *testing.T, doc map[string]any, name string) map[string]any {
	t.Helper()
	props, ok := doc["properties"].(map[string]any)
	if !ok {
		t.Fatalf("schema root has no properties object")
	}
	node, ok := props[name].(map[string]any)
	if !ok {
		t.Fatalf("schema has no properties.%s object", name)
	}
	return node
}

func propertyNames(t *testing.T, node map[string]any, where string) map[string]bool {
	t.Helper()
	props, ok := node["properties"].(map[string]any)
	if !ok {
		t.Fatalf("%s: node has no `properties` object", where)
	}
	set := map[string]bool{}
	for k := range props {
		set[k] = true
	}
	return set
}

func structJSONNames(t *testing.T, typ reflect.Type) map[string]bool {
	t.Helper()
	set := map[string]bool{}
	for i := 0; i < typ.NumField(); i++ {
		name := strings.Split(typ.Field(i).Tag.Get("json"), ",")[0]
		if name == "" || name == "-" {
			continue
		}
		set[name] = true
	}
	if len(set) == 0 {
		t.Fatalf("%s: no json tags found — reflection is broken, fix it before trusting a pass", typ.Name())
	}
	return set
}

func enumOf(t *testing.T, doc map[string]any, field string) map[string]bool {
	t.Helper()
	props, _ := doc["properties"].(map[string]any)
	f, ok := props[field].(map[string]any)
	if !ok {
		t.Fatalf("schema has no properties.%s", field)
	}
	raw, ok := f["enum"].([]any)
	if !ok {
		t.Fatalf("schema properties.%s has no enum array", field)
	}
	set := map[string]bool{}
	for _, v := range raw {
		s, ok := v.(string)
		if !ok {
			t.Fatalf("schema properties.%s enum has a non-string value %v", field, v)
		}
		set[s] = true
	}
	return set
}

func constOf(t *testing.T, doc map[string]any, field string) string {
	t.Helper()
	props, _ := doc["properties"].(map[string]any)
	f, ok := props[field].(map[string]any)
	if !ok {
		t.Fatalf("schema has no properties.%s", field)
	}
	s, ok := f["const"].(string)
	if !ok {
		t.Fatalf("schema properties.%s has no string const", field)
	}
	return s
}

func setOf(items ...string) map[string]bool {
	set := map[string]bool{}
	for _, s := range items {
		set[s] = true
	}
	return set
}

// setDiff returns the members of a not present in b.
func setDiff(a, b map[string]bool) []string {
	var out []string
	for k := range a {
		if !b[k] {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

func sameSet(a, b map[string]bool) bool {
	return len(setDiff(a, b)) == 0 && len(setDiff(b, a)) == 0
}

func sortedKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func plural(n int) string {
	if n == 1 {
		return "y"
	}
	return "ies"
}

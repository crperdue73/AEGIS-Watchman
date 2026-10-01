package collector

import (
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// docs/collector-config.md calls itself "the exhaustive key reference" and names
// internal/collector/config.go (and the telemetry blocks it embeds) as the source
// of truth. That is a completeness claim about a hand-maintained document, which
// is exactly the kind that rots: a key added to the struct and not to the page
// leaves an operator unable to find the knob they need. This guard reflects over
// the real config types — so it can never drift from the struct — and fails when
// a key is undocumented.
//
// The page may document a nested block either as a bare key (`docker`) or as a
// qualified heading (`behavior.docker`); both are accepted.

func TestCollectorConfigDocsAreExhaustive(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "docs", "collector-config.md"))
	if err != nil {
		t.Fatalf("read docs/collector-config.md: %v", err)
	}
	doc := string(raw)

	keys := map[string]bool{}
	walkYAML(reflect.TypeOf(Config{}), keys, map[reflect.Type]bool{})
	if len(keys) < 20 {
		t.Fatalf("reflection found only %d yaml keys — the walk is broken, fix it before trusting a pass", len(keys))
	}

	var missing []string
	for k := range keys {
		// Accept `k` or a dotted form ending in the key, e.g. `behavior.ebpf`.
		re := regexp.MustCompile("`([a-z_][a-z0-9_]*\\.)*" + regexp.QuoteMeta(k) + "`")
		if !re.MatchString(doc) {
			missing = append(missing, k)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Errorf("docs/collector-config.md claims to be the exhaustive key reference but omits %d key(s): %v", len(missing), missing)
	}
}

// walkYAML collects the yaml key names of a struct and every nested struct it
// reaches, descending only into structs defined in this repo (so a stdlib or
// third-party type encountered as a value does not drag its fields in).
func walkYAML(t reflect.Type, out map[string]bool, seen map[reflect.Type]bool) {
	for t.Kind() == reflect.Pointer || t.Kind() == reflect.Slice || t.Kind() == reflect.Array {
		t = t.Elem()
	}
	if t.Kind() != reflect.Struct || seen[t] {
		return
	}
	seen[t] = true
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if name := strings.Split(f.Tag.Get("yaml"), ",")[0]; name != "" && name != "-" {
			out[name] = true
		}
		ft := f.Type
		for ft.Kind() == reflect.Pointer || ft.Kind() == reflect.Slice || ft.Kind() == reflect.Array {
			ft = ft.Elem()
		}
		if ft.Kind() == reflect.Struct && strings.HasPrefix(ft.PkgPath(), "github.com/crperdue73/AEGIS-Watchman/internal/") {
			walkYAML(ft, out, seen)
		}
	}
}

package rules

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// Fuzz targets for the rule-pack and pin parsers, and for the matcher fed an
// arbitrary (untrusted) action.
//
// Rule packs are meant to be shared — the BRIEF's Phase 3 is a Snort-style
// community pack model — so a pack is untrusted input from the moment someone
// else can write one. ParsePack is strict about unknown keys (a typo is a hard
// error, not a silently weakened rule), which means it does a lot of walking over
// attacker-shaped YAML. The contract is the same as any parser: return, never
// panic. A pack that crashes the loader is a pack that can take a fleet's Layer 3
// down on a routine `make pin`/reload.
//
// FuzzEngineMatch goes further: it decodes an arbitrary JSON action and runs it
// through the REAL shipped pack. A rule predicate reads values an attacker
// controls (a crafted audit line can set `pid` to a string, `argv` to a number),
// so the matcher must tolerate any shape in `detail`, not just the well-formed
// one. A panic here is the same denial of service as a parser panic.
//
//	go test ./internal/rules -run=^$ -fuzz=FuzzParsePack -fuzztime=30s

func FuzzParsePack(f *testing.F) {
	f.Add([]byte("name: example\nversion: 1\nrules:\n  - id: a.b\n    action: process.exec\n    match:\n      exe_glob: \"**/curl\"\n    severity: high\n"))
	f.Add([]byte("name: x\nrules: []\n"))
	f.Add([]byte("name: {}\nnot: [valid: yaml"))
	f.Add([]byte(""))
	f.Fuzz(func(t *testing.T, data []byte) {
		_, _ = ParsePack(data)
	})
}

func FuzzParsePins(f *testing.F) {
	f.Add([]byte("deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef  carbonato@7  carbonato.yaml\n"))
	f.Add([]byte("# comment\n\n"))
	f.Add([]byte("only one field\nshort name@1 file\n"))
	f.Fuzz(func(t *testing.T, data []byte) {
		_, _ = ParsePins(data)
	})
}

func FuzzEngineMatch(f *testing.F) {
	packPath := filepath.Join("..", "..", "rules", "carbonato.yaml")
	if _, err := os.Stat(packPath); err != nil {
		f.Skipf("rule pack not found at %s: %v", packPath, err)
	}
	pack, err := LoadPack(packPath)
	if err != nil {
		f.Fatalf("load pack: %v", err)
	}
	eng := NewEngine(pack)

	host := schema.Host{ID: "h1", Name: "test"}
	agent := &schema.Agent{ID: "a1", Root: "/home/agent"}
	at := time.Unix(0, 0).UTC()

	f.Add([]byte(`{"type":"process.exec","detail":{"exe":"/bin/sh","argv":["sh","-c","id"]}}`))
	f.Add([]byte(`{"type":"net.egress","detail":{"service":"slack.com","endpoint":"slack.com 1.2.3.4:443"}}`))
	f.Add([]byte(`{"type":"file.access","detail":{"path":"/home/agent/.ssh/id_rsa"}}`))
	f.Add([]byte(`{"type":"container.start","detail":{"privileged":"true","image":"x"}}`))
	// Wrong shapes on purpose: numbers where strings are expected, and vice versa.
	f.Add([]byte(`{"type":"process.exec","detail":{"pid":"not-a-number","argv":[1,2,3],"exe":42}}`))
	f.Add([]byte(`not json`))

	f.Fuzz(func(t *testing.T, data []byte) {
		var a schema.Action
		if err := json.Unmarshal(data, &a); err != nil {
			return
		}
		_ = eng.Match(host, agent, a, nil, at)
	})
}

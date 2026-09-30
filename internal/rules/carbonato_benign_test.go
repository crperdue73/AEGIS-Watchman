package rules

import (
	"path/filepath"
	"testing"

	"github.com/CRPerdue/agentids/internal/schema"
)

// TestShippedPackQuietOnBenignCorpus is the P1.4 rule-pack quality gate: a
// handful of ordinary-looking actions that a naive glob would trip on must
// produce ZERO matches against the shipped pack. Each case is a "benign
// lookalike" for a rule that was tightened in the quality pass, so this test
// fails if someone re-broadens a glob (e.g. puts back "*.profile*" and catches
// `--profile`).
//
// The corpus is deliberately small and honest: it covers the lookalikes we know
// about, not an exhaustive benign set. A rule that fires on one of these is a
// tuning bug, not a detection.
func TestShippedPackQuietOnBenignCorpus(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	e := NewEngine(p)
	host := schema.Host{ID: "builder-01"}

	cases := []struct {
		name   string
		action schema.Action
	}{
		{
			"node run with a --profile flag (not a shell rc write)",
			schema.Action{Type: "process.exec", Detail: map[string]any{
				"exe":  "/usr/bin/node",
				"argv": []any{"node", "--prof", "build.js", "--profile", "prod"},
			}},
		},
		{
			"a bounded wget of a documented artifact (no pipe to shell)",
			schema.Action{Type: "process.exec", Detail: map[string]any{
				"exe":  "/usr/bin/wget",
				"argv": []any{"wget", "https://example.com/releases/v1.2.0.tar.gz"},
			}},
		},
		{
			"a routine ls",
			schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/usr/bin/ls"}},
		},
		{
			"a unit test run (no secret, no egress, no listener)",
			schema.Action{Type: "process.exec", Detail: map[string]any{
				"exe":  "/usr/local/go/bin/go",
				"argv": []any{"go", "test", "./..."},
			}},
		},
		{
			"egress to a package registry (not a chat/C2 endpoint)",
			schema.Action{Type: "net.egress", Detail: map[string]any{
				"endpoint": "registry.npmjs.org 104.16.0.35:443",
			}},
		},
		{
			"internal JSON-RPC over the office network (not a public chain provider)",
			schema.Action{Type: "net.egress", Detail: map[string]any{
				"endpoint": "rpc.internal.example.com 10.0.0.5:443",
			}},
		},
		{
			"a build writing its own output file (not a persistence path)",
			schema.Action{Type: "file.write", Detail: map[string]any{
				"path": "/tmp/build/output.log", "syscall": "openat", "nametype": "CREATE",
			}},
		},
		{
			"a listener on loopback from a dev server the operator started",
			// NOTE: this one is EXPECTED to fire listener_bound — a listener is
			// a listener. It is here to document that we do NOT special-case
			// loopback in v1; a per-host allowlist is a later feature. Marked by
			// the expectation below.
			schema.Action{Type: "net.listen", Detail: map[string]any{
				"listen": "127.0.0.1:8080", "syscall": "bind", "exe": "/usr/bin/python3",
			}},
		},
	}

	// The listener case is a documented, deliberate fire — a probe that a
	// loopback listener still trips the rule (so the test is honest about what
	// "benign" means and does not silently hide a true positive).
	listenerFires := map[string]bool{"a listener on loopback from a dev server the operator started": true}

	for _, c := range cases {
		var got []string
		for _, ev := range e.Match(host, nil, c.action, nil, timeNow()) {
			got = append(got, ev.Rule.ID)
		}
		if listenerFires[c.name] {
			if len(got) == 0 {
				t.Errorf("%s: expected at least one match (documented true positive), got none", c.name)
			}
			continue
		}
		if len(got) != 0 {
			t.Errorf("%s: benign action fired rules %v — a rule was re-broadened", c.name, got)
		}
	}
}

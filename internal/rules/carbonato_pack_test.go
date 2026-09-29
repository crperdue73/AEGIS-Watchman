package rules

import (
	"path/filepath"
	"testing"

	"github.com/CRPerdue/agentids/internal/schema"
)

// TestShippedCarbonatoPackLoaded checks the real rules/carbonato.yaml parses and
// is well-formed — the shipped artifact must never be broken.
func TestShippedCarbonatoPackLoaded(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatalf("shipped carbonato pack does not load: %v", err)
	}
	if p.Name != "carbonato" {
		t.Errorf("pack name = %q", p.Name)
	}
	if len(p.Rules) < 6 {
		t.Errorf("expected at least 6 seed rules, got %d", len(p.Rules))
	}
	ids := map[string]bool{}
	for _, r := range p.Rules {
		if ids[r.ID] {
			t.Errorf("duplicate rule id %s", r.ID)
		}
		ids[r.ID] = true
	}
}

// TestShippedCarbonatoFiresOnExfilChain walks the canonical CARBONATO sequence
// against the shipped pack: secret read, shell spawn, egress, new scope.
func TestShippedCarbonatoFiresOnExfilChain(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	e := NewEngine(p)
	host := schema.Host{ID: "builder-01"}

	fired := func(action schema.Action) []string {
		var ids []string
		for _, ev := range e.Match(host, nil, action, nil, timeNow()) {
			ids = append(ids, ev.Rule.ID)
		}
		return ids
	}
	has := func(ids []string, want string) bool {
		for _, id := range ids {
			if id == want {
				return true
			}
		}
		return false
	}

	// 1. The agent reads an SSH private key (the CARBONATO read leg).
	if got := fired(schema.Action{Type: "file.access", Detail: map[string]any{"path": "/home/vivi/.ssh/id_rsa"}}); !has(got, "carbonato.secret.read_order_follows_constraint") {
		t.Errorf("secret read did not fire: %v", got)
	}
	// 2. A shell command names the key.
	if got := fired(schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/bin/cat", "argv": []any{"cat", "/home/vivi/.ssh/id_rsa"}}}); !has(got, "carbonato.exec.ssh_key_heredoc") {
		t.Errorf("ssh-key argv did not fire: %v", got)
	}
	// 3. Egress to a chat endpoint (the exfil leg).
	if got := fired(schema.Action{Type: "net.egress", Detail: map[string]any{"endpoint": "https://hooks.slack.com/services/T00/B00/XXX"}}); !has(got, "carbonato.exfil.egress_after_change") {
		t.Errorf("chat egress did not fire: %v", got)
	}
	// 4. A brand-new capability request.
	if got := fired(schema.Action{Type: "scope.change", Detail: map[string]any{"novel": "true", "scope": "filesystem.write"}}); !has(got, "carbonato.tool.scope_new") {
		t.Errorf("novel scope did not fire: %v", got)
	}
	// 5. Shell spawn is itself a signal.
	if got := fired(schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "bash"}}); !has(got, "carbonato.exec.shell_spawn") {
		t.Errorf("shell spawn did not fire: %v", got)
	}
	// 6. Persistence via nohup.
	if got := fired(schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/usr/bin/nohup", "argv": []any{"nohup", "./beacon"}}}); !has(got, "carbonato.persist.survives_session") {
		t.Errorf("persistence did not fire: %v", got)
	}

	// A benign action must stay quiet.
	if got := fired(schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/usr/bin/ls"}}); len(got) != 0 {
		t.Errorf("benign ls fired rules: %v", got)
	}
}

// TestShippedCarbonatoFiresOnListener proves the R1 rule fires on a net.listen
// action — the kernel-side counterpart to the argv-based listener rule. This is
// what turns carbonato.net.listener_bound from dead code into a live detection.
func TestShippedCarbonatoFiresOnListener(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	e := NewEngine(p)

	var ids []string
	for _, ev := range e.Match(schema.Host{ID: "h"}, nil,
		schema.Action{Type: "net.listen", Detail: map[string]any{
			"listen":  "0.0.0.0:4444",
			"syscall": "bind",
			"exe":     "/usr/bin/nc",
		}}, nil, timeNow()) {
		ids = append(ids, ev.Rule.ID)
	}
	found := false
	for _, id := range ids {
		if id == "carbonato.net.listener_bound" {
			found = true
		}
	}
	if !found {
		t.Errorf("net.listen did not fire listener_bound: %v", ids)
	}
}

// TestShippedCarbonatoFiresOnContainer walks the container-runtime half of the
// CARBONATO entry vector against the shipped pack: a privileged, host-root
// mounted container appearing on a host. This is the action the docker events
// adapter emits (internal/telemetry/docker.go) — the detection that does not
// need the `docker` CLI to have been typed, so it survives an intrusion that
// went straight at the daemon on :2375.
func TestShippedCarbonatoFiresOnContainer(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	e := NewEngine(p)

	action := schema.Action{Type: "container.start", Detail: map[string]any{
		"container":  "a1b2c3d4",
		"image":      "alpine:latest",
		"name":       "worker",
		"privileged": "true",
		"binds":      "/:/host,/var/run/docker.sock:/var/run/docker.sock",
	}}
	var ids []string
	for _, ev := range e.Match(schema.Host{ID: "h"}, nil, action, nil, timeNow()) {
		ids = append(ids, ev.Rule.ID)
	}
	want := map[string]bool{
		"carbonato.container.privileged_start": true,
		"carbonato.container.host_root_mount":  true,
	}
	for _, id := range ids {
		delete(want, id)
	}
	if len(want) != 0 {
		t.Errorf("container.start missed %v (fired: %v)", want, ids)
	}
}

// TestContainerRuleQuietOnOrdinaryContainer guards the low-noise promise: an
// ordinary container with no host access must NOT fire the container rules.
func TestContainerRuleQuietOnOrdinaryContainer(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	e := NewEngine(p)
	action := schema.Action{Type: "container.start", Detail: map[string]any{
		"container": "c0ffee",
		"image":     "nginx:latest",
		"name":      "web",
	}}
	for _, ev := range e.Match(schema.Host{ID: "h"}, nil, action, nil, timeNow()) {
		t.Errorf("ordinary container fired %s; container rules must stay quiet", ev.Rule.ID)
	}
}

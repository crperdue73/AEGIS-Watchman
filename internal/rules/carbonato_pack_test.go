package rules

import (
	"path/filepath"
	"testing"
	"time"

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
	if got := fired(schema.Action{Type: "file.access", Detail: map[string]any{"path": "/home/agent/.ssh/id_rsa"}}); !has(got, "carbonato.secret.read_order_follows_constraint") {
		t.Errorf("secret read did not fire: %v", got)
	}
	// 2. A shell command names the key.
	if got := fired(schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/bin/cat", "argv": []any{"cat", "/home/agent/.ssh/id_rsa"}}}); !has(got, "carbonato.exec.ssh_key_heredoc") {
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

	// 7. A multi-word phrase rule must fire on SPLIT argv (auditd shape), not
	// just a pre-joined string. This is the P1.4 regression: before the fix,
	// "*pip install*" could never match ["pip","install","evil"].
	if got := fired(schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/usr/bin/pip3", "argv": []any{"pip3", "install", "requests"}}}); !has(got, "carbonato.tool.package_install") {
		t.Errorf("split-argv package install did not fire: %v", got)
	}
	if got := fired(schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "/usr/bin/nc", "argv": []any{"nc", "-l", "4444"}}}); !has(got, "carbonato.net.new_listener") {
		t.Errorf("split-argv nc -l did not fire: %v", got)
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

// TestShippedCarbonatoFiresOnBlockchainC2 covers the v6 Graphalgo leg: an agent
// egressing to a public chain RPC provider is medium on its own, and the same
// egress becomes the critical correlation when an identity file changed shortly
// before. An internal JSON-RPC endpoint (not a public chain provider) stays
// quiet.
func TestShippedCarbonatoFiresOnBlockchainC2(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	e := NewEngine(p)
	host := schema.Host{ID: "h"}

	ids := func(eng *Engine, endpoint string, asOf time.Time) []string {
		var out []string
		for _, ev := range eng.Match(host, nil, schema.Action{
			Type: "net.egress", Detail: map[string]any{"endpoint": endpoint},
		}, nil, asOf) {
			out = append(out, ev.Rule.ID)
		}
		return out
	}
	has := func(ids []string, want string) bool {
		for _, id := range ids {
			if id == want {
				return true
			}
		}
		return false
	}

	// Standalone: the chain-RPC rule fires (no prior drift, so the correlation
	// does not).
	if got := ids(e, "https://sepolia-rollup.arbitrum.io/rpc", t0); !has(got, "carbonato.c2.blockchain_rpc") {
		t.Errorf("arbitrum RPC did not fire the chain rule: %v", got)
	}
	if got := ids(e, "mainnet.infura.io:443", t0); !has(got, "carbonato.c2.blockchain_rpc") {
		t.Errorf("infura RPC did not fire the chain rule: %v", got)
	}

	// Escalation: the same egress after an identity-file change is critical.
	tl := NewTimeline()
	tl.Record(host.ID, t0, schema.EventIdentityDrift, t0)
	eng := NewEngine(p).WithTimeline(tl)
	if got := ids(eng, "arbitrum.io/rpc", t0.Add(time.Minute)); !has(got, "carbonato.exfil.egress_after_identity_change") {
		t.Errorf("chain egress after identity change did not fire the critical correlation: %v", got)
	}

	// Benign lookalike: an internal JSON-RPC endpoint is not a public chain
	// provider and must stay quiet.
	if got := ids(e, "rpc.internal.example.com:443", t0); len(got) != 0 {
		t.Errorf("internal JSON-RPC fired rules (should be quiet): %v", got)
	}
}

// TestShippedCarbonatoFiresOnFileWrite covers the writable half of G3: a
// file.write action naming a persistence path — the path the kernel reported,
// with no command line naming it — fires the persistence rule. This is the
// counterpart to the argv rule (carbonato.persist.cron_or_rc): the same write
// by a wrapped interpreter still fires here.
func TestShippedCarbonatoFiresOnFileWrite(t *testing.T) {
	p, err := LoadPack(filepath.Join("..", "..", "rules", "carbonato.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	e := NewEngine(p)
	host := schema.Host{ID: "h"}

	fired := func(path string) []string {
		var out []string
		for _, ev := range e.Match(host, nil, schema.Action{
			Type: "file.write", Detail: map[string]any{
				"path": path, "syscall": "openat", "nametype": "CREATE",
			},
		}, nil, timeNow()) {
			out = append(out, ev.Rule.ID)
		}
		return out
	}
	has := func(ids []string, want string) bool {
		for _, id := range ids {
			if id == want {
				return true
			}
		}
		return false
	}

	// A cron drop, an rc-file overwrite, an authorized_keys addition — each is a
	// persistence write the kernel named.
	for _, path := range []string{
		"/etc/cron.d/payload",
		"/home/agent/.bashrc",
		"/home/agent/.ssh/authorized_keys",
		"/etc/systemd/system/beacon.service",
	} {
		if got := fired(path); !has(got, "carbonato.persist.file_write") {
			t.Errorf("file.write to %s did not fire persist.file_write: %v", path, got)
		}
	}

	// A write to a working directory is not persistence and must stay quiet.
	if got := fired("/tmp/build/output.log"); len(got) != 0 {
		t.Errorf("benign file.write fired rules (should be quiet): %v", got)
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

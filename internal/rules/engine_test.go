package rules

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

const samplePack = `
name: testpack
version: "1"
rules:
  - id: test.exec.shell
    title: shell spawn
    severity: medium
    match:
      type: process.exec
      exe_any: [sh, bash]
  - id: test.secret.read
    title: ssh key read
    severity: critical
    match:
      type: file.access
      path_glob_any: ["*/.ssh/*", "*id_rsa*"]
  - id: test.exfil.chat
    title: chat egress
    severity: high
    match:
      type: net.egress
      endpoint_glob_any: ["*slack.com*", "*api.telegram.org*"]
  - id: test.scope.new
    title: new scope
    severity: high
    match:
      type: scope.change
      detail_equals:
        novel: "true"
`

func mustPack(t *testing.T, yaml string) *Pack {
	t.Helper()
	p, err := ParsePack([]byte(yaml))
	if err != nil {
		t.Fatalf("parse pack: %v", err)
	}
	return p
}

func TestParsePackBasics(t *testing.T) {
	p := mustPack(t, samplePack)
	if p.Name != "testpack" || p.Version != "1" {
		t.Errorf("pack header wrong: %+v", p)
	}
	if len(p.Rules) != 4 {
		t.Fatalf("expected 4 rules, got %d", len(p.Rules))
	}
	if p.Rules[0].Pack != "testpack" || p.Rules[0].Version != "1" {
		t.Errorf("pack/version not inherited: %+v", p.Rules[0])
	}
}

func TestParsePackRejects(t *testing.T) {
	cases := map[string]string{
		"no name":     "version: \"1\"\nrules: [{id: a.b, severity: low, match: {type: process.exec, exe: sh}}]",
		"no rules":    "name: p\nversion: \"1\"\nrules: []",
		"no id":       "name: p\nrules: [{severity: low, match: {type: process.exec, exe: sh}}]",
		"unqualified": "name: p\nrules: [{id: nosep, severity: low, match: {type: process.exec, exe: sh}}]",
		"no sev":      "name: p\nrules: [{id: a.b, match: {type: process.exec, exe: sh}}]",
		"bad sev":     "name: p\nrules: [{id: a.b, severity: bogus, match: {type: process.exec, exe: sh}}]",
		"empty match": "name: p\nrules: [{id: a.b, severity: low, match: {}}]",
		"dupe id":     "name: p\nrules: [{id: a.b, severity: low, match: {type: process.exec, exe: sh}}, {id: a.b, severity: low, match: {type: process.exec, exe: bash}}]",
		// Strict parsing: a misspelled field must fail loudly, not load a rule
		// that quietly never fires (or silently drops a predicate).
		"unknown match key": "name: p\nrules: [{id: a.b, severity: low, match: {type: process.exec, argv_anyy: [x]}}]",
		"unknown rule key":  "name: p\nrules: [{id: a.b, severity: low, extra: 1, match: {type: process.exec, exe: sh}}]",
		"unknown pack key":  "name: p\nversion: \"1\"\nbogus: 1\nrules: [{id: a.b, severity: low, match: {type: process.exec, exe: sh}}]",
		"misspelled prior":  "name: p\nrules: [{id: a.b, severity: low, match: {type: net.egress, prior_even: {class: x, within: 5m}}}]",
	}
	for name, y := range cases {
		if _, err := ParsePack([]byte(y)); err == nil {
			t.Errorf("%s: expected parse error", name)
		}
	}
}

// TestParsePackUnknownKeyErrorIsActionable pins the message for the most common
// authoring mistake: a misspelled predicate. The error must name the offending
// key, so the fix is obvious without reading the parser.
func TestParsePackUnknownKeyErrorIsActionable(t *testing.T) {
	_, err := ParsePack([]byte("name: p\nrules: [{id: a.b, severity: low, match: {type: process.exec, argv_anyy: [x]}}]"))
	if err == nil {
		t.Fatal("expected an error for a misspelled predicate")
	}
	if !strings.Contains(err.Error(), "argv_anyy") {
		t.Errorf("error should name the bad key, got: %v", err)
	}
}

func TestEngineMatchesProcessExec(t *testing.T) {
	e := NewEngine(mustPack(t, samplePack))
	host := schema.Host{ID: "h1"}

	// /bin/bash -> matches the shell rule via basename.
	evs := e.Match(host, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"exe": "/bin/bash"},
	}, nil, timeNow())
	if len(evs) != 1 || evs[0].Rule.ID != "test.exec.shell" {
		t.Fatalf("expected shell rule, got %+v", evs)
	}
	if evs[0].Severity != schema.SeverityMedium {
		t.Errorf("severity = %s", evs[0].Severity)
	}
	if evs[0].Event != schema.EventBehaviorRuleMatch {
		t.Errorf("event class = %s", evs[0].Event)
	}
	if evs[0].Rule.Pack != "testpack" || evs[0].Rule.Version != "1" {
		t.Errorf("rule provenance wrong: %+v", evs[0].Rule)
	}
	if evs[0].Action == nil || evs[0].Action.Type != "process.exec" {
		t.Errorf("action not attached: %+v", evs[0].Action)
	}
	if evs[0].TS.IsZero() || evs[0].Schema != schema.Version {
		t.Errorf("envelope incomplete: %+v", evs[0])
	}
}

func TestEngineNoMatchIsSilent(t *testing.T) {
	e := NewEngine(mustPack(t, samplePack))
	evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"exe": "/usr/bin/ls"},
	}, nil, timeNow())
	if len(evs) != 0 {
		t.Fatalf("expected no events, got %+v", evs)
	}
}

// TestEnginePropagatesTechnique: a rule that declares a technique must ride it
// onto the emitted event's Rule, so downstream consumers (central's fleet
// rollup) can group by technique without re-reading the pack.
func TestEnginePropagatesTechnique(t *testing.T) {
	pack := `
name: techpack
version: "1"
rules:
  - id: tech.exec.shell
    title: shell
    severity: medium
    technique: phase3/execution
    match:
      type: process.exec
      exe_any: [bash]
`
	e := NewEngine(mustPack(t, pack))
	evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"exe": "/bin/bash"},
	}, nil, timeNow())
	if len(evs) != 1 {
		t.Fatalf("expected 1 event, got %+v", evs)
	}
	if evs[0].Rule == nil || evs[0].Rule.Technique != "phase3/execution" {
		t.Errorf("technique not propagated: %+v", evs[0].Rule)
	}
}

func TestEngineSecretReadByPath(t *testing.T) {
	e := NewEngine(mustPack(t, samplePack))
	for _, p := range []string{"/home/vivi/.ssh/id_rsa", "/root/.ssh/id_ed25519", "/home/x/id_rsa"} {
		evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
			Type:   "file.access",
			Detail: map[string]any{"path": p},
		}, nil, timeNow())
		if len(evs) != 1 || evs[0].Rule.ID != "test.secret.read" {
			t.Errorf("path %s: expected secret.read, got %+v", p, evs)
		}
	}
}

func TestEngineEgressEndpoints(t *testing.T) {
	e := NewEngine(mustPack(t, samplePack))
	for _, ep := range []string{"hooks.slack.com:443", "https://api.telegram.org/bot123/send"} {
		evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
			Type:   "net.egress",
			Detail: map[string]any{"endpoint": ep},
		}, nil, timeNow())
		if len(evs) != 1 || evs[0].Rule.ID != "test.exfil.chat" {
			t.Errorf("endpoint %s: expected exfil.chat, got %+v", ep, evs)
		}
	}
}

func TestEngineScopeNovelty(t *testing.T) {
	e := NewEngine(mustPack(t, samplePack))
	host := schema.Host{ID: "h1"}

	novel := e.Match(host, nil, schema.Action{Type: "scope.change", Detail: map[string]any{"novel": "true"}}, nil, timeNow())
	if len(novel) != 1 || novel[0].Rule.ID != "test.scope.new" {
		t.Errorf("novel scope: expected rule, got %+v", novel)
	}
	known := e.Match(host, nil, schema.Action{Type: "scope.change", Detail: map[string]any{"novel": "false"}}, nil, timeNow())
	if len(known) != 0 {
		t.Errorf("known scope should not fire, got %+v", known)
	}
	missing := e.Match(host, nil, schema.Action{Type: "scope.change"}, nil, timeNow())
	if len(missing) != 0 {
		t.Errorf("absent novel key should not fire, got %+v", missing)
	}
}

func TestEngineArgvMatching(t *testing.T) {
	p := mustPack(t, `
name: p
rules:
  - id: p.exec.key
    severity: high
    match:
      type: process.exec
      argv_any: ["*.ssh/id_*", "*id_rsa*"]
`)
	e := NewEngine(p)
	evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type: "process.exec",
		Detail: map[string]any{
			"exe":  "/bin/cat",
			"argv": []any{"/bin/cat", "/home/vivi/.ssh/id_rsa"},
		},
	}, nil, timeNow())
	if len(evs) != 1 {
		t.Fatalf("expected argv match, got %+v", evs)
	}
	// argv as []string must work too.
	evs = e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"argv": []string{"base64", "/root/id_rsa"}},
	}, nil, timeNow())
	if len(evs) != 1 {
		t.Fatalf("expected []string argv match, got %+v", evs)
	}
}

// TestEngineArgvJoinedPhrase locks in the P1.4 fix: a multi-word glob
// ("*pip install*") must match argv even though auditd hands us the command
// split into elements. Before the fix the joined command line was never a
// matching candidate, so every phrase-shaped rule was silently dead.
func TestEngineArgvJoinedPhrase(t *testing.T) {
	p := mustPack(t, `
name: p
rules:
  - id: p.exec.install
    severity: medium
    match:
      type: process.exec
      argv_any: ["*pip install*", "*npm install -g*"]
`)
	e := NewEngine(p)
	// Split argv, as auditd actually delivers it.
	evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"exe": "/usr/bin/pip", "argv": []any{"pip", "install", "requests"}},
	}, nil, timeNow())
	if len(evs) != 1 {
		t.Fatalf("split-argv phrase did not match; joined candidate missing: %+v", evs)
	}
	// A single-element exec is not falsely matched against a phrase glob.
	evs = e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"exe": "/bin/ls", "argv": []any{"ls"}},
	}, nil, timeNow())
	if len(evs) != 0 {
		t.Fatalf("benign single-arg exec matched a phrase glob: %+v", evs)
	}
}

func TestEngineAgentIDConstraint(t *testing.T) {
	p := mustPack(t, `
name: p
rules:
  - id: p.vivi.only
    severity: low
    match:
      type: process.exec
      agent_id: vivi
      exe: bash
`)
	e := NewEngine(p)
	action := schema.Action{Type: "process.exec", Detail: map[string]any{"exe": "bash"}}

	if evs := e.Match(schema.Host{ID: "h1"}, &schema.Agent{ID: "vivi"}, action, nil, timeNow()); len(evs) != 1 {
		t.Errorf("vivi should match, got %+v", evs)
	}
	if evs := e.Match(schema.Host{ID: "h1"}, &schema.Agent{ID: "other"}, action, nil, timeNow()); len(evs) != 0 {
		t.Errorf("other agent should not match, got %+v", evs)
	}
	if evs := e.Match(schema.Host{ID: "h1"}, nil, action, nil, timeNow()); len(evs) != 0 {
		t.Errorf("nil agent should not match a constrained rule, got %+v", evs)
	}
}

func TestEngineMultipleRulesFireInOrder(t *testing.T) {
	p := mustPack(t, `
name: p
rules:
  - id: p.a
    severity: low
    match:
      type: process.exec
      exe: bash
  - id: p.b
    severity: high
    match:
      type: process.exec
      argv_any: ["*secret*"]
`)
	e := NewEngine(p)
	evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"exe": "bash", "argv": []any{"bash", "-c", "cat secret"}},
	}, nil, timeNow())
	if len(evs) != 2 {
		t.Fatalf("expected 2 events, got %+v", evs)
	}
	if evs[0].Rule.ID != "p.a" || evs[1].Rule.ID != "p.b" {
		t.Errorf("rule order not preserved: %+v", evs)
	}
}

func TestEngineActorAttached(t *testing.T) {
	e := NewEngine(mustPack(t, samplePack))
	uid := 1000
	actor := &schema.Actor{UID: &uid, User: "vivi", PID: intp(42)}
	evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{
		Type:   "process.exec",
		Detail: map[string]any{"exe": "bash"},
	}, actor, timeNow())
	if len(evs) != 1 || evs[0].Actor == nil || evs[0].Actor.User != "vivi" {
		t.Fatalf("actor not attached: %+v", evs)
	}
}

func TestEngineNilPacksSafe(t *testing.T) {
	e := NewEngine(nil, nil)
	if e.Len() != 0 {
		t.Errorf("expected 0 rules, got %d", e.Len())
	}
	if evs := e.Match(schema.Host{ID: "h1"}, nil, schema.Action{Type: "process.exec"}, nil, timeNow()); len(evs) != 0 {
		t.Errorf("empty engine should never fire, got %+v", evs)
	}
}

func TestLoadPacksDir(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.yaml"), []byte(samplePack), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "b.yml"), []byte("name: b\nrules: [{id: b.r, severity: info, match: {type: process.exec, exe: ls}}]"), 0o600); err != nil {
		t.Fatal(err)
	}
	// A non-YAML file is ignored.
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("ignore me"), 0o600); err != nil {
		t.Fatal(err)
	}
	packs, err := LoadPacks(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(packs) != 2 {
		t.Fatalf("expected 2 packs, got %d", len(packs))
	}
	if packs[0].Name != "testpack" || packs[1].Name != "b" {
		t.Errorf("packs not sorted: %s %s", packs[0].Name, packs[1].Name)
	}
}

func TestLoadPacksEmptyDirAndBadPack(t *testing.T) {
	dir := t.TempDir()
	packs, err := LoadPacks(dir)
	if err != nil || len(packs) != 0 {
		t.Errorf("empty dir should be nil,nil; got %v,%v", packs, err)
	}
	if err := os.WriteFile(filepath.Join(dir, "bad.yaml"), []byte("name: bad\nrules: [{id: x, severity: nope, match: {type: process.exec}}]"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPacks(dir); err == nil {
		t.Error("expected error for an invalid pack")
	}
}

func TestLoadPacksEmptyString(t *testing.T) {
	if packs, err := LoadPacks(""); err != nil || packs != nil {
		t.Errorf("empty dir string should be nil,nil; got %v,%v", packs, err)
	}
}

func intp(i int) *int { return &i }

// timeNow is a fixed reference time for Match calls in tests. Temporal rules
// (prior_event) are only exercised in timeline_test.go, where the clock is
// driven explicitly.
func timeNow() time.Time { return time.Unix(1790600000, 0).UTC() }

// --- detail predicate extensions (DetailNonEmptyAny / DetailGlobAny) --------

func TestDetailNonEmptyAnyOrs(t *testing.T) {
	m := Match{Type: "container.start", DetailNonEmptyAny: []string{"privileged", "host_pid", "host_network"}}
	// One of three present -> fires.
	act := schema.Action{Type: "container.start", Detail: map[string]any{"host_pid": "true"}}
	if !matches(m, nil, act) {
		t.Errorf("one-of-many non-empty should match")
	}
	// None present -> no fire.
	bare := schema.Action{Type: "container.start", Detail: map[string]any{"image": "alpine"}}
	if matches(m, nil, bare) {
		t.Errorf("no risk field present must not match")
	}
	// Present but empty string -> treated as absent.
	empty := schema.Action{Type: "container.start", Detail: map[string]any{"privileged": ""}}
	if matches(m, nil, empty) {
		t.Errorf("empty value must not satisfy non-empty")
	}
}

func TestDetailGlobAnyMatchesComposite(t *testing.T) {
	m := Match{Type: "container.start", DetailGlobAny: map[string][]string{
		"binds": {"*:/host*", "*/var/run/docker.sock*"},
	}}
	act := schema.Action{Type: "container.start", Detail: map[string]any{
		"binds": "/data:/data,/var/run/docker.sock:/var/run/docker.sock",
	}}
	if !matches(m, nil, act) {
		t.Errorf("a bind containing the docker socket should match")
	}
	clean := schema.Action{Type: "container.start", Detail: map[string]any{"binds": "/data:/data"}}
	if matches(m, nil, clean) {
		t.Errorf("an ordinary bind must not match")
	}
}

var _ = normalizePathGlobs

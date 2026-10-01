package collector_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/crperdue73/AEGIS-Watchman/internal/central"
	"github.com/crperdue73/AEGIS-Watchman/internal/collector"
	"github.com/crperdue73/AEGIS-Watchman/internal/schema"
)

// TestLayer3EndToEnd wires the whole Layer 3 path with no mocks in the middle:
// an auditd-shaped stream on disk -> FileSource -> rules engine -> BehaviorEngine
// -> real collector.Client -> real central Service over real HTTP. This is the
// live e2e script frozen as a regression test.
//
// The scenario is the brief's canonical chain: an agent reads its own identity
// file (identity drift fed to the timeline) and then reaches out to a chat
// endpoint. The correlation rule must fire on the ACTIONS while central receives
// it, alerts on the right class, and stamps the host/agent.
func TestLayer3EndToEnd(t *testing.T) {
	dir := t.TempDir()
	auditPath := filepath.Join(dir, "audit.log")
	if err := os.WriteFile(auditPath, nil, 0o644); err != nil {
		t.Fatal(err)
	}

	// Real central service, real HTTP server.
	sink := &recordingSink{}
	svc := central.NewService(central.NewMemoryStore())
	svc.SetAlerter(sink)
	srv := httptest.NewServer(svc.Handler())
	defer srv.Close()

	// Real collector client pointed at the test server.
	client := collector.NewClient(srv.URL + "/api/v1/events")

	host := schema.Host{ID: "debian"}
	l3 := schema.Agent{ID: "agent-1", Root: "/srv/agent"}
	cfg := &collector.Config{
		Behavior: collector.Behavior{
			RulesDir: "../../rules",
			AuditLog: auditPath,
			Spool:    filepath.Join(dir, "spool.jsonl"),
		},
	}
	be, err := collector.NewBehaviorEngine(cfg, host, &l3, client)
	if err != nil {
		t.Fatalf("NewBehaviorEngine: %v", err)
	}

	// Feed the file half of the correlation first: an identity.drift on SOUL.md.
	drift := schema.NewEnvelope(schema.EventIdentityDrift, host)
	drift.Agent = &l3
	drift.File = &schema.FileInfo{Path: "/srv/agent/SOUL.md"}
	drift.Severity = schema.SeverityHigh
	be.ObserveDrift([]schema.Event{*drift})

	// Now the action half: the agent used a shell and then connected to Slack.
	// Written as auditd would emit it — an execve frame, then a connect frame
	// (saddr 020001bb037881e2 = AF_INET 3.120.129.226:443, slack.com).
	writeAudit(t, auditPath, ""+
		`type=SYSCALL msg=audit(1790597000.100:900): arch=x86_64 syscall=execve success=yes pid=4242 ppid=1 uid=0 auid=1000 exe="/usr/bin/bash"`+"\n"+
		`type=EXECVE msg=audit(1790597000.100:900): argc=3 a0="bash" a1="-c" a2="cat secrets.txt"`+"\n"+
		`type=SYSCALL msg=audit(1790597000.200:901): arch=x86_64 syscall=connect success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/curl"`+"\n"+
		`type=SOCKADDR msg=audit(1790597000.200:901): saddr=020001bb037881e2`+"\n")

	evs := be.Poll()
	if len(evs) == 0 {
		t.Fatal("expected rule matches from the canonical chain")
	}

	// At least one of each: a shell spawn and the egress-after-identity change
	// correlation. The correlation is the one the whole design exists for.
	var sawShell, sawCorrelation bool
	for _, ev := range evs {
		if ev.Rule == nil {
			t.Fatalf("match with no rule: %+v", ev)
		}
		switch ev.Rule.ID {
		case "carbonato.exec.shell_spawn":
			sawShell = true
		case "carbonato.exfil.egress_after_identity_change":
			sawCorrelation = true
			if ev.Severity != schema.SeverityCritical {
				t.Errorf("correlation severity = %s, want critical", ev.Severity)
			}
		}
	}
	if !sawShell {
		t.Error("shell spawn rule did not fire")
	}
	if !sawCorrelation {
		t.Errorf("the egress-after-identity-change correlation did not fire; got %d matches", len(evs))
	}

	// Central must have received them (shipping is synchronous within Poll's
	// drain, and the loopback hop is effectively instant).
	summaries := sink.summaries()
	if len(summaries) == 0 {
		t.Fatal("central dispatched no alert")
	}
	// The summary names the event class (rule match), not drift.
	found := false
	for _, s := range summaries {
		if containsWord(s, "rule match") {
			found = true
		}
	}
	if !found {
		t.Errorf("no alert summarised as a rule match: %v", summaries)
	}
}

// TestLayer3SurvivesCentralOutage proves the durability guarantee through the
// real HTTP path: matches produced while central is down must reach it once it
// returns, with no duplication.
func TestLayer3SurvivesCentralOutage(t *testing.T) {
	dir := t.TempDir()
	auditPath := filepath.Join(dir, "audit.log")
	if err := os.WriteFile(auditPath, nil, 0o644); err != nil {
		t.Fatal(err)
	}

	// A server that can be toggled between failing and accepting.
	var (
		mu      sync.Mutex
		down    = true
		batches int
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		d := down
		mu.Unlock()
		if d {
			http.Error(w, "central down", http.StatusServiceUnavailable)
			return
		}
		body, _ := io.ReadAll(r.Body)
		var req central.ActionsRequest
		_ = json.Unmarshal(body, &req)
		mu.Lock()
		batches++
		mu.Unlock()
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"accepted":1}`))
	}))
	defer srv.Close()

	client := collector.NewClient(srv.URL + "/api/v1/events")
	host := schema.Host{ID: "debian"}
	l3 := schema.Agent{ID: "agent-1", Root: "/srv/agent"}
	cfg := &collector.Config{Behavior: collector.Behavior{
		RulesDir: "../../rules", AuditLog: auditPath, Spool: filepath.Join(dir, "spool.jsonl"),
	}}
	be, err := collector.NewBehaviorEngine(cfg, host, &l3, client)
	if err != nil {
		t.Fatal(err)
	}

	writeAudit(t, auditPath, ""+
		`type=SYSCALL msg=audit(1790597000.200:901): arch=x86_64 syscall=execve success=yes pid=5 ppid=1 uid=0 auid=1000 exe="/usr/bin/bash"`+"\n"+
		`type=EXECVE msg=audit(1790597000.200:901): argc=3 a0="bash" a1="-c" a2="cat ~/.ssh/id_ed25519"`+"\n")

	if evs := be.Poll(); len(evs) == 0 {
		t.Fatal("expected matches")
	}
	mu.Lock()
	if batches != 0 {
		t.Fatalf("central was down but accepted %d batches", batches)
	}
	mu.Unlock()

	// Central recovers. No new telemetry — the backlog alone must drain.
	mu.Lock()
	down = false
	mu.Unlock()
	if evs := be.Poll(); len(evs) != 0 {
		t.Errorf("expected no new events on an idle cycle, got %d", len(evs))
	}
	mu.Lock()
	got := batches
	mu.Unlock()
	if got != 1 {
		t.Fatalf("recovered central accepted %d batches, want exactly 1 (no dup)", got)
	}
}

func writeAudit(t *testing.T, path, content string) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(content); err != nil {
		t.Fatal(err)
	}
}

func containsWord(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

// recordingSink captures the summaries central dispatched. It satisfies
// central.Alerter (Alert(ctx, Alert) error).
type recordingSink struct {
	mu   sync.Mutex
	sums []string
}

func (r *recordingSink) Alert(_ context.Context, a central.Alert) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.sums = append(r.sums, a.Summary())
	return nil
}

func (r *recordingSink) summaries() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.sums...)
}

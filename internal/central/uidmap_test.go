package central

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/CRPerdue/agentids/internal/schema"
)

func TestNewUidResolverResolves(t *testing.T) {
	r := NewUidResolver(map[int]string{1000: "deploy", 0: "root"})
	if got := r.Resolve(1000); got != "deploy" {
		t.Errorf("Resolve(1000) = %q", got)
	}
	if got := r.Resolve(4242); got != "" {
		t.Errorf("unknown uid should resolve to empty, got %q", got)
	}
	if !r.Enabled() {
		t.Error("resolver with entries should be enabled")
	}
}

// A nil resolver must be safe to call — no nil checks at call sites.
func TestNilUidResolverIsSafe(t *testing.T) {
	var r *UidResolver
	if got := r.Resolve(1000); got != "" {
		t.Errorf("nil resolver Resolve = %q, want empty", got)
	}
	if r.Enabled() {
		t.Error("nil resolver should not be enabled")
	}
	ev := &schema.Event{Actor: &schema.Actor{User: "uid:1000"}}
	uid := 1000
	ev.Actor.UID = &uid
	r.Apply(ev) // must not panic
	if ev.Actor.User != "uid:1000" {
		t.Errorf("nil resolver changed the actor: %q", ev.Actor.User)
	}
}

func TestApplyNamesActorKeepsUID(t *testing.T) {
	r := NewUidResolver(map[int]string{1000: "deploy"})
	uid := 1000
	ev := &schema.Event{Actor: &schema.Actor{User: "uid:1000", UID: &uid}}
	r.Apply(ev)
	if ev.Actor.User != "deploy" {
		t.Errorf("User = %q, want deploy", ev.Actor.User)
	}
	// The numeric id is the ground truth and must survive.
	if ev.Actor.UID == nil || *ev.Actor.UID != 1000 {
		t.Errorf("UID lost: %+v", ev.Actor.UID)
	}
}

func TestApplyUnknownUidLeavesNumeric(t *testing.T) {
	r := NewUidResolver(map[int]string{1000: "deploy"})
	uid := 4242
	ev := &schema.Event{Actor: &schema.Actor{User: "uid:4242", UID: &uid}}
	r.Apply(ev)
	if ev.Actor.User != "uid:4242" {
		t.Errorf("unknown uid should stay numeric, got %q", ev.Actor.User)
	}
}

func TestApplyNilActorIsSafe(t *testing.T) {
	r := NewUidResolver(map[int]string{1000: "deploy"})
	ev := &schema.Event{}
	r.Apply(ev) // must not panic
}

func TestLoadUidMapFromPasswd(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "passwd")
	content := "# comment\n" +
		"root:x:0:0:root:/root:/bin/bash\n" +
		"deploy:x:1000:1000::/home/deploy:/bin/bash\n" +
		"malformed-line\n" +
		"\n" +
		"broken:x:notanumber:1000:::/bin/false\n"
	if err := os.WriteFile(p, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	m, err := LoadUidMapFromPasswd(p)
	if err != nil {
		t.Fatal(err)
	}
	if m[0] != "root" || m[1000] != "deploy" {
		t.Errorf("map = %#v", m)
	}
	if len(m) != 2 {
		t.Errorf("malformed/comment lines should be skipped, got %#v", m)
	}
}

func TestLoadUidMapMissingFileIsError(t *testing.T) {
	if _, err := LoadUidMapFromPasswd(filepath.Join(t.TempDir(), "nope")); err == nil {
		t.Error("missing passwd file should error")
	}
}

// The whole point: a rule match with a numeric actor reaches the alerter with a
// name on it, and the numeric id intact.
func TestIngestActionsResolvesActorName(t *testing.T) {
	svc := NewService(NewMemoryStore())
	cap := &captureAlerter{}
	svc.SetAlerter(cap)
	svc.SetUidResolver(NewUidResolver(map[int]string{1000: "deploy"}))

	ev := ruleMatchEvent("h-1", "carbonato.exec.shell_spawn")
	uid := 1000
	ev.Actor = &schema.Actor{UID: &uid, User: "uid:1000"}

	if _, err := svc.IngestActions(context.Background(), host, agent,
		[]schema.Event{ev}); err != nil {
		t.Fatal(err)
	}
	if len(cap.alerts) != 1 {
		t.Fatalf("alerts = %d", len(cap.alerts))
	}
	got := cap.alerts[0].Events[0].Actor
	if got == nil || got.User != "deploy" {
		t.Fatalf("actor = %+v, want user=deploy", got)
	}
	if got.UID == nil || *got.UID != 1000 {
		t.Errorf("uid lost: %+v", got.UID)
	}
}

// With no resolver installed, the numeric display string is untouched.
func TestIngestActionsWithoutResolverLeavesNumeric(t *testing.T) {
	svc := NewService(NewMemoryStore())
	cap := &captureAlerter{}
	svc.SetAlerter(cap)

	ev := ruleMatchEvent("h-1", "carbonato.exec.shell_spawn")
	uid := 1000
	ev.Actor = &schema.Actor{UID: &uid, User: "uid:1000"}

	if _, err := svc.IngestActions(context.Background(), host, agent,
		[]schema.Event{ev}); err != nil {
		t.Fatal(err)
	}
	if got := cap.alerts[0].Events[0].Actor.User; got != "uid:1000" {
		t.Errorf("actor user = %q, want uid:1000", got)
	}
}

package telemetry

import (
	"os"
	"strings"
	"testing"
)

// saddr hex for AF_INET 3.120.129.226:443 -> family 0002, port 01bb, addr 0378 81e2
// 3.120.129.226 = 0x037881e2
const (
	slackSaddr  = "020001bb037881e2" // 3.120.129.226:443
	tgSaddr     = "020001bb959aa700" // 149.154.167.0:443
	unusedSaddr = "020000505f000001" // 95.0.0.1:80
)

func TestDecodeSockAddrIPv4(t *testing.T) {
	ep, ok := decodeSockAddr(slackSaddr)
	if !ok || ep != "3.120.129.226:443" {
		t.Fatalf("decode = %q,%v", ep, ok)
	}
}

func TestDecodeSockAddrBad(t *testing.T) {
	for _, s := range []string{"", "zz", "0200", "020001bb"} {
		if _, ok := decodeSockAddr(s); ok {
			t.Errorf("saddr %q should not decode", s)
		}
	}
}

func TestServiceForAddr(t *testing.T) {
	if got := serviceForAddr("3.120.129.226:443"); got != "slack.com" {
		t.Errorf("slack = %q", got)
	}
	if got := serviceForAddr("149.154.167.5:443"); got != "api.telegram.org" {
		t.Errorf("telegram = %q", got)
	}
	if got := serviceForAddr("203.0.113.9:443"); got != "" {
		t.Errorf("unknown = %q, want empty", got)
	}
}

const connectChain = `type=SYSCALL msg=audit(1790597001.100:950): arch=x86_64 syscall=connect success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/curl"
type=SOCKADDR msg=audit(1790597001.100:950): saddr=020001bb037881e2
type=SYSCALL msg=audit(1790597001.200:951): arch=x86_64 syscall=connect success=no pid=4243 ppid=1 uid=1000 auid=1000 exe="/usr/bin/curl"
type=SOCKADDR msg=audit(1790597001.200:951): saddr=020000505f000001
`

func TestParseAuditConnectLog(t *testing.T) {
	recs, err := ParseAuditConnectLog(strings.NewReader(connectChain))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 2 {
		t.Fatalf("expected 2 connect records, got %d", len(recs))
	}
	act, actor, ok := recs[0].EgressAction()
	if !ok {
		t.Fatal("first connect should convert")
	}
	if act.Type != NetEgress {
		t.Errorf("action type = %q", act.Type)
	}
	ep, _ := act.Detail["endpoint"].(string)
	if !strings.Contains(ep, "3.120.129.226:443") || !strings.Contains(ep, "slack.com") {
		t.Errorf("endpoint = %q, want IP and service name", ep)
	}
	if act.Detail["service"] != "slack.com" {
		t.Errorf("service = %v", act.Detail["service"])
	}
	if actor == nil || actor.PID == nil || *actor.PID != 4242 {
		t.Errorf("actor = %+v", actor)
	}

	// Unknown address keeps the raw IP and no service name.
	act2, _, ok := recs[1].EgressAction()
	if !ok {
		t.Fatal("second connect should convert")
	}
	if _, named := act2.Detail["service"]; named {
		t.Errorf("unknown addr should not be named: %v", act2.Detail)
	}
	if got, _ := act2.Detail["endpoint"].(string); got != "95.0.0.1:80" {
		t.Errorf("endpoint = %q, want raw IP:port", got)
	}
}

func TestParseAuditConnectSkipsNonConnect(t *testing.T) {
	// An execve record must be ignored by the connect parser.
	exec := `type=SYSCALL msg=audit(1790597002.100:960): arch=x86_64 syscall=execve success=yes pid=1 uid=0 exe="/bin/bash"
type=EXECVE msg=audit(1790597002.100:960): argc=1 a0="bash"
`
	recs, err := ParseAuditConnectLog(strings.NewReader(exec))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 0 {
		t.Fatalf("execve should not yield connect records, got %d", len(recs))
	}
}

func TestParseAuditStreamMixesShapes(t *testing.T) {
	mixed := `type=SYSCALL msg=audit(1790597003.100:970): arch=x86_64 syscall=execve success=yes pid=1 uid=0 exe="/bin/bash"
type=EXECVE msg=audit(1790597003.100:970): argc=1 a0="bash"
type=SYSCALL msg=audit(1790597003.200:971): arch=x86_64 syscall=connect success=yes pid=2 uid=1000 exe="/usr/bin/curl"
type=SOCKADDR msg=audit(1790597003.200:971): saddr=020001bb037881e2
`
	recs, err := ParseAuditStream(strings.NewReader(mixed))
	if err != nil {
		t.Fatal(err)
	}
	if len(recs) != 2 {
		t.Fatalf("expected 2 actionable records, got %d", len(recs))
	}
	a0, _, ok0 := recs[0].AnyAction()
	a1, _, ok1 := recs[1].AnyAction()
	if !ok0 || a0.Type != ProcessExec {
		t.Errorf("first should be process.exec, got %+v", a0)
	}
	if !ok1 || a1.Type != NetEgress {
		t.Errorf("second should be net.egress, got %+v", a1)
	}
}

// TestFileSourceCursorResumesAcrossRestart proves a restarted tail does not
// replay the log — without this, every restart re-fires the rules its retained
// lines still match.
func TestFileSourceCursorResumesAcrossRestart(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/audit.log"
	cursor := dir + "/audit.cursor"
	if err := os.WriteFile(path, []byte(connectChain), 0o644); err != nil {
		t.Fatal(err)
	}

	// First tail reads both frames and records its offset.
	s1 := NewFileSource(path)
	s1.SetCursor(cursor)
	evs, err := s1.Actions()
	if err != nil {
		t.Fatal(err)
	}
	if len(evs) != 2 {
		t.Fatalf("first pass = %d events, want 2", len(evs))
	}

	// Second tail over the same file, same cursor: nothing new, no replay.
	s2 := NewFileSource(path)
	s2.SetCursor(cursor)
	evs, err = s2.Actions()
	if err != nil {
		t.Fatal(err)
	}
	if len(evs) != 0 {
		t.Fatalf("restart replayed %d events, want 0", len(evs))
	}

	// Appending more is then seen exactly once.
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(connectChain); err != nil {
		t.Fatal(err)
	}
	f.Close()
	s3 := NewFileSource(path)
	s3.SetCursor(cursor)
	evs, err = s3.Actions()
	if err != nil {
		t.Fatal(err)
	}
	if len(evs) != 2 {
		t.Fatalf("appended frames = %d, want 2", len(evs))
	}
}

func TestFileSourceNoCursorReplays(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/audit.log"
	if err := os.WriteFile(path, []byte(connectChain), 0o644); err != nil {
		t.Fatal(err)
	}
	// Without a cursor, a fresh source replays (the documented old behavior).
	s := NewFileSource(path)
	if evs, _ := s.Actions(); len(evs) != 2 {
		t.Fatalf("first pass = %d, want 2", len(evs))
	}
	s2 := NewFileSource(path)
	if evs, _ := s2.Actions(); len(evs) != 2 {
		t.Fatalf("no-cursor restart = %d, want 2 (replay)", len(evs))
	}
}

// TestFileSourceEmitsEgress proves the single-tail source now carries connect
// records, which is what makes the correlation rule reachable on a live host.
func TestFileSourceEmitsEgress(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/audit.log"
	if err := os.WriteFile(path, []byte(connectChain), 0o644); err != nil {
		t.Fatal(err)
	}
	src := NewFileSource(path)
	evs, err := src.Actions()
	if err != nil {
		t.Fatal(err)
	}
	var egress int
	for _, e := range evs {
		if e.Action.Type == NetEgress {
			egress++
		}
	}
	if egress != 2 {
		t.Fatalf("expected 2 net.egress events from the file source, got %d (%+v)", egress, evs)
	}
}

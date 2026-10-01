package telemetry

import (
	"bytes"
	"testing"
)

// Fuzz targets for the untrusted-input parsers.
//
// Every stream this package reads is attacker-influenced: auditd lines carry
// argv an attacker chose, `docker events` is JSON off a socket, and the text
// stream is a pipeline. A parser that panics on crafted input is a denial of
// service against the *detector* — the cheapest way to turn detection off is to
// crash the collector, and the operator's first sign of trouble would be a
// restart, not an alert. These fuzzers assert the only contract a parser should
// have on hostile input: it returns, with a value or an error, and never panics
// or hangs.
//
// Run a fuzzer for a bounded time with:
//
//	go test ./internal/telemetry -run=^$ -fuzz=FuzzParseAuditStream -fuzztime=30s
//
// The seed corpus below is exercised by an ordinary `go test`, so CI covers the
// known-shape inputs even without a fuzzing run.

func FuzzParseAuditStream(f *testing.F) {
	f.Add([]byte(`type=SYSCALL msg=audit(1790597002.100:960): arch=x86_64 syscall=execve success=yes pid=1 uid=0 exe="/bin/cat"` + "\n" + `type=EXECVE msg=audit(1790597002.100:960): argc=2 a0="cat" a1="/etc/shadow"`))
	f.Add([]byte(`type=SYSCALL msg=audit(1790597001.100:950): arch=x86_64 syscall=connect success=yes pid=4242 ppid=1 uid=1000 auid=1000 exe="/usr/bin/curl"` + "\n" + `type=SOCKADDR msg=audit(1790597001.100:950): saddr=020001bb037881e2`))
	f.Add([]byte("type=CWD msg=audit(1.1:1): cwd=\"/home/agent\"\n\ntype=PATH msg=audit(1.1:1): item=0 name=\"/x\"\n"))
	f.Add([]byte("not an audit line\nmsg=audit(\nserial=abc\n"))
	f.Fuzz(func(t *testing.T, data []byte) {
		_, _ = ParseAuditStream(bytes.NewReader(data))
	})
}

func FuzzParseAuditLog(f *testing.F) {
	f.Add([]byte(`type=SYSCALL msg=audit(1790597002.100:960): syscall=execve exe="/bin/sh"`))
	f.Add([]byte(`type=EXECVE msg=audit(1790597002.100:960): argc=1 a0="id"`))
	f.Add([]byte("garbage\x00\xff\n"))
	f.Fuzz(func(t *testing.T, data []byte) {
		_, _ = ParseAuditLog(bytes.NewReader(data))
	})
}

func FuzzParseAuditConnectLog(f *testing.F) {
	f.Add([]byte(`type=SYSCALL msg=audit(1790597001.100:950): syscall=connect pid=4242 exe="/usr/bin/curl"` + "\n" + `type=SOCKADDR msg=audit(1790597001.100:950): saddr=020001bb037881e2`))
	f.Add([]byte(`type=SOCKADDR msg=audit(1.1:1): saddr=`))
	f.Fuzz(func(t *testing.T, data []byte) {
		_, _ = ParseAuditConnectLog(bytes.NewReader(data))
	})
}

func FuzzParseDockerEvents(f *testing.F) {
	f.Add([]byte(`{"Type":"container","Action":"start","id":"abc","time":1,"Actor":{"ID":"abc","Attributes":{"image":"alpine"}}}`))
	f.Add([]byte(`{"Type":"container","Action":"start","Actor":{"ID":"x","Attributes":{"name":"/p","image":"i"}}}` + "\n" + `{"Type":"network","Action":"connect"}`))
	f.Add([]byte("{not json\n# comment\n\n"))
	f.Fuzz(func(t *testing.T, data []byte) {
		_, _ = ParseDockerEvents(bytes.NewReader(data))
	})
}

func FuzzParseTextStream(f *testing.F) {
	f.Add([]byte("/bin/cat\t/etc/shadow\n"))
	f.Add([]byte("cat /etc/shadow\nls\n# comment\n\n"))
	f.Add([]byte("\t\t\n/bin/ls\n"))
	f.Fuzz(func(t *testing.T, data []byte) {
		_, _ = ParseTextStream(bytes.NewReader(data))
	})
}

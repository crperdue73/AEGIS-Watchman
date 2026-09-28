// Package telemetry turns real host activity into schema.Action values so the
// Layer 3 rules engine has something to match on.
//
// Layers 1 and 2 watch files; Layer 3 watches actions. A file signature can be
// evaded by editing a markdown persona file (no malicious byte), but the
// attacker still has to *do* something to reach the objective — spawn a shell,
// read ~/.ssh, call out to a chat service. This package is where those actions
// come from.
//
// Scope of this first adapter: Linux process execution. Two input shapes are
// supported, both cheap and dependency-free:
//
//   - Auditd execve records (type=SYSCALL ... type=EXECVE ...), read from
//     /var/log/audit/audit.log or a tail of it. This is the primary source:
//     auditd is present on most servers and its execve records carry pid, uid,
//     exe, and argv.
//   - A simple line-oriented text stream ("exe<TAB>arg1<TAB>arg2..."), used for
//     tests, demos, and anything that can pipe commands somewhere.
//
// The adapter emits process.exec actions only. net.egress / file.access /
// scope.change come from later adapters (eBPF, FIM, agent control plane); the
// Action shape and the rule predicate set already support them.
package telemetry

import (
	"fmt"
	"io"
	"strconv"
	"strings"

	"github.com/CRPerdue/agentids/internal/schema"
)

// ProcessExec is the action type emitted by the execve adapters.
const ProcessExec = "process.exec"

// NetEgress, FileAccess, and ScopeChange are the action types later adapters
// will emit. Declared here so rule authors have one place to look.
const (
	NetEgress   = "net.egress"
	FileAccess  = "file.access"
	ScopeChange = "scope.change"
)

// AuditRecord is one parsed auditd event (a group of same-serial messages).
//
// Not every field is present in every record: auditd splits one logical event
// across several lines sharing a serial number, and it truncates argv unless
// configured otherwise.
type AuditRecord struct {
	Serial int
	Type   string

	// From type=SYSCALL.
	Arch    string
	Syscall string
	Success bool
	PID     int
	PPID    int
	UID     int
	AUid    int
	Exe     string

	// From type=EXECVE (argv), reconstructed in order.
	Argv []string

	// From type=CWD / type=PATH (not used for v1 matching, kept for later).
	Cwd   string
	Paths []string
}

// Action converts an execve-ish audit record into a scheme.Action. It returns
// false if the record is not a usable process-execution event.
func (r *AuditRecord) Action() (schema.Action, *schema.Actor, bool) {
	if r.Exe == "" && len(r.Argv) == 0 {
		return schema.Action{}, nil, false
	}
	exe := r.Exe
	if exe == "" {
		exe = r.Argv[0]
	}
	detail := map[string]any{
		"exe": exe,
	}
	if len(r.Argv) > 0 {
		argv := make([]any, 0, len(r.Argv))
		for _, a := range r.Argv {
			argv = append(argv, a)
		}
		detail["argv"] = argv
	}
	if r.Syscall != "" {
		detail["syscall"] = r.Syscall
	}
	if r.Cwd != "" {
		detail["cwd"] = r.Cwd
	}
	if r.PPID != 0 {
		detail["ppid"] = r.PPID
	}

	act := schema.Action{Type: ProcessExec, Detail: detail}
	actor := &schema.Actor{User: r.user()}
	if r.PID != 0 {
		pid := r.PID
		actor.PID = &pid
	}
	if r.UID != 0 || r.AUid != 0 {
		uid := r.UID
		actor.UID = &uid
	}
	if actor.UID == nil && actor.PID == nil && actor.User == "" {
		actor = nil
	}
	return act, actor, true
}

// user returns a best-effort username. auditd's SYSCALL line has numeric ids;
// mapping to names would need /etc/passwd, which we avoid here on purpose
// (a lookup at ingest time is a dependency and a failure mode). Central can
// resolve ids later. Falls back to "uid:<n>" so alerts are still readable.
func (r *AuditRecord) user() string {
	if r.UID != 0 {
		return "uid:" + strconv.Itoa(r.UID)
	}
	return ""
}

// ParseAuditLog parses a stream of auditd log lines into records and returns
// only those that represent a usable process execution (syscall execve/execveat
// or a record carrying argv/exe). Lines that don't belong to a process event
// are skipped, not errors — an audit log is a mixed stream by nature.
func ParseAuditLog(r io.Reader) ([]*AuditRecord, error) {
	data, err := io.ReadAll(r)
	if err != nil {
		return nil, fmt.Errorf("telemetry: read audit log: %w", err)
	}

	// auditd serialises one logical event across several lines keyed by the
	// "msg=audit(TS:SERIAL)" trailer. Group by serial, preserving first-seen
	// order so output is deterministic.
	order := []int{}
	bySerial := map[int]*AuditRecord{}

	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		typ := auditLineType(line)
		if typ == "" {
			continue
		}
		serial := auditSerial(line)
		if serial == 0 {
			continue
		}
		rec, ok := bySerial[serial]
		if !ok {
			rec = &AuditRecord{Serial: serial}
			bySerial[serial] = rec
			order = append(order, serial)
		}
		applyAuditLine(rec, typ, line)
	}

	var out []*AuditRecord
	for _, serial := range order {
		rec := bySerial[serial]
		if rec.Syscall != "" && rec.Syscall != "execve" && rec.Syscall != "execveat" {
			continue
		}
		if rec.Exe == "" && len(rec.Argv) == 0 {
			continue
		}
		out = append(out, rec)
	}
	return out, nil
}

// auditLineType extracts the "type=XXX" token from an auditd line.
func auditLineType(line string) string {
	const key = "type="
	i := strings.Index(line, key)
	if i < 0 {
		return ""
	}
	rest := line[i+len(key):]
	end := strings.IndexAny(rest, " \t")
	if end < 0 {
		return rest
	}
	return rest[:end]
}

// auditSerial extracts the serial number from "msg=audit(1234567890.123:456)".
// The trailer is "<epoch>.<millis>:<serial>", closed by ')'. We locate that
// closing paren first, then take the digits between the ':' and it — using
// LastIndex on the colon would wrongly jump to a colon later in the body.
func auditSerial(line string) int {
	i := strings.Index(line, "audit(")
	if i < 0 {
		return 0
	}
	rest := line[i+len("audit("):]
	close := strings.IndexByte(rest, ')')
	if close < 0 {
		return 0
	}
	stamp := rest[:close] // "1790597000.123:100"
	colon := strings.LastIndex(stamp, ":")
	if colon < 0 {
		return 0
	}
	n, err := strconv.Atoi(strings.TrimSpace(stamp[colon+1:]))
	if err != nil {
		return 0
	}
	return n
}

// applyAuditLine folds one auditd line into a record.
func applyAuditLine(rec *AuditRecord, typ, line string) {
	switch typ {
	case "SYSCALL":
		rec.Type = "SYSCALL"
		rec.Arch = auditField(line, "arch")
		rec.Syscall = auditField(line, "syscall")
		rec.Exe = auditField(line, "exe")
		rec.Cwd = auditField(line, "cwd")
		rec.Success = auditField(line, "success") == "yes"
		rec.PID = auditInt(line, "pid")
		rec.PPID = auditInt(line, "ppid")
		rec.UID = auditInt(line, "uid")
		rec.AUid = auditInt(line, "auid")
	case "EXECVE":
		rec.Type = "EXECVE"
		rec.Argv = append(rec.Argv, parseExecveArgs(line)...)
	case "CWD":
		rec.Type = "CWD"
		if v := auditField(line, "cwd"); v != "" {
			rec.Cwd = v
		}
	case "PATH":
		if v := auditField(line, "name"); v != "" {
			rec.Paths = append(rec.Paths, unquote(v))
		}
	}
}

// auditField extracts a key=value token, returning the unquoted value. Values
// may be double-quoted and contain spaces, so we cannot use strings.Fields.
func auditField(line, key string) string {
	prefix := key + "="
	for _, f := range splitAuditFields(line) {
		if strings.HasPrefix(f, prefix) {
			return unquote(strings.TrimPrefix(f, prefix))
		}
	}
	return ""
}

// splitAuditFields splits an auditd line into key=value tokens, treating a
// double-quoted value (which may contain spaces) as a single token.
func splitAuditFields(line string) []string {
	var out []string
	var cur strings.Builder
	inQuote := false
	for i := 0; i < len(line); i++ {
		c := line[i]
		switch {
		case c == '"':
			inQuote = !inQuote
			cur.WriteByte(c)
		case (c == ' ' || c == '\t') && !inQuote:
			if cur.Len() > 0 {
				out = append(out, cur.String())
				cur.Reset()
			}
		default:
			cur.WriteByte(c)
		}
	}
	if cur.Len() > 0 {
		out = append(out, cur.String())
	}
	return out
}

// auditInt extracts a key=int token, returning 0 if absent or unparsable.
func auditInt(line, key string) int {
	v := auditField(line, key)
	if v == "" {
		return 0
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0
	}
	return n
}

// parseExecveArgs reconstructs argv from an EXECVE line. auditd encodes args as
// a0=..., a1=..., a2_len=N a2[0]=... for long args. Short form is the common
// case; the long form is reassembled for completeness.
func parseExecveArgs(line string) []string {
	fields := splitAuditFields(line)
	var args []string
	// Index the short form aN=value and the long form aN_len / aN[i].
	short := map[int]string{}
	long := map[int]map[int]string{}
	var maxIdx int

	for _, f := range fields {
		if !strings.HasPrefix(f, "a") {
			continue
		}
		eq := strings.Index(f, "=")
		if eq < 0 {
			continue
		}
		key := f[:eq]
		val := unquote(f[eq+1:])

		if br := strings.IndexByte(key, '['); br >= 0 { // a2[0]
			idx, err := strconv.Atoi(key[1:br])
			if err != nil {
				continue
			}
			part, err := strconv.Atoi(strings.TrimSuffix(key[br+1:], "]"))
			if err != nil {
				continue
			}
			if long[idx] == nil {
				long[idx] = map[int]string{}
			}
			long[idx][part] = val
			if idx > maxIdx {
				maxIdx = idx
			}
			continue
		}

		n, err := strconv.Atoi(strings.TrimPrefix(key, "a"))
		if err != nil {
			continue
		}
		if strings.HasSuffix(key, "_len") {
			continue // length hint, not a value
		}
		short[n] = val
		if n > maxIdx {
			maxIdx = n
		}
	}

	for i := 0; i <= maxIdx; i++ {
		if v, ok := short[i]; ok {
			args = append(args, v)
			continue
		}
		if parts, ok := long[i]; ok {
			var b strings.Builder
			for j := 0; ; j++ {
				p, ok := parts[j]
				if !ok {
					break
				}
				b.WriteString(p)
			}
			args = append(args, b.String())
		}
	}
	return args
}

// unquote strips a single layer of surrounding double quotes and unescapes the
// common auditd hex-encoded values (\x22 style is rare; auditd usually emits
// raw), which is enough for argv reconstruction.
func unquote(s string) string {
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		s = s[1 : len(s)-1]
	}
	if strings.Contains(s, "\\x") {
		s = decodeHexEscapes(s)
	}
	return s
}

// decodeHexEscapes turns \xHH sequences into bytes, leaving other characters
// untouched. auditd hex-encodes non-printable argv, which would otherwise make
// a rule miss a path.
func decodeHexEscapes(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+3 < len(s) && s[i+1] == 'x' {
			if n, err := strconv.ParseUint(s[i+2:i+4], 16, 8); err == nil {
				b.WriteByte(byte(n))
				i += 3
				continue
			}
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

package telemetry

import (
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"

	"github.com/CRPerdue/agentids/internal/schema"
)

// EgressConnect is the syscall this adapter understands.
const EgressConnect = "connect"

// ListenBind and ListenListen are the listener syscalls. A process that binds
// a socket and then listens is opening a foothold — a reverse-shell handler, a
// beacon, or a service that has no business running under an agent. On Linux,
// `bind` carries the local address in SOCKADDR; `listen` on a socket already
// bound in a previous call carries none, so `bind` is the record that matters.
const (
	ListenBind   = "bind"
	ListenListen = "listen"
)

// ParseAuditConnectLog parses a stream of auditd lines and returns the records
// that represent an outbound connection (syscall=connect with a SOCKADDR).
//
// This is the second telemetry shape Layer 3 needs. Execve tells you a shell
// spawned; connect tells you where the process then reached — and the brief's
// highest-value rule ("egress within N minutes of an identity change") cannot
// be decided without it.
//
// auditd emits a connect as a SYSCALL line (syscall=connect, with exe/pid/uid)
// plus a SOCKADDR line carrying the peer address hex-encoded in saddr=.
// Enable it with:
//
//	-a always,exit -F arch=b64 -S connect -k agentids
func ParseAuditConnectLog(r io.Reader) ([]*AuditRecord, error) {
	data, err := io.ReadAll(r)
	if err != nil {
		return nil, fmt.Errorf("telemetry: read audit log: %w", err)
	}

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
		applyConnectLine(rec, typ, line)
	}

	var out []*AuditRecord
	for _, serial := range order {
		rec := bySerial[serial]
		switch rec.Syscall {
		case EgressConnect:
			if rec.SockAddr == "" {
				continue // no address -> nothing to match an endpoint rule against
			}
		case ListenBind, ListenListen:
			if rec.BindAddr == "" {
				continue // a listen on an already-bound socket carries no address
			}
		default:
			continue
		}
		out = append(out, rec)
	}
	return out, nil
}

// applyConnectLine folds one auditd line into a record for connect parsing.
// It shares the auditField/splitAuditFields helpers with the execve parser but
// keeps its own field handling for SOCKADDR.
// applyConnectLine folds a connect-or-bind record into an AuditRecord. Both
// syscalls are emitted by auditd as a SYSCALL line plus a SOCKADDR line, so
// this one function serves both adapters. The SOCKADDR is filed under the
// field matching the syscall: a connect's address is a PEER (SockAddr), a
// bind's address is LOCAL (BindAddr). Keeping them apart stops a listener from
// being reported as "connected to 0.0.0.0:4444".
func applyConnectLine(rec *AuditRecord, typ, line string) {
	switch typ {
	case "SYSCALL":
		rec.Type = "SYSCALL"
		rec.Arch = auditField(line, "arch")
		rec.Syscall = auditField(line, "syscall")
		rec.Exe = auditField(line, "exe")
		rec.Success = auditField(line, "success") == "yes"
		rec.PID = auditInt(line, "pid")
		rec.PPID = auditInt(line, "ppid")
		rec.UID = auditInt(line, "uid")
		rec.AUid = auditInt(line, "auid")
	case "SOCKADDR":
		v := auditField(line, "saddr")
		if v == "" {
			return
		}
		if rec.Syscall == ListenBind || rec.Syscall == ListenListen {
			rec.BindAddr = v
		} else {
			rec.SockAddr = v
		}
	}
}

// ListenAction converts a bind/listen record into a net.listen action. It
// returns false if the record has no usable local address. The address is
// decoded into "host:port" (a wildcard bind shows as 0.0.0.0 or ::), so a rule
// can match on "anything that listens" or on a specific port.
func (r *AuditRecord) ListenAction() (schema.Action, *schema.Actor, bool) {
	addr, ok := decodeSockAddr(r.BindAddr)
	if !ok || addr == "" {
		return schema.Action{}, nil, false
	}
	detail := map[string]any{
		"listen":  addr,
		"syscall": r.Syscall,
	}
	if r.Exe != "" {
		detail["exe"] = r.Exe
	}
	if r.PID != 0 {
		detail["pid"] = r.PID
	}
	return schema.Action{Type: NetListen, Detail: detail}, actorOf(r), true
}

// EgressAction converts a connect record into a net.egress action. It returns
// false if the record has no usable peer address. The SOCKADDR is decoded into
// "host:port"; when the peer is a known chat/C2 service the endpoint also
// carries that service name so a domain-glob rule can match.
func (r *AuditRecord) EgressAction() (schema.Action, *schema.Actor, bool) {
	ep, ok := decodeSockAddr(r.SockAddr)
	if !ok || ep == "" {
		return schema.Action{}, nil, false
	}
	detail := map[string]any{"endpoint": ep}
	// If we can name the service, add it as a second endpoint candidate so
	// rules authored against "*slack.com*" fire without DNS (the audit record
	// only carries an IP). Named candidates are appended, never replaced.
	if svc := serviceForAddr(ep); svc != "" {
		detail["endpoint"] = svc + " " + ep
		detail["service"] = svc
	}
	if r.Exe != "" {
		detail["exe"] = r.Exe
	}
	if r.PID != 0 {
		detail["pid"] = r.PID
	}
	return schema.Action{Type: NetEgress, Detail: detail}, actorOf(r), true
}

// actorOf builds the actor shared by both adapters.
func actorOf(r *AuditRecord) *schema.Actor {
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
		return nil
	}
	return actor
}

// decodeSockAddr turns an auditd saddr hex string into "host:port".
//
// The bytes are a raw sockaddr. IPv4 (AF_INET=2) is
// <u16 family><u16 port_be><u32 addr_be>; IPv6 (AF_INET=10 on Linux) is
// <u16 family><u16 port_be><u32 flowinfo><16 bytes addr><u32 scope>. We are
// forgiving: a malformed saddr returns ok=false rather than a bogus endpoint,
// so a rule never fires on garbage.
func decodeSockAddr(hexStr string) (string, bool) {
	hexStr = strings.TrimSpace(strings.Trim(hexStr, "\""))
	if hexStr == "" {
		return "", false
	}
	b, err := hex.DecodeString(hexStr)
	if err != nil || len(b) < 8 {
		return "", false
	}
	family := uint16(b[0]) | uint16(b[1])<<8
	port := int(uint16(b[2])<<8 | uint16(b[3])) // network byte order

	switch family {
	case 2: // AF_INET
		if len(b) < 8 {
			return "", false
		}
		ip := net.IPv4(b[4], b[5], b[6], b[7])
		return net.JoinHostPort(ip.String(), strconv.Itoa(port)), true
	case 10: // AF_INET6
		if len(b) < 24 {
			return "", false
		}
		ip := net.IP(b[8:24])
		return net.JoinHostPort(ip.String(), strconv.Itoa(port)), true
	default:
		return "", false
	}
}

// serviceForAddr reverse-maps a small, fixed set of well-known endpoint ranges
// to a service name. This exists because auditd records an IP, not a hostname,
// and the highest-value rules are written against chat/C2 domains. It is a
// convenience, not a resolver: unknown addresses return "" and the rule engine
// still sees the raw IP. Add ranges deliberately — a wrong name is worse than
// no name.
func serviceForAddr(endpoint string) string {
	host, _, err := net.SplitHostPort(endpoint)
	if err != nil {
		return ""
	}
	for _, e := range knownServices {
		if e.match(host) {
			return e.name
		}
	}
	return ""
}

type knownService struct {
	name string
	// cidrs are the ranges whose membership implies the service. This is a
	// minimum viable set; a production deployment would load from a feed.
	cidrs []*net.IPNet
}

func (k knownService) match(host string) bool {
	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}
	for _, n := range k.cidrs {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

func mustCIDR(s string) *net.IPNet {
	_, n, err := net.ParseCIDR(s)
	if err != nil {
		panic("telemetry: bad CIDR constant: " + s)
	}
	return n
}

// knownServices maps published service addresses to names. Kept short and
// explicit on purpose. Slack and Telegram publish their egress ranges; these
// are representative entries, not exhaustive.
var knownServices = []knownService{
	{name: "slack.com", cidrs: []*net.IPNet{mustCIDR("3.120.129.226/32")}},
	{name: "api.telegram.org", cidrs: []*net.IPNet{mustCIDR("149.154.167.0/24")}},
}

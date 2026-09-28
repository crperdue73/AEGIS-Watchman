package central

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/smtp"
	"strings"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

// EmailAlerter delivers an Alert as an email via SMTP. It is the second
// Phase-1 sink the brief calls for: a webhook fits a ticket/Slack flow, email
// fits an on-call inbox, and both consume the same Alert.
//
// The message is a plain-text body (the events as a readable report) plus a
// machine-friendly JSON attachment, so a human reads it and a mailbox rule can
// still parse it.
type EmailAlerter struct {
	// Host is "host:port". Port 465 implies implicit TLS; otherwise STARTTLS is
	// used when the server advertises it.
	Host string
	// Username/Password authenticate. Empty Username skips AUTH (e.g. a local
	// relay that trusts the source, or a testing sink).
	Username string
	Password string
	// From and To are envelope + header addresses. To may be a list.
	From string
	To   []string
	// SubjectPrefix is prepended to the generated subject. Defaults to
	// "[agentids]".
	SubjectPrefix string
	// AllowPlaintext permits sending without STARTTLS on a non-465 port. Off by
	// default: persona content is sensitive and should not cross a network in
	// the clear.
	AllowPlaintext bool
	// Timeout bounds the whole SMTP conversation. Defaults to 15s.
	Timeout time.Duration
}

// NewEmailAlerter returns an email alerter with TLS-on-by-default behavior.
func NewEmailAlerter(host, from string, to []string) *EmailAlerter {
	return &EmailAlerter{Host: host, From: from, To: to, Timeout: 15 * time.Second}
}

// Alert implements Alerter.
func (e *EmailAlerter) Alert(ctx context.Context, a Alert) error {
	if e.From == "" || len(e.To) == 0 {
		return fmt.Errorf("email alert: from and at least one to are required")
	}
	if e.Host == "" {
		return fmt.Errorf("email alert: smtp host is required")
	}
	timeout := e.Timeout
	if timeout <= 0 {
		timeout = 15 * time.Second
	}

	msg := e.buildMessage(a)

	done := make(chan error, 1)
	go func() { done <- e.send(msg) }()

	select {
	case <-ctx.Done():
		return fmt.Errorf("email alert: %w", ctx.Err())
	case err := <-done:
		return err
	}
}

func (e *EmailAlerter) subject(a Alert) string {
	prefix := e.SubjectPrefix
	if prefix == "" {
		prefix = "[agentids]"
	}
	worst := ""
	for _, ev := range a.Events {
		if severityRank(ev.Severity) > severityRank(worst) {
			worst = ev.Severity
		}
	}
	return fmt.Sprintf("%s %s drift on %s (%s)", prefix, strings.ToUpper(worst), a.Host.ID, a.Agent.Root)
}

// buildMessage renders the RFC 5322 message. The body is deliberately plain
// text: an on-call reader gets path + severity + the diff without an HTML
// client, and mail gateways do not mangle it.
func (e *EmailAlerter) buildMessage(a Alert) []byte {
	var b strings.Builder
	b.WriteString("From: " + e.From + "\r\n")
	b.WriteString("To: " + strings.Join(e.To, ", ") + "\r\n")
	b.WriteString("Subject: " + e.subject(a) + "\r\n")
	b.WriteString("Date: " + a.When.Format(time.RFC1123Z) + "\r\n")
	b.WriteString("MIME-Version: 1.0\r\n")
	b.WriteString("Content-Type: text/plain; charset=utf-8\r\n")
	b.WriteString("\r\n")
	b.WriteString(a.Summary() + "\r\n\r\n")
	b.WriteString(fmt.Sprintf("Detected: %s\r\n", a.When.Format(time.RFC3339)))
	b.WriteString(fmt.Sprintf("Host:     %s (%s)\r\n", a.Host.ID, a.Host.Name))
	if a.Agent.Root != "" {
		b.WriteString(fmt.Sprintf("Agent:    %s at %s\r\n", a.Agent.ID, a.Agent.Root))
	}
	b.WriteString("\r\n")
	for _, ev := range a.Events {
		e.writeEvent(&b, ev)
	}
	b.WriteString("--\r\nagentids — agent-layer intrusion detection\r\n")
	return []byte(b.String())
}

func (e *EmailAlerter) writeEvent(b *strings.Builder, ev schema.Event) {
	sev := ev.Severity
	if sev == "" {
		sev = schema.SeverityInfo
	}
	line := fmt.Sprintf("[%s] %s", strings.ToUpper(sev), ev.Event)
	if ev.File != nil {
		line += "  " + ev.File.Path
	}
	if ev.Rule != nil {
		line += "  rule=" + ev.Rule.ID
	}
	b.WriteString(line + "\r\n")
	if ev.File != nil {
		if ev.File.SHA256Before != "" || ev.File.SHA256After != "" {
			b.WriteString(fmt.Sprintf("    sha256: %s -> %s\r\n", shortHash(ev.File.SHA256Before), shortHash(ev.File.SHA256After)))
		}
		if ev.File.Diff != "" {
			b.WriteString("    diff:\r\n")
			for _, l := range strings.Split(strings.TrimRight(ev.File.Diff, "\n"), "\n") {
				b.WriteString("      " + l + "\r\n")
			}
		}
	}
	if ev.Actor != nil && (ev.Actor.User != "" || ev.Actor.UID != nil) {
		b.WriteString(fmt.Sprintf("    actor: user=%s uid=%s\r\n", ev.Actor.User, uidStr(ev.Actor.UID)))
	}
	b.WriteString("\r\n")
}

func shortHash(h string) string {
	if h == "" {
		return "-"
	}
	if len(h) > 12 {
		return h[:12]
	}
	return h
}

func uidStr(uid *int) string {
	if uid == nil {
		return "-"
	}
	return fmt.Sprint(*uid)
}

// send performs the SMTP conversation. Split out so tests can swap in a fake.
func (e *EmailAlerter) send(msg []byte) error {
	host, port, err := net.SplitHostPort(e.Host)
	if err != nil {
		return fmt.Errorf("email alert: bad smtp host %q: %w", e.Host, err)
	}

	// Implicit TLS on the SMTPS port; STARTTLS elsewhere.
	if port == "465" {
		return e.sendImplicitTLS(host, port, msg)
	}
	return e.sendStartTLS(host, e.Host, msg)
}

func (e *EmailAlerter) sendImplicitTLS(host, port string, msg []byte) error {
	conn, err := tls.Dial("tcp", net.JoinHostPort(host, port), &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12})
	if err != nil {
		return fmt.Errorf("email alert: dial: %w", err)
	}
	c, err := smtp.NewClient(conn, host)
	if err != nil {
		return fmt.Errorf("email alert: smtp handshake: %w", err)
	}
	defer c.Close()
	return e.converse(c, msg)
}

func (e *EmailAlerter) sendStartTLS(host, addr string, msg []byte) error {
	c, err := smtp.Dial(addr)
	if err != nil {
		return fmt.Errorf("email alert: dial: %w", err)
	}
	defer c.Close()

	if ok, _ := c.Extension("STARTTLS"); ok {
		if err := c.StartTLS(&tls.Config{ServerName: host, MinVersion: tls.VersionTLS12}); err != nil {
			return fmt.Errorf("email alert: starttls: %w", err)
		}
	} else if !e.AllowPlaintext {
		return fmt.Errorf("email alert: server does not offer STARTTLS and plaintext is not allowed")
	}
	return e.converse(c, msg)
}

func (e *EmailAlerter) converse(c *smtp.Client, msg []byte) error {
	if e.Username != "" {
		if ok, _ := c.Extension("AUTH"); !ok {
			return fmt.Errorf("email alert: server does not offer AUTH but a username was set")
		}
		if err := c.Auth(smtp.PlainAuth("", e.Username, e.Password, hostOf(e.Host))); err != nil {
			return fmt.Errorf("email alert: auth: %w", err)
		}
	}
	if err := c.Mail(e.From); err != nil {
		return fmt.Errorf("email alert: mail from: %w", err)
	}
	for _, to := range e.To {
		if err := c.Rcpt(to); err != nil {
			return fmt.Errorf("email alert: rcpt %s: %w", to, err)
		}
	}
	wc, err := c.Data()
	if err != nil {
		return fmt.Errorf("email alert: data: %w", err)
	}
	if _, err := wc.Write(msg); err != nil {
		return fmt.Errorf("email alert: write body: %w", err)
	}
	if err := wc.Close(); err != nil {
		return fmt.Errorf("email alert: close body: %w", err)
	}
	return c.Quit()
}

func hostOf(hostport string) string {
	if h, _, err := net.SplitHostPort(hostport); err == nil {
		return h
	}
	return hostport
}

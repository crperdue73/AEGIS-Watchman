package central

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/CRPerdue/agentids/internal/schema"
)

// Alert is the payload handed to an Alerter after drift is detected. One Alert
// carries every event from a single ingest cycle, so a noisy cycle produces one
// notification instead of N.
type Alert struct {
	When   time.Time      `json:"when"`
	Host   schema.Host    `json:"host"`
	Agent  schema.Agent   `json:"agent"`
	Events []schema.Event `json:"events"`
}

// Count returns the number of events in the alert.
func (a Alert) Count() int { return len(a.Events) }

// Summary renders a one-line human summary: "3 drifts on h-1:/root (max=high)".
func (a Alert) Summary() string {
	max := ""
	for _, e := range a.Events {
		if severityRank(e.Severity) > severityRank(max) {
			max = e.Severity
		}
	}
	where := a.Host.ID
	if a.Agent.Root != "" {
		where = a.Host.ID + ":" + a.Agent.Root
	}
	return fmt.Sprintf("%d drift(s) on %s (max=%s)", len(a.Events), where, max)
}

// Alerter delivers an Alert somewhere a human or system will see it. Implementations
// must be safe for concurrent use.
type Alerter interface {
	Alert(ctx context.Context, a Alert) error
}

// severityRank orders severities for "worst in this alert" comparison. Unknown
// severities rank lowest so an unrecognized value never masks a known high.
func severityRank(sev string) int {
	switch sev {
	case schema.SeverityCritical:
		return 5
	case schema.SeverityHigh:
		return 4
	case schema.SeverityMedium:
		return 3
	case schema.SeverityLow:
		return 2
	case schema.SeverityInfo:
		return 1
	default:
		return 0
	}
}

// WebhookAlerter POSTs a JSON Alert to URL. It is the Phase-1 default: any
// system that speaks HTTP (Slack, PagerDuty, a ticket bot, a shell receiver)
// can consume it with no agentids-specific client.
type WebhookAlerter struct {
	URL  string
	HTTP *http.Client
	// Log receives delivery failures. If nil, a package logger is used.
	Log *log.Logger
}

// NewWebhookAlerter returns a webhook alerter with a sane timeout.
func NewWebhookAlerter(url string) *WebhookAlerter {
	return &WebhookAlerter{URL: url, HTTP: &http.Client{Timeout: 10 * time.Second}}
}

// Alert implements Alerter.
func (w *WebhookAlerter) Alert(ctx context.Context, a Alert) error {
	body, err := json.Marshal(a)
	if err != nil {
		return fmt.Errorf("marshal alert: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, w.URL, strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("build alert request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := w.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("post alert: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("alert endpoint returned %s", resp.Status)
	}
	return nil
}

// LogAlerter writes a one-line summary per alert to a logger. Useful when no
// external system is configured yet, and as a second sink alongside a webhook.
type LogAlerter struct {
	Logger *log.Logger
}

// Alert implements Alerter.
func (l *LogAlerter) Alert(_ context.Context, a Alert) error {
	logger := l.Logger
	if logger == nil {
		logger = log.Default()
	}
	logger.Printf("ALERT: %s", a.Summary())
	return nil
}

// MultiAlerter fans an alert out to several Alerters. Every sink is attempted
// even if an earlier one fails, and the failures are joined into one error so
// one unreachable webhook cannot silently suppress a working sink.
type MultiAlerter struct {
	Alerters []Alerter
}

// Alert implements Alerter.
func (m *MultiAlerter) Alert(ctx context.Context, a Alert) error {
	var errs []string
	for _, al := range m.Alerters {
		if al == nil {
			continue
		}
		if err := al.Alert(ctx, a); err != nil {
			errs = append(errs, err.Error())
		}
	}
	if len(errs) > 0 {
		return fmt.Errorf("alert delivery: %s", strings.Join(errs, "; "))
	}
	return nil
}

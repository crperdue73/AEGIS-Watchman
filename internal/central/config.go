package central

import (
	"fmt"
	"os"
	"time"

	"github.com/CRPerdue/agentids/internal/mtls"
	"gopkg.in/yaml.v3"
)

// Config is the central service's on-disk configuration (central.yaml). It is
// intentionally small in Phase 1: where the baselines live, how much diff to
// carry, and where alerts go.
type Config struct {
	// Addr is the listen address, e.g. ":8080".
	Addr string `yaml:"addr"`
	// Store is the path to the durable baseline store file.
	Store string `yaml:"store"`
	// Records is the path to the durable event-record store file (the fleet
	// view's data). Empty -> records retained in memory only for the process
	// lifetime (the fleet view works, but resets on restart).
	Records string `yaml:"records"`
	// RecordBackend selects the record-store implementation: "file" (default,
	// atomic JSON, no extra dependency) or "sqlite" (requires the `-tags sqlite`
	// build for scale/concurrent readers). Unknown values fall back to "file"
	// with a log line rather than failing startup.
	RecordBackend string `yaml:"record_backend"`
	// RecordRetentionDays bounds the retained history (default 7).
	RecordRetentionDays int `yaml:"record_retention_days"`
	// RecordMax bounds the retained record count (default 10000).
	RecordMax int `yaml:"record_max"`
	// MaxDiffBytes caps the unified diff attached to each drift event.
	MaxDiffBytes int `yaml:"max_diff_bytes"`

	// TLS enables mutual TLS on the ingest listener. Absent -> plain HTTP
	// (loopback dev only).
	TLS mtls.Config `yaml:"tls,omitempty"`

	// Manifests configures Layer 2 provenance/attestation.
	Manifests Manifests `yaml:"manifests"`

	// UidMap points at a passwd-style file used to resolve numeric uids in
	// alerts to usernames. Empty disables resolution (uids stay numeric).
	UidMap string `yaml:"uid_map"`

	Alerts Alerts `yaml:"alerts"`
}

// Manifests configures Layer 2: where the signed approval manifests live and
// which Ed25519 keys may have signed them.
//
//	manifests:
//	  dir: /etc/agentids/manifests
//	  trust_keys: /etc/agentids/trust-keys.json   # {"ops-2026-q3": "<base64>"}
//	  require_approval: false                      # true -> unapproved agents are loud
//	  on_change: reload                            # reload manifests when the dir changes
//
// An empty dir disables Layer 2.
type Manifests struct {
	// Dir holds one *.json approval manifest per agent root.
	Dir string `yaml:"dir"`
	// TrustKeys is a path to a JSON file of {"name": "base64-ed25519-pubkey"}.
	TrustKeys string `yaml:"trust_keys"`
	// RequireApproval treats an agent with no manifest as fully untrusted.
	RequireApproval bool `yaml:"require_approval"`
}

// Alerts configures notification delivery for drift events.
type Alerts struct {
	// Webhook is an HTTP endpoint that receives a JSON Alert. Empty disables it.
	Webhook string `yaml:"webhook"`
	// Log writes a one-line summary per alert to the service log.
	Log bool `yaml:"log"`
	// Email, when set, delivers each alert as a plain-text SMTP message.
	Email Email `yaml:"email"`
}

// Email configures the SMTP sink. Absent/empty Host disables it.
type Email struct {
	// Host is "host:port". Port 465 uses implicit TLS; other ports use STARTTLS.
	Host     string   `yaml:"host"`
	Username string   `yaml:"username"`
	Password string   `yaml:"password"`
	From     string   `yaml:"from"`
	To       []string `yaml:"to"`
	// AllowPlaintext permits sending without STARTTLS (not recommended).
	AllowPlaintext bool `yaml:"allow_plaintext"`
}

// DefaultConfig returns the Phase-1 defaults, matching the built-in flag
// defaults so a missing config file and an empty config file behave the same.
func DefaultConfig() Config {
	return Config{
		Addr:         ":8080",
		Store:        "agentids-baselines.json",
		MaxDiffBytes: 4096,
		Alerts:       Alerts{Log: true},
	}
}

// LoadConfig reads central.yaml from path. A missing file returns the defaults
// with no error, so `agentids-central` runs out of the box.
func LoadConfig(path string) (Config, error) {
	cfg := DefaultConfig()

	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return cfg, nil
		}
		return cfg, fmt.Errorf("read central config: %w", err)
	}
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return cfg, fmt.Errorf("parse central config: %w", err)
	}
	if err := cfg.validate(); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func (c Config) validate() error {
	if c.Addr == "" {
		return fmt.Errorf("config: addr is empty")
	}
	if c.Store == "" {
		return fmt.Errorf("config: store is empty")
	}
	if c.MaxDiffBytes < 0 {
		return fmt.Errorf("config: max_diff_bytes must be >= 0")
	}
	if c.Alerts.Email.Host != "" {
		if c.Alerts.Email.From == "" {
			return fmt.Errorf("config: alerts.email.from is required when email.host is set")
		}
		if len(c.Alerts.Email.To) == 0 {
			return fmt.Errorf("config: alerts.email.to requires at least one recipient")
		}
	}
	if err := c.TLS.Validate(); err != nil {
		return err
	}
	if c.Manifests.Dir == "" && c.Manifests.TrustKeys != "" {
		return fmt.Errorf("config: manifests.trust_keys set but manifests.dir is empty")
	}
	return nil
}

// UidResolverFn builds the uid->name resolver described by the config, or a
// disabled resolver (nil-behaving) when no map is configured. A configured map
// that cannot be read is an error: the operator asked for names, so silently
// falling back to numerics would hide a real misconfiguration.
func (c Config) UidResolverFn() (*UidResolver, error) {
	if c.UidMap == "" {
		return NewUidResolver(nil), nil
	}
	m, err := LoadUidMapFromPasswd(c.UidMap)
	if err != nil {
		return nil, err
	}
	return NewUidResolver(m), nil
}

// ManifestRegistryFn builds the Layer 2 registry described by the config, or nil
// when Layer 2 is disabled. Load-time manifest/trust-key errors are returned so
// startup fails loudly on a bad approval list.
func (c Config) ManifestRegistry() (*ManifestRegistry, error) {
	if c.Manifests.Dir == "" {
		return nil, nil
	}
	keys, err := LoadTrustKeys(c.Manifests.TrustKeys)
	if err != nil {
		return nil, fmt.Errorf("config: load trust keys: %w", err)
	}
	r, err := NewManifestRegistry(c.Manifests.Dir, keys)
	if err != nil {
		return nil, err
	}
	r.requireApproval = c.Manifests.RequireApproval
	return r, nil
}

// TLSEnabled reports whether the ingest listener should serve HTTPS.
func (c Config) TLSEnabled() bool { return c.TLS.Enabled() }

// sqliteRecordStoreFn is set by init() in the -tags sqlite build. Nil means the
// SQLite backend is not compiled in. Kept as a function pointer so the default
// build never references modernc.org/sqlite and stays one-dependency.
var sqliteRecordStoreFn func(path string, retain RecordRetention) (RecordStore, error)

// RecordBackendOrDefault reports the record backend that will be used, naming
// the default so the startup log is unambiguous.
func (c Config) RecordBackendOrDefault() string {
	if c.RecordBackend == "" {
		return "file"
	}
	return c.RecordBackend
}

// RecordStoreFn builds the event-record store behind the fleet view. With a
// records path configured, it is durable (FileRecordStore by default, or the
// SQLite backend when record_backend: sqlite and the sqlite build is present);
// without one, an in-memory store is returned (the view works but resets on
// restart). It never returns nil — the fleet endpoint is always functional — so
// callers can wire it unconditionally.
func (c Config) RecordStoreFn() (RecordStore, error) {
	retain := RecordRetention{
		Window:     time.Duration(c.RecordRetentionDays) * 24 * time.Hour,
		MaxRecords: c.RecordMax,
	}
	if retain.Window <= 0 {
		retain.Window = DefaultRecordRetention.Window
	}
	if retain.MaxRecords <= 0 {
		retain.MaxRecords = DefaultRecordRetention.MaxRecords
	}
	if c.Records == "" {
		return NewMemoryRecordStore(retain), nil
	}
	if c.RecordBackend == "sqlite" {
		if sqliteRecordStoreFn == nil {
			return nil, fmt.Errorf("config: record_backend=sqlite requires a build with -tags sqlite")
		}
		return sqliteRecordStoreFn(c.Records, retain)
	}
	return NewFileRecordStore(c.Records, retain)
}

// Alerter builds the Alerter described by the config, or nil when no sink is
// enabled. Webhook, log, and email sinks compose via MultiAlerter.
func (c Config) Alerter(logger Logger) Alerter {
	var sinks []Alerter
	if c.Alerts.Webhook != "" {
		sinks = append(sinks, NewWebhookAlerter(c.Alerts.Webhook))
	}
	if e := c.Alerts.Email; e.Host != "" {
		em := NewEmailAlerter(e.Host, e.From, e.To)
		em.Username = e.Username
		em.Password = e.Password
		em.AllowPlaintext = e.AllowPlaintext
		sinks = append(sinks, em)
	}
	if c.Alerts.Log && logger != nil {
		sinks = append(sinks, &LogAlerter{Logger: logger})
	}
	switch len(sinks) {
	case 0:
		return nil
	case 1:
		return sinks[0]
	default:
		return &MultiAlerter{Alerters: sinks}
	}
}

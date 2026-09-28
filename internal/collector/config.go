package collector

import (
	"fmt"
	"os"
	"time"

	"github.com/CRPerdue/agentids/internal/mtls"
	"github.com/CRPerdue/agentids/internal/telemetry"
	"gopkg.in/yaml.v3"
)

// Config is the collector's on-host configuration. It mirrors the shape of
// examples/collector.yaml. One collector runs per host.
type Config struct {
	AgentRoots  []AgentRoot `yaml:"agent_roots"`
	Hash        string      `yaml:"hash"`
	Watch       Watch       `yaml:"watch"`
	Report      Report      `yaml:"report"`
	Attribution Attribution `yaml:"attribution"`
	// Behavior configures Layer 3 (action rules) on this host. Absent or with
	// no rules_dir -> Layer 3 is off and the collector runs Layers 1/2 only.
	Behavior Behavior `yaml:"behavior"`
}

// Behavior configures the Layer 3 rules engine on the collector.
type Behavior struct {
	// RulesDir is a directory of *.yaml rule packs, loaded sorted by name.
	RulesDir string `yaml:"rules_dir"`
	// AuditLog is the auditd log to tail for process.exec actions, e.g.
	// /var/log/audit/audit.log. Empty -> no telemetry source (rules loaded but
	// idle, useful for validating a pack without a live feed).
	AuditLog string `yaml:"audit_log"`
	// Spool is a file path where unmatched rule matches are durably queued
	// before shipping, so a central outage costs latency, not evidence. Empty
	// -> no spool (matches are shipped best-effort and dropped on failure).
	Spool string `yaml:"spool"`
	// SpoolMaxBytes bounds the spool file; oldest entries are dropped past it.
	// Zero -> DefaultSpoolMaxBytes (16 MiB).
	SpoolMaxBytes int64 `yaml:"spool_max_bytes"`
	// Cursor persists the audit-log read offset so a collector restart resumes
	// instead of replaying the whole log (which would re-fire every rule its
	// retained lines still match). Empty -> derived as <spool>.cursor when a
	// spool is set, else no persistence.
	Cursor string `yaml:"cursor"`
	// EBPF configures the optional kernel-side file.access adapter. It is
	// always additional to audit_log, never a replacement; a build without eBPF
	// support or a host that cannot load the object degrades to auditd-only.
	EBPF telemetry.EBPFConfig `yaml:"ebpf"`
}

// AgentRoot is one globbed agent workspace to inventory.
type AgentRoot struct {
	Path    string   `yaml:"path"`
	Include []string `yaml:"include"`
	Exclude []string `yaml:"exclude"`
}

// Watch controls how file changes are noticed.
type Watch struct {
	Mode         string        `yaml:"mode"` // "inotify" | "poll"
	PollInterval time.Duration `yaml:"poll_interval"`
}

// Report controls how inventories reach the central service.
type Report struct {
	Endpoint string        `yaml:"endpoint"`
	Interval time.Duration `yaml:"interval"`
	// TLS enables mutual TLS to the central service. Absent -> plain HTTP
	// (fine for a loopback dev setup, never for a real fleet).
	TLS mtls.Config `yaml:"tls,omitempty"`
}

// Attribution controls "who changed it" enrichment.
type Attribution struct {
	Source string `yaml:"source"` // "auditd" | "" (disabled)
}

// LoadConfig reads and validates a collector config from path.
func LoadConfig(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	var c Config
	if err := yaml.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	if err := c.validate(); err != nil {
		return nil, err
	}
	return &c, nil
}

func (c *Config) validate() error {
	if len(c.AgentRoots) == 0 {
		return fmt.Errorf("config: at least one agent_roots entry is required")
	}
	for i, r := range c.AgentRoots {
		if r.Path == "" {
			return fmt.Errorf("config: agent_roots[%d].path is empty", i)
		}
	}
	if c.Hash == "" {
		c.Hash = "sha256"
	}
	if c.Hash != "sha256" {
		return fmt.Errorf("config: unsupported hash %q (only sha256)", c.Hash)
	}
	if c.Watch.Mode == "" {
		c.Watch.Mode = "poll"
	}
	if c.Watch.Mode == "poll" && c.Watch.PollInterval <= 0 {
		c.Watch.PollInterval = 60 * time.Second
	}
	if c.Report.Interval <= 0 {
		c.Report.Interval = 30 * time.Second
	}
	if err := c.Report.TLS.Validate(); err != nil {
		return err
	}
	if c.Behavior.RulesDir != "" {
		if _, err := os.Stat(c.Behavior.RulesDir); err != nil {
			return fmt.Errorf("config: behavior.rules_dir %q: %w", c.Behavior.RulesDir, err)
		}
	}
	if err := c.Behavior.EBPF.Validate(); err != nil {
		return fmt.Errorf("config: behavior.%w", err)
	}
	return nil
}
